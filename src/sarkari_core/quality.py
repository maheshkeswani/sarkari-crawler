"""The publish gate: decides, without a reviewer, whether and how a draft goes live.

    HIGH    every check passed
    MEDIUM  published, with `flagged_fields` shown as "verify on the official notice"
    LOW     header only: the posts step failed, so the site shows dates and the official link
    None    not published (no usable header, or a BLOCK rule flag)

The database's publish_draft() function trusts this decision, so it lives in the shared
package and is covered by tests in both repos.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from .enums import Confidence, DocumentKind
from .rules import RuleFlag, check_draft
from .schemas import RecruitmentDraft


@dataclass
class Assessment:
    confidence: Optional[Confidence]
    flags: list[RuleFlag] = field(default_factory=list)
    flagged_fields: list[str] = field(default_factory=list)
    failure_reason: str = ""

    @property
    def publishable(self) -> bool:
        return self.confidence is not None


def assess(draft: Optional[RecruitmentDraft], errors: list[str]) -> Assessment:
    if draft is None:
        return Assessment(None, failure_reason="; ".join(errors) or "No draft was extracted")

    flags = check_draft(draft)
    blocking = [f for f in flags if f.blocks]
    if blocking:
        return Assessment(None, flags, failure_reason="; ".join(f.message for f in blocking))

    flagged = list(dict.fromkeys([f.path for f in flags] + list(draft.low_confidence_fields)))
    posts_failed = any(error.startswith("posts:") for error in errors)
    if draft.document_kind == DocumentKind.NOTIFICATION and (posts_failed or not draft.posts):
        confidence = Confidence.LOW
    elif flagged or errors:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.HIGH
    return Assessment(confidence, flags, flagged)


_ADVT_PREFIX = re.compile(r"^((advt|advertisement|advert|notice|notification|no|number)\s+)+")


def _words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def notice_key(advertisement_number: Optional[str], title: str, year: Optional[int]) -> str:
    """The key that ties a notification, its re-uploads and its corrigenda together.

    One recruitment per (organisation, key). Mirrors catalog_notice_key() in the database
    (sarkari-web dbsecurity migrations); a test there checks both give the same answer.
    """
    advt = _ADVT_PREFIX.sub("", _words(advertisement_number or "")).replace(" ", "")
    if advt:
        return f"adv:{advt}"
    words = _words(title).replace(" ", "")
    if not words:  # e.g. a title only in Hindi
        words = hashlib.md5(title.encode("utf-8")).hexdigest()
    return f"title:{words}:{year or ''}"
