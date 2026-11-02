"""Everything the crawler reads and writes in Supabase, as the `sarkari_crawler` role.

That role can read sources and record their health, write documents, insert PENDING
drafts and crawl runs, and call two functions: publish_draft() and
close_expired_recruitments(). It cannot touch published rows directly. The schema and
those grants live in sarkari-web (Django migrations + dbsecurity/policy.py), whose tests
also run this module against a real database.

`conn` is any DB-API connection with %s parameters (psycopg 3 in production, Django's
connection in sarkari-web's tests). Production connections use autocommit, so each
statement commits on its own; publish_draft() is atomic by itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from sarkari_core.extractor import ExtractionResult
from sarkari_core.quality import Assessment
from sarkari_core.schemas import SCHEMA_VERSION

if TYPE_CHECKING:
    from .pipeline import RunStats

# This module needs only sarkari_core and a DB-API connection (no httpx, bs4 or PyMuPDF),
# so sarkari-web's tests can import it with the base install.

# ingest_ingestdocument.status values
DISCOVERED = "DISCOVERED"
FETCHED = "FETCHED"
EXTRACTED = "EXTRACTED"
IGNORED = "IGNORED"
DUPLICATE = "DUPLICATE"
FAILED = "FAILED"


@dataclass(frozen=True)
class SourceRow:
    id: int
    name: str
    listing_url: str
    parser_config: dict[str, Any]
    is_first_crawl: bool

    @property
    def verify_tls(self) -> bool:
        return self.parser_config.get("verify_tls", True) is not False

    @property
    def link_selector(self) -> Optional[str]:
        return self.parser_config.get("link_selector") or None


@dataclass(frozen=True)
class DocumentRow:
    id: int
    url: str
    classification: str
    attempts: int
    source_name: str
    parser_config: dict[str, Any] = field(default_factory=dict)
    link_text: str = ""

    @property
    def verify_tls(self) -> bool:
        return self.parser_config.get("verify_tls", True) is not False


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _config(value: Any) -> dict[str, Any]:
    if isinstance(value, str):  # Django's test connection returns jsonb as text
        value = json.loads(value)
    return value or {}


class Store:
    def __init__(self, conn):
        self._conn = conn

    def _one(self, sql: str, params: tuple = ()) -> Optional[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)

    # --- crawl runs ------------------------------------------------------------

    def start_run(self, trigger: str) -> int:
        return self._one("INSERT INTO ingest_crawlrun (trigger) VALUES (%s) RETURNING id", (trigger,))[0]

    def finish_run(self, run_id: int, stats: "RunStats") -> None:
        self._execute(
            "UPDATE ingest_crawlrun SET finished_at = now(), sources_checked = %s, sources_failed = %s, "
            "links_found = %s, documents_processed = %s, published = %s, corrigenda_applied = %s, "
            "duplicates = %s, failed = %s, tokens_used = %s, quota_exhausted = %s, errors = %s::jsonb "
            "WHERE id = %s",
            (
                stats.sources_checked,
                stats.sources_failed,
                stats.links_found,
                stats.documents_processed,
                stats.published,
                stats.corrigenda_applied,
                stats.duplicates,
                stats.failed,
                stats.tokens_used,
                stats.quota_exhausted,
                _json(stats.errors[:50]),
                run_id,
            ),
        )

    # --- sources ---------------------------------------------------------------

    def due_sources(self, limit: int) -> list[SourceRow]:
        rows = self._all(
            "SELECT id, name, listing_url, parser_config, last_success_at IS NULL FROM ingest_source "
            "WHERE is_active AND (last_checked_at IS NULL "
            "  OR last_checked_at + make_interval(mins => check_interval_minutes) <= now()) "
            "ORDER BY priority, last_checked_at NULLS FIRST, id LIMIT %s",
            (limit,),
        )
        return [SourceRow(r[0], r[1], r[2], _config(r[3]), r[4]) for r in rows]

    def record_source_success(self, source_id: int, link_count: int) -> None:
        self._execute(
            "UPDATE ingest_source SET last_checked_at = now(), last_success_at = now(), consecutive_failures = 0, "
            "last_error = '', last_link_count = %s WHERE id = %s",
            (link_count, source_id),
        )

    def record_source_failure(self, source_id: int, error: str) -> None:
        self._execute(
            "UPDATE ingest_source SET last_checked_at = now(), consecutive_failures = consecutive_failures + 1, "
            "last_error = %s WHERE id = %s",
            (error[:2000], source_id),
        )

    # --- documents -------------------------------------------------------------

    def add_document(
        self,
        source_id: int,
        url: str,
        url_normalized: str,
        link_text: str,
        classification: str,
        status: str = DISCOVERED,
        error: str = "",
    ) -> bool:
        """Insert a newly found link; False if it was already known."""
        classified_by = "" if classification == "UNCLASSIFIED" else "KEYWORD"
        row = self._one(
            "INSERT INTO ingest_ingestdocument (source_id, url, url_normalized, link_text, classification, "
            "classified_by, status, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (url_normalized) DO NOTHING RETURNING id",
            (source_id, url, url_normalized, link_text[:500], classification, classified_by, status, error),
        )
        return row is not None

    def pending_documents(self, limit: int) -> list[DocumentRow]:
        """Documents waiting for extraction: central sources first (lower priority value), newest first."""
        rows = self._all(
            "SELECT d.id, d.url, d.classification, d.attempts, s.name, s.parser_config, d.link_text "
            "FROM ingest_ingestdocument d JOIN ingest_source s ON s.id = d.source_id "
            "WHERE d.status IN ('DISCOVERED', 'FETCHED') AND d.classification <> 'NOISE' "
            "ORDER BY s.priority, d.discovered_at DESC, d.id DESC LIMIT %s",
            (limit,),
        )
        return [DocumentRow(r[0], r[1], r[2], r[3], r[4], _config(r[5]), r[6]) for r in rows]

    def find_duplicate(self, sha256: str, document_id: int) -> Optional[int]:
        """An earlier document with the same file, e.g. the same PDF under a new URL."""
        row = self._one(
            "SELECT id FROM ingest_ingestdocument WHERE content_sha256 = %s AND id < %s AND status <> 'DUPLICATE' "
            "ORDER BY id LIMIT 1",
            (sha256, document_id),
        )
        return row[0] if row else None

    def mark_fetched(
        self, document_id: int, sha256: str, size: int, content_type: str, classification: str
    ) -> None:
        self._execute(
            "UPDATE ingest_ingestdocument SET status = 'FETCHED', fetched_at = now(), content_sha256 = %s, "
            "size_bytes = %s, content_type = %s, classification = %s, "
            "classified_by = CASE WHEN classified_by = '' AND %s <> 'UNCLASSIFIED' THEN 'KEYWORD' ELSE classified_by END, "
            "error = '' WHERE id = %s",
            (sha256, size, content_type[:100], classification, classification, document_id),
        )

    def set_document_status(
        self,
        document_id: int,
        status: str,
        error: str = "",
        classification: Optional[str] = None,
        duplicate_of: Optional[int] = None,
    ) -> None:
        self._execute(
            "UPDATE ingest_ingestdocument SET status = %s, error = %s, "
            "classification = COALESCE(%s, classification), duplicate_of_id = COALESCE(%s, duplicate_of_id) "
            "WHERE id = %s",
            (status, error[:2000], classification, duplicate_of, document_id),
        )

    def record_attempt_failure(self, document_id: int, error: str, max_attempts: int) -> str:
        """Count a failed fetch or provider error; give up after `max_attempts`. Returns the new status."""
        row = self._one(
            "UPDATE ingest_ingestdocument SET attempts = attempts + 1, error = %s, "
            "status = CASE WHEN attempts + 1 >= %s THEN 'FAILED' ELSE status END "
            "WHERE id = %s RETURNING status",
            (error[:2000], max_attempts, document_id),
        )
        return row[0]

    # --- drafts and publishing ---------------------------------------------------

    def save_draft(self, document_id: int, result: ExtractionResult, assessment: Assessment) -> int:
        payload = result.draft.model_dump(mode="json") if result.draft is not None else None
        confidence = assessment.confidence.value if assessment.confidence else ""
        return self._one(
            "INSERT INTO ingest_extractiondraft (document_id, status, payload, schema_version, extractor, model, "
            "prompt_version, confidence, flagged_fields, low_confidence_fields, rule_flags, errors, raw_outputs, "
            "failure_reason, tokens_used) "
            "VALUES (%s, 'PENDING', %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, "
            "%s::jsonb, %s, %s) RETURNING id",
            (
                document_id,
                _json(payload) if payload is not None else None,
                payload["schema_version"] if payload else SCHEMA_VERSION,
                result.extractor[:40],
                result.model[:200],
                result.prompt_version[:20],
                confidence,
                _json(assessment.flagged_fields),
                _json(result.draft.low_confidence_fields if result.draft else []),
                _json([f.as_dict() for f in assessment.flags]),
                _json(result.errors),
                _json(result.raw_outputs),
                assessment.failure_reason[:1000],
                result.tokens_used,
            ),
        )[0]

    def publish(self, draft_id: int) -> str:
        """Run the database's publish gate. Returns the draft's new status."""
        return self._one("SELECT publish_draft(%s)", (draft_id,))[0]

    def close_expired(self) -> int:
        return self._one("SELECT close_expired_recruitments()")[0]

    # --- reporting ---------------------------------------------------------------

    def summary(self, hours: int) -> dict[str, Any]:
        runs = self._one(
            "SELECT count(*), coalesce(sum(sources_checked), 0), coalesce(sum(sources_failed), 0), "
            "coalesce(sum(links_found), 0), coalesce(sum(published), 0), coalesce(sum(corrigenda_applied), 0), "
            "coalesce(sum(failed), 0), coalesce(sum(tokens_used), 0), coalesce(bool_or(quota_exhausted), false) "
            "FROM ingest_crawlrun WHERE started_at > now() - make_interval(hours => %s)",
            (hours,),
        )
        failing = self._all(
            "SELECT name, consecutive_failures, left(last_error, 200) FROM ingest_source "
            "WHERE is_active AND consecutive_failures >= 3 ORDER BY consecutive_failures DESC, name LIMIT 20"
        )
        silent = self._all(
            "SELECT name FROM ingest_source WHERE is_active AND last_success_at IS NOT NULL AND last_link_count = 0 "
            "ORDER BY name LIMIT 20"
        )
        backlog = self._one(
            "SELECT count(*) FROM ingest_ingestdocument WHERE status IN ('DISCOVERED', 'FETCHED') "
            "AND classification <> 'NOISE'"
        )[0]
        keys = ("runs", "sources_checked", "sources_failed", "links_found", "published", "corrigenda_applied",
                "failed", "tokens_used", "quota_exhausted")
        return {
            **dict(zip(keys, runs)),
            "failing_sources": [{"name": n, "failures": f, "error": e} for n, f, e in failing],
            "sources_without_links": [n for (n,) in silent],
            "backlog": backlog,
        }
