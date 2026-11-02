"""One crawler run: crawl due sources, extract queued documents, publish, close expired.

    sources ──crawl──▶ documents (DISCOVERED) ──download──▶ FETCHED ──LLM──▶ draft ──publish_draft()──▶ catalog

Every step is safe to re-run: links are unique by normalised URL, files by content hash,
and publish_draft() ignores drafts it has already handled. When the free LLM quota runs
out, the remaining documents simply wait for the next run.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from sarkari_core.extractor import DocumentText, ExtractionError, QuotaExhausted
from sarkari_core.quality import assess

from . import store as st
from .classify import NOISE, UNCLASSIFIED, classify_text
from .discover import extract_links, html_to_text
from .fetch import Fetched, FetchError, Fetcher
from .pdf import prepare_document, prepare_text

log = logging.getLogger(__name__)

# On a source's first crawl, only the first few links (listing pages put the newest
# first) are queued; the rest are recorded as already seen, so a new source doesn't
# spend days of free LLM quota on old notices.
FIRST_CRAWL_TAKE = 5
MAX_ATTEMPTS = 3  # downloads or provider errors before a document is marked FAILED

OUTCOME_COUNTERS = {
    "PUBLISHED": "published",
    "CORRIGENDUM_APPLIED": "corrigenda_applied",
    "DUPLICATE": "duplicates",
    "UNMATCHED": "failed",
    "FAILED": "failed",
}


@dataclass
class RunStats:
    sources_checked: int = 0
    sources_failed: int = 0
    links_found: int = 0
    documents_processed: int = 0
    published: int = 0
    corrigenda_applied: int = 0
    duplicates: int = 0
    failed: int = 0
    tokens_used: int = 0
    quota_exhausted: bool = False
    closed: int = 0
    errors: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        store: st.Store,
        fetcher: Fetcher,
        extractor,
        ocr: Optional[bool] = None,
        log_line: Callable[[str], None] = log.info,
    ):
        self.store = store
        self.fetcher = fetcher
        self.extractor = extractor
        self.ocr = ocr
        self.log = log_line

    def run(self, max_sources: int, max_documents: int, trigger: str = "schedule") -> RunStats:
        stats = RunStats()
        run_id = self.store.start_run(trigger)
        try:
            self.crawl(max_sources, stats)
            self.process(max_documents, stats)
            stats.closed = self.store.close_expired()
        except Exception as exc:  # record what happened, then fail the job
            stats.errors.append(f"run aborted: {exc!r}")
            raise
        finally:
            self.store.finish_run(run_id, stats)
        return stats

    # --- crawl -----------------------------------------------------------------

    def crawl(self, max_sources: int, stats: RunStats) -> None:
        for source in self.store.due_sources(max_sources):
            stats.sources_checked += 1
            try:
                page = self.fetcher.get(source.listing_url, verify_tls=source.verify_tls)
                links = extract_links(page.content, page.url, source.link_selector)
            except Exception as exc:  # FetchError, or a bad link_selector in parser_config
                stats.sources_failed += 1
                stats.errors.append(f"{source.name}: {exc}")
                self.store.record_source_failure(source.id, str(exc))
                continue

            if source.is_first_crawl:
                # Home pages list menu links before notices; prefer links that look like notices.
                links = sorted(links, key=lambda link: link.classification == UNCLASSIFIED)
            new = 0
            queued = 0
            for link in links:
                status, error = st.DISCOVERED, ""
                if link.classification == NOISE:
                    status, error = st.IGNORED, "noise (keyword rules)"
                elif source.is_first_crawl and queued >= FIRST_CRAWL_TAKE:
                    status, error = st.IGNORED, "already listed at the source's first crawl"
                else:
                    queued += 1
                if self.store.add_document(
                    source.id, link.url, link.url_normalized, link.text, link.classification, status, error
                ):
                    new += 1
            stats.links_found += new
            self.store.record_source_success(source.id, len(links))
            self.log(f"{source.name}: {len(links)} candidate links, {new} new")

    # --- process ---------------------------------------------------------------

    def process(self, max_documents: int, stats: RunStats) -> None:
        for document in self.store.pending_documents(max_documents):
            try:
                self._process_one(document, stats)
            except QuotaExhausted as exc:
                stats.quota_exhausted = True
                stats.errors.append(str(exc)[:300])
                self.log(f"LLM quota used up; {document.url} and the rest wait for the next run")
                return
            except Exception as exc:
                # One bad document must not block the queue forever: count the attempt and move on.
                log.exception("processing %s failed", document.url)
                status = self.store.record_attempt_failure(document.id, f"unexpected error: {exc!r}", MAX_ATTEMPTS)
                stats.errors.append(f"{document.url}: unexpected error ({status}): {exc!r}"[:300])

    def _process_one(self, document: st.DocumentRow, stats: RunStats) -> None:
        try:
            fetched = self.fetcher.get(document.url, verify_tls=document.verify_tls)
        except FetchError as exc:
            status = self.store.record_attempt_failure(document.id, str(exc), MAX_ATTEMPTS)
            stats.errors.append(f"download failed ({status}): {exc}"[:300])
            return

        if not (fetched.is_pdf or fetched.is_html):
            self.store.set_document_status(document.id, st.IGNORED, f"unsupported content type {fetched.content_type!r}")
            return

        sha256 = hashlib.sha256(fetched.content).hexdigest()
        duplicate_of = self.store.find_duplicate(sha256, document.id)
        if duplicate_of is not None:
            self.store.set_document_status(document.id, st.DUPLICATE, duplicate_of=duplicate_of)
            stats.duplicates += 1
            return

        try:
            text = self._prepare(fetched, sha256)
        except Exception as exc:  # a corrupt or encrypted PDF won't get better on retry
            self.store.set_document_status(document.id, st.FAILED, f"could not read the file: {exc}")
            stats.failed += 1
            return
        # The free LLM quota is the scarce resource: every file must read like a notice
        # (by its own text, not just its link label) before it is spent.
        all_text = "\n".join(p.text for p in text.header_pages + text.posts_pages)
        if not all_text.strip():
            self.store.set_document_status(
                document.id, st.FAILED, "no readable text: a scanned file and OCR (Tesseract) isn't installed"
            )
            stats.failed += 1
            return
        classification = classify_text(all_text)
        if classification in (NOISE, UNCLASSIFIED):
            self.store.set_document_status(
                document.id, st.IGNORED, "doesn't read like a recruitment notice (keyword rules)",
                classification=NOISE if classification == NOISE else document.classification,
            )
            return

        # The file itself is never kept: only its link, hash and size. The text goes to the LLM
        # from memory and the published recruitment links to the official URL.
        self.store.mark_fetched(document.id, sha256, len(fetched.content), fetched.content_type, classification)

        # Sources are named "<organisation> – <page>", e.g. "ESIC – recruitments".
        organisation = document.source_name.split(" – ")[0].strip()
        text = dataclasses.replace(text, title_hint=document.link_text[:300], organisation_hint=organisation)
        try:
            result = self.extractor.extract(text)
        except QuotaExhausted:
            raise
        except ExtractionError as exc:
            status = self.store.record_attempt_failure(document.id, str(exc), MAX_ATTEMPTS)
            stats.errors.append(f"extraction failed ({status}): {exc}"[:300])
            return

        stats.documents_processed += 1
        stats.tokens_used += result.tokens_used
        assessment = assess(result.draft, result.errors)
        draft_id = self.store.save_draft(document.id, result, assessment)
        outcome = self.store.publish(draft_id)
        self.store.set_document_status(document.id, st.EXTRACTED)
        counter = OUTCOME_COUNTERS.get(outcome)
        if counter:
            setattr(stats, counter, getattr(stats, counter) + 1)
        title = result.draft.title if result.draft else document.url
        self.log(f"{document.source_name}: {title[:80]} → {outcome}")

    def _prepare(self, fetched: Fetched, sha256: str) -> DocumentText:
        if fetched.is_pdf:
            return prepare_document(fetched.content, source_url=fetched.url, ocr=self.ocr)
        return prepare_text(html_to_text(fetched.content), sha256, source_url=fetched.url)
