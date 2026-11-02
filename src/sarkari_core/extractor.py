"""The provider-neutral extraction interface.

The crawler picks an Extractor by name ("groq:<model>", "openrouter:<model>") and
stores `extractor`, `model` and `prompt_version` on every draft, so accuracy can be
compared across providers and prompt changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from .schemas import RecruitmentDraft


@dataclass(frozen=True)
class PageText:
    number: int  # 1-based, as printed in the PDF viewer
    text: str
    ocr: bool = False


@dataclass(frozen=True)
class DocumentText:
    """The pages selected for extraction. Building this is the crawler's job."""

    header_pages: list[PageText]  # dates, fees, how to apply
    posts_pages: list[PageText]  # vacancy tables, eligibility
    sha256: str = ""
    source_url: str = ""
    # The link's label on the listing page; the title when the notice prints none.
    title_hint: str = ""
    # The source's organisation; publish_draft() uses the source's anyway.
    organisation_hint: str = ""


@dataclass
class ExtractionResult:
    extractor: str
    model: str
    prompt_version: str
    draft: Optional[RecruitmentDraft] = None
    errors: list[str] = field(default_factory=list)
    # Raw model responses, kept so a failed draft can be diagnosed.
    raw_outputs: list[str] = field(default_factory=list)
    # Total tokens the provider reported for this document (prompt + completion + reasoning).
    tokens_used: int = 0

    @property
    def ok(self) -> bool:
        return self.draft is not None and not self.errors


class ExtractionError(Exception):
    """The provider failed (network, quota, server error). Fallbacks may retry."""


class QuotaExhausted(ExtractionError):
    """The provider's daily free quota is used up. Retrying today is pointless; the
    document stays queued for the next run."""


@runtime_checkable
class Extractor(Protocol):
    name: str
    model: str

    def extract(self, document: DocumentText) -> ExtractionResult: ...
