"""The crawler run end to end, with mocked websites, a stub LLM and an in-memory store.
The real Store and publish_draft() are tested against Postgres in sarkari-web
(admin/tests/test_publish.py)."""

from dataclasses import dataclass, field

import httpx
import pytest

from sarkari_core.extractor import ExtractionError, ExtractionResult, QuotaExhausted
from sarkari_core.schemas import RecruitmentDraft
from sarkari_crawler.fetch import Fetcher
from sarkari_crawler.pipeline import FIRST_CRAWL_TAKE, Pipeline
from sarkari_crawler.store import DocumentRow, SourceRow
from tests.conftest import make_pdf

NOTICE_TEXT = (
    "Staff Selection Commission\nNotice: Recruitment of Constables 2026\nAdvt. No. 05/2026\n"
    "Applications are invited for 500 vacancies. Important dates: last date 04-07-2026."
)
RESULT_TEXT = "Result of Combined Graduate Level Examination 2025\nList of selected candidates"


@dataclass
class Doc:
    id: int
    source_id: int
    url: str
    url_normalized: str
    text: str
    classification: str
    status: str
    error: str = ""
    attempts: int = 0
    sha256: str = ""
    duplicate_of: int | None = None


@dataclass
class FakeStore:
    sources: list[SourceRow] = field(default_factory=list)
    docs: dict[int, Doc] = field(default_factory=dict)
    drafts: list[tuple] = field(default_factory=list)
    health: dict[int, str] = field(default_factory=dict)
    runs: list = field(default_factory=list)
    closed: int = 0

    def start_run(self, trigger):
        self.runs.append({"trigger": trigger})
        return len(self.runs)

    def finish_run(self, run_id, stats):
        self.runs[run_id - 1]["stats"] = stats

    def due_sources(self, limit):
        return self.sources[:limit]

    def record_source_success(self, source_id, link_count):
        self.health[source_id] = f"ok:{link_count}"

    def record_source_failure(self, source_id, error):
        self.health[source_id] = f"failed:{error}"

    def add_document(self, source_id, url, url_normalized, link_text, classification, status="DISCOVERED", error=""):
        if any(d.url_normalized == url_normalized for d in self.docs.values()):
            return False
        doc_id = len(self.docs) + 1
        self.docs[doc_id] = Doc(doc_id, source_id, url, url_normalized, link_text, classification, status, error)
        return True

    def pending_documents(self, limit):
        pending = [d for d in self.docs.values() if d.status in ("DISCOVERED", "FETCHED") and d.classification != "NOISE"]
        return [DocumentRow(d.id, d.url, d.classification, d.attempts, "SSC notices") for d in pending][:limit]

    def find_duplicate(self, sha256, document_id):
        return next((d.id for d in self.docs.values() if d.sha256 == sha256 and d.id < document_id), None)

    def mark_fetched(self, document_id, sha256, size, content_type, classification):
        doc = self.docs[document_id]
        doc.status, doc.sha256, doc.classification = "FETCHED", sha256, classification

    def set_document_status(self, document_id, status, error="", classification=None, duplicate_of=None):
        doc = self.docs[document_id]
        doc.status, doc.error = status, error
        doc.classification = classification or doc.classification
        doc.duplicate_of = duplicate_of

    def record_attempt_failure(self, document_id, error, max_attempts):
        doc = self.docs[document_id]
        doc.attempts += 1
        doc.error = error
        if doc.attempts >= max_attempts:
            doc.status = "FAILED"
        return doc.status

    def save_draft(self, document_id, result, assessment):
        self.drafts.append((document_id, result, assessment))
        return len(self.drafts)

    def publish(self, draft_id):
        return "PUBLISHED" if self.drafts[draft_id - 1][2].publishable else "FAILED"

    def close_expired(self):
        return self.closed


class StubExtractor:
    name = model = "stub"

    def __init__(self, sample_payload, outcomes=None):
        self.payload = sample_payload
        self.outcomes = list(outcomes or [])
        self.documents = []

    def extract(self, document):
        self.documents.append(document)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        draft = RecruitmentDraft.model_validate(self.payload)
        return ExtractionResult("groq", "openai/gpt-oss-120b", "v2", draft=draft, tokens_used=5000)


def site(pages: dict[str, httpx.Response]) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return pages.get(str(request.url), httpx.Response(404))

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return Fetcher(client=client, sleep=lambda s: None, min_host_delay=0, respect_robots=False)


def pdf(text: str) -> httpx.Response:
    return httpx.Response(200, content=make_pdf([text]), headers={"content-type": "application/pdf"})


def listing(*links: tuple[str, str]) -> httpx.Response:
    rows = "".join(f'<tr><td><a href="{href}">{label}</a></td></tr>' for href, label in links)
    return httpx.Response(200, text=f"<table>{rows}</table>", headers={"content-type": "text/html"})


SOURCE = SourceRow(1, "SSC notices", "https://ssc.gov.in/notices", {}, is_first_crawl=False)


def test_full_run_publishes_a_new_notice_and_skips_noise(sample_payload):
    store = FakeStore(sources=[SOURCE])
    pages = {
        "https://ssc.gov.in/notices": listing(
            ("/files/advt-05-2026.pdf", "Recruitment of Constables 2026 – Advertisement"),
            ("/files/result.pdf", "Final result of CGL 2025"),
        ),
        "https://ssc.gov.in/files/advt-05-2026.pdf": pdf(NOTICE_TEXT),
    }
    extractor = StubExtractor(sample_payload)
    stats = Pipeline(store, site(pages), extractor, ocr=False, log_line=lambda s: None).run(10, 10)

    assert (stats.sources_checked, stats.links_found, stats.documents_processed, stats.published) == (1, 2, 1, 1)
    assert stats.tokens_used == 5000
    statuses = {d.url.rsplit("/", 1)[-1]: d.status for d in store.docs.values()}
    assert statuses == {"advt-05-2026.pdf": "EXTRACTED", "result.pdf": "IGNORED"}
    assert store.health[1] == "ok:2"
    assert "Recruitment of Constables" in extractor.documents[0].header_pages[0].text
    assert store.runs[0]["stats"] is stats


def test_first_crawl_queues_only_the_newest_links(sample_payload):
    links = [(f"/files/advt-{i}.pdf", f"Advertisement {i} for recruitment of clerks") for i in range(8)]
    store = FakeStore(sources=[SourceRow(1, "new source", "https://ssc.gov.in/notices", {}, is_first_crawl=True)])
    Pipeline(store, site({"https://ssc.gov.in/notices": listing(*links)}), None).crawl(10, stats := _stats())
    statuses = [d.status for d in store.docs.values()]
    assert statuses.count("DISCOVERED") == FIRST_CRAWL_TAKE
    assert statuses.count("IGNORED") == 8 - FIRST_CRAWL_TAKE
    assert stats.links_found == 8


def test_source_failure_is_recorded_and_the_run_goes_on():
    other = SourceRow(2, "other", "https://other.gov.in/", {}, is_first_crawl=False)
    store = FakeStore(sources=[SOURCE, other])
    pages = {"https://other.gov.in/": listing(("/a.pdf", "Notice"))}
    stats = _stats()
    Pipeline(store, site(pages), None).crawl(10, stats)
    assert store.health == {1: "failed:https://ssc.gov.in/notices: HTTP 404", 2: "ok:1"}
    assert (stats.sources_checked, stats.sources_failed) == (2, 1)


def test_quota_exhaustion_leaves_the_queue_for_the_next_run(sample_payload):
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/a.pdf", "a", "Advertisement A recruitment", "NOTIFICATION")
    store.add_document(1, "https://ssc.gov.in/b.pdf", "b", "Advertisement B recruitment", "NOTIFICATION")
    pages = {"https://ssc.gov.in/a.pdf": pdf(NOTICE_TEXT), "https://ssc.gov.in/b.pdf": pdf(NOTICE_TEXT + " B")}
    extractor = StubExtractor(sample_payload, [QuotaExhausted("groq daily quota used up")])
    stats = _stats()
    Pipeline(store, site(pages), extractor, ocr=False, log_line=lambda s: None).process(10, stats)

    assert stats.quota_exhausted and stats.documents_processed == 0
    assert [d.status for d in store.docs.values()] == ["FETCHED", "DISCOVERED"]  # both wait; nothing counted as failed
    assert all(d.attempts == 0 for d in store.docs.values())
    assert len(extractor.documents) == 1  # stopped at the first refusal


def test_provider_error_counts_an_attempt(sample_payload):
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/a.pdf", "a", "Advertisement recruitment", "NOTIFICATION")
    extractor = StubExtractor(sample_payload, [ExtractionError("HTTP 500")])
    Pipeline(store, site({"https://ssc.gov.in/a.pdf": pdf(NOTICE_TEXT)}), extractor, ocr=False).process(10, _stats())
    doc = store.docs[1]
    assert (doc.status, doc.attempts, doc.error) == ("FETCHED", 1, "HTTP 500")


def test_same_file_under_a_new_url_is_a_duplicate(sample_payload):
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/a.pdf", "a", "Advertisement recruitment", "NOTIFICATION")
    store.add_document(1, "https://ssc.gov.in/a-copy.pdf", "a-copy", "Advertisement recruitment (copy)", "NOTIFICATION")
    same = pdf(NOTICE_TEXT)
    pages = {"https://ssc.gov.in/a.pdf": same, "https://ssc.gov.in/a-copy.pdf": pdf(NOTICE_TEXT)}
    pages["https://ssc.gov.in/a-copy.pdf"] = httpx.Response(200, content=same.content, headers=same.headers)
    extractor = StubExtractor(sample_payload)
    stats = _stats()
    Pipeline(store, site(pages), extractor, ocr=False, log_line=lambda s: None).process(10, stats)
    assert (store.docs[2].status, store.docs[2].duplicate_of) == ("DUPLICATE", 1)
    assert len(extractor.documents) == 1 and stats.duplicates == 1


def test_unlabelled_pdf_is_classified_by_its_text_before_any_llm_call(sample_payload):
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/1.pdf", "1", "Download", "UNCLASSIFIED")
    store.add_document(1, "https://ssc.gov.in/2.pdf", "2", "Download", "UNCLASSIFIED")
    pages = {"https://ssc.gov.in/1.pdf": pdf(RESULT_TEXT), "https://ssc.gov.in/2.pdf": pdf(NOTICE_TEXT)}
    extractor = StubExtractor(sample_payload)
    Pipeline(store, site(pages), extractor, ocr=False, log_line=lambda s: None).process(10, _stats())
    assert (store.docs[1].status, store.docs[1].classification) == ("IGNORED", "NOISE")
    assert (store.docs[2].status, store.docs[2].classification) == ("EXTRACTED", "NOTIFICATION")
    assert len(extractor.documents) == 1


def test_corrupt_pdf_fails_without_retrying(sample_payload):
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/a.pdf", "a", "Advertisement recruitment", "NOTIFICATION")
    broken = httpx.Response(200, content=b"%PDF-1.7 truncated garbage", headers={"content-type": "application/pdf"})
    Pipeline(store, site({"https://ssc.gov.in/a.pdf": broken}), StubExtractor(sample_payload), ocr=False).process(10, _stats())
    assert store.docs[1].status == "FAILED" and "could not read" in store.docs[1].error


def test_html_notice_is_extracted_from_its_text(sample_payload):
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/notice/7", "n7", "Recruitment of 120 Junior Assistants", "NOTIFICATION")
    page = httpx.Response(
        200, text=f"<html><body><nav>Menu</nav><main><p>{NOTICE_TEXT}</p></main></body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )
    extractor = StubExtractor(sample_payload)
    Pipeline(store, site({"https://ssc.gov.in/notice/7": page}), extractor, log_line=lambda s: None).process(10, _stats())
    assert store.docs[1].status == "EXTRACTED"
    assert "Advt. No. 05/2026" in extractor.documents[0].header_pages[0].text


def test_blocked_draft_is_saved_but_not_published(sample_payload):
    sample_payload["application_end_date"] = None
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/a.pdf", "a", "Advertisement recruitment", "NOTIFICATION")
    stats = _stats()
    Pipeline(store, site({"https://ssc.gov.in/a.pdf": pdf(NOTICE_TEXT)}), StubExtractor(sample_payload), ocr=False,
             log_line=lambda s: None).process(10, stats)
    (_, _, assessment), = store.drafts
    assert not assessment.publishable and "Last date" in assessment.failure_reason
    assert (stats.published, stats.failed) == (0, 1)


def _stats():
    from sarkari_crawler.pipeline import RunStats

    return RunStats()


def test_link_label_alone_never_spends_llm_quota(sample_payload):
    """A page that merely mentions vacancies in its link label is checked by its own text first."""
    store = FakeStore()
    store.add_document(1, "https://esic.gov.in/dashboard", "d", "Bed occupancy and vacancy status page", "NOTIFICATION")
    page = httpx.Response(200, text="<main>Hospital beds available today: 120. Updated hourly.</main>",
                          headers={"content-type": "text/html"})
    extractor = StubExtractor(sample_payload)
    Pipeline(store, site({"https://esic.gov.in/dashboard": page}), extractor).process(10, _stats())
    assert store.docs[1].status == "IGNORED" and extractor.documents == []


def test_first_crawl_prefers_notice_like_links():
    menu = [(f"/menu/{i}.pdf", "Download") for i in range(6)]
    notices = [(f"/files/advt-{i}.pdf", f"Advertisement {i} for recruitment of clerks") for i in range(2)]
    store = FakeStore(sources=[SourceRow(1, "home page", "https://kvs.gov.in/", {}, is_first_crawl=True)])
    Pipeline(store, site({"https://kvs.gov.in/": listing(*menu, *notices)}), None).crawl(10, _stats())
    queued = [d.url for d in store.docs.values() if d.status == "DISCOVERED"]
    assert queued[:2] == ["https://kvs.gov.in/files/advt-0.pdf", "https://kvs.gov.in/files/advt-1.pdf"]
    assert len(queued) == FIRST_CRAWL_TAKE


def test_scanned_pdf_without_ocr_is_reported_as_unreadable(sample_payload):
    store = FakeStore()
    store.add_document(1, "https://ssc.gov.in/scan.pdf", "s", "Advertisement recruitment", "NOTIFICATION")
    blank = httpx.Response(200, content=make_pdf([""]), headers={"content-type": "application/pdf"})
    extractor = StubExtractor(sample_payload)
    Pipeline(store, site({"https://ssc.gov.in/scan.pdf": blank}), extractor, ocr=False).process(10, _stats())
    assert store.docs[1].status == "FAILED" and "OCR" in store.docs[1].error
    assert extractor.documents == []
