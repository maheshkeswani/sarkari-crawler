"""Extraction through OpenAI-compatible chat APIs (Groq, OpenRouter).

Two steps per document: the header fields, then the posts table. Each step is
validated separately, so a failed posts step still leaves a usable header, which is
published as a header-only notice (see sarkari_core.quality).

Built for GPT-OSS-120B on free tiers: reasoning effort is kept low (reasoning tokens
count against the reply cap and the per-minute quota), each step has its own reply
cap, and a daily-quota 429 stops the run instead of retrying.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
from pydantic import ValidationError

from sarkari_core.enums import DocumentKind
from sarkari_core.extractor import DocumentText, ExtractionError, ExtractionResult, QuotaExhausted
from sarkari_core.prompts import PROMPT_VERSION, render
from sarkari_core.schemas import PostsDraft, RecruitmentDraft, RecruitmentHeaderDraft

from ..pdf import join_pages

log = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are a careful data extraction system. You reply with a single valid JSON object only."
MAX_BACKOFF_SECONDS = 60.0
# A 429 asking us to wait longer than this is a daily limit, not a per-minute one.
DAILY_LIMIT_WAIT_SECONDS = 600.0
# Reply caps per step, including reasoning tokens. See pdf.HEADER_TOKEN_BUDGET.
MAX_TOKENS = {"header": 2_500, "posts": 3_500}

_DAILY_LIMIT_TEXT = re.compile(r"per[ _-]?day|\bTPD\b|\bRPD\b|daily", re.IGNORECASE)


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key_env: str
    # Provider-specific request fields, e.g. how to ask for low reasoning effort.
    extra_body: dict[str, Any] = field(default_factory=dict)


PROVIDERS = {
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", {"reasoning_effort": "low"}),
    "openrouter": Provider(
        "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", {"reasoning": {"effort": "low"}}
    ),
}


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse the model's reply, tolerating code fences and text around the object."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in model output")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output is not a JSON object")
    return value


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10])
    return str(exc)


class ChatExtractor:
    def __init__(
        self,
        provider: Provider,
        model: str,
        api_key: str,
        client: Optional[httpx.Client] = None,
        max_attempts: int = 6,
        max_tokens: Optional[dict[str, int]] = None,
        prompt_version: str = PROMPT_VERSION,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = provider
        self.model = model
        self.name = f"{provider.name}:{model}"
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
        self._max_attempts = max_attempts
        self._max_tokens = {**MAX_TOKENS, **(max_tokens or {})}
        self._prompt_version = prompt_version
        self._sleep = sleep

    def extract(self, document: DocumentText) -> ExtractionResult:
        result = ExtractionResult(extractor=self.provider.name, model=self.model, prompt_version=self._prompt_version)
        if not document.header_pages:
            result.errors.append("no text found in the document (scanned without OCR?)")
            return result

        header = self._run_step(
            result, "header", join_pages(document.header_pages), RecruitmentHeaderDraft,
            document.title_hint, document.organisation_hint,
        )
        if header is None:
            return result

        posts = PostsDraft()
        if header.document_kind == DocumentKind.NOTIFICATION:
            posts_text = join_pages(document.posts_pages or document.header_pages)
            posts = self._run_step(result, "posts", posts_text, PostsDraft) or PostsDraft()

        result.draft = RecruitmentDraft.combine(header, posts)
        return result

    def _run_step(
        self, result: ExtractionResult, step: str, text: str, schema: type, title_hint: str = "", organisation_hint: str = ""
    ):
        reply, finish_reason, tokens = self._complete(render(step, text, self._prompt_version), self._max_tokens[step])
        result.raw_outputs.append(reply)
        result.tokens_used += tokens
        try:
            data = parse_json_object(reply)
            if organisation_hint and not str(data.get("organisation_name") or "").strip():
                data["organisation_name"] = organisation_hint  # known from the source
            if title_hint and not str(data.get("title") or "").strip():
                # Many notices print no title; the listing page's label is the next best thing.
                data["title"] = title_hint
                unsure = data.get("low_confidence_fields")
                data["low_confidence_fields"] = list(dict.fromkeys((unsure if isinstance(unsure, list) else []) + ["title"]))
            return schema.model_validate(data)
        except (ValueError, ValidationError) as exc:
            hint = " (the reply hit the token limit)" if finish_reason == "length" else ""
            result.errors.append(f"{step}: {_short_error(exc)}{hint}")
            return None

    def _complete(self, prompt: str, max_tokens: int) -> tuple[str, Optional[str], int]:
        """Return (reply text, finish reason, tokens used)."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            **self.provider.extra_body,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self.provider.base_url}/chat/completions"
        last_problem = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(url, json=body, headers=headers)
            except httpx.TransportError as exc:
                last_problem = f"network error: {exc}"
                self._backoff(attempt, None)
                continue
            if response.status_code == 429 and _is_daily_limit(response):
                raise QuotaExhausted(f"{self.name} daily quota used up: {response.text[:200]}")
            if response.status_code == 429 or response.status_code >= 500:
                last_problem = f"HTTP {response.status_code}: {response.text[:200]}"
                self._backoff(attempt, response.headers.get("retry-after"))
                continue
            if response.status_code != 200:
                raise ExtractionError(f"{self.name} HTTP {response.status_code}: {response.text[:300]}")
            try:
                data = response.json()
                choice = data["choices"][0]
                content = choice["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ExtractionError(f"{self.name} returned an unexpected response: {exc}") from exc
            tokens = (data.get("usage") or {}).get("total_tokens") or 0
            return content, choice.get("finish_reason"), int(tokens)
        raise ExtractionError(f"{self.name} failed after {self._max_attempts} attempts ({last_problem})")

    def _backoff(self, attempt: int, retry_after: Optional[str]) -> None:
        if attempt >= self._max_attempts:
            return
        try:
            delay = float(retry_after) if retry_after else 2.0 * 2 ** (attempt - 1)
        except ValueError:
            delay = 2.0 * 2 ** (attempt - 1)
        log.info("%s: retrying in %.1fs (attempt %d)", self.name, delay, attempt)
        self._sleep(min(delay, MAX_BACKOFF_SECONDS))


def _is_daily_limit(response: httpx.Response) -> bool:
    if _DAILY_LIMIT_TEXT.search(response.text):
        return True
    try:
        return float(response.headers.get("retry-after", "0")) > DAILY_LIMIT_WAIT_SECONDS
    except ValueError:
        return False


class FallbackExtractor:
    """Try extractors in order; move on only when a provider fails, not when output is bad.

    An extractor whose daily quota ran out is skipped for the rest of the run. When every
    extractor is out of quota, QuotaExhausted is raised so the run can stop cleanly.
    """

    def __init__(self, extractors: list):
        if not extractors:
            raise ValueError("FallbackExtractor needs at least one extractor")
        self.extractors = extractors
        self.name = ",".join(e.name for e in extractors)
        self.model = extractors[0].model
        self._exhausted: set[str] = set()

    def extract(self, document: DocumentText) -> ExtractionResult:
        failures = []
        for extractor in self.extractors:
            if extractor.name in self._exhausted:
                continue
            try:
                return extractor.extract(document)
            except QuotaExhausted as exc:
                log.warning("%s is out of quota, trying next: %s", extractor.name, exc)
                self._exhausted.add(extractor.name)
                failures.append(str(exc))
            except ExtractionError as exc:
                log.warning("%s failed, trying next: %s", extractor.name, exc)
                failures.append(str(exc))
        if all(e.name in self._exhausted for e in self.extractors):
            raise QuotaExhausted("all extractors are out of quota: " + " | ".join(failures))
        raise ExtractionError("all extractors failed: " + " | ".join(failures))
