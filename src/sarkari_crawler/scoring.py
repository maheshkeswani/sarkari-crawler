"""Field-by-field comparison of an extracted draft against a hand-checked one.

Only fields the expected draft fills in are scored for accuracy. A value the model
returned where the expected draft has null counts as a hallucination instead, so
empty fields can't inflate the score.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sarkari_core.schemas import PostDraft, RecruitmentDraft

POST_MATCH_THRESHOLD = 0.5


def norm_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def norm_advt(value: Optional[str]) -> Optional[str]:
    # "Advt. No. 05/2026" and "05/2026" are the same advertisement.
    if value is None:
        return None
    return re.sub(r"^((advt|advertisement|notice|no|number)\s+)+", "", norm_text(value) or "")


def norm_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return re.sub(r"^https?://(www\.)?", "", value.strip().lower()).rstrip("/")


def norm_fees(fees: list) -> frozenset:
    return frozenset((frozenset(a.value for a in fee.applies_to), fee.amount_inr) for fee in fees)


def norm_enum(value: Any) -> Any:
    return getattr(value, "value", value)


# field name -> normaliser applied to both sides before comparing
HEADER_FIELDS: dict[str, Callable[[Any], Any]] = {
    "document_kind": norm_enum,
    "organisation_name": norm_text,
    "advertisement_number": norm_advt,
    "year": lambda v: v,
    "notification_date": lambda v: v,
    "application_start_date": lambda v: v,
    "application_end_date": lambda v: v,
    "fee_payment_last_date": lambda v: v,
    "age_cutoff_date": lambda v: v,
    "total_vacancies": lambda v: v,
    "fees": lambda v: norm_fees(v) or None,
    "apply_url": norm_url,
}

POST_FIELDS: dict[str, Callable[[Any], Any]] = {
    "total_vacancies": lambda v: v,
    "vacancies_by_category": lambda v: {norm_enum(k): n for k, n in v.items()} or None,
    "min_qualification": norm_enum,
    "disciplines": lambda v: frozenset(norm_enum(d) for d in v) or None,
    "min_age": lambda v: v,
    "max_age": lambda v: v,
    "gender": norm_enum,
    "final_year_allowed": lambda v: v,
}


@dataclass
class FieldResult:
    field: str
    expected: Any
    actual: Any

    @property
    def scored(self) -> bool:
        return self.expected is not None

    @property
    def correct(self) -> bool:
        return self.scored and self.expected == self.actual

    @property
    def hallucinated(self) -> bool:
        return self.expected is None and self.actual is not None


@dataclass
class CaseScore:
    case_id: str
    fields: list[FieldResult] = field(default_factory=list)
    expected_posts: int = 0
    actual_posts: int = 0
    matched_posts: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def post_recall(self) -> Optional[float]:
        return self.matched_posts / self.expected_posts if self.expected_posts else None

    @property
    def post_precision(self) -> Optional[float]:
        return self.matched_posts / self.actual_posts if self.actual_posts else None


def _compare(name: str, normaliser: Callable[[Any], Any], expected: Any, actual: Any) -> FieldResult:
    return FieldResult(name, normaliser(expected) if expected is not None else None, normaliser(actual) if actual is not None else None)


def _name_similarity(a: str, b: str) -> float:
    ta, tb = set((norm_text(a) or "").split()), set((norm_text(b) or "").split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match_posts(expected: list[PostDraft], actual: list[PostDraft]) -> list[tuple[PostDraft, PostDraft]]:
    """Greedily pair posts by name similarity, best pairs first."""
    candidates = sorted(
        (
            (_name_similarity(e.name, a.name), i, j)
            for i, e in enumerate(expected)
            for j, a in enumerate(actual)
        ),
        reverse=True,
    )
    used_e, used_a, pairs = set(), set(), []
    for similarity, i, j in candidates:
        if similarity < POST_MATCH_THRESHOLD:
            break
        if i in used_e or j in used_a:
            continue
        used_e.add(i)
        used_a.add(j)
        pairs.append((expected[i], actual[j]))
    return pairs


def score_case(case_id: str, expected: RecruitmentDraft, actual: Optional[RecruitmentDraft]) -> CaseScore:
    score = CaseScore(case_id=case_id, expected_posts=len(expected.posts))
    if actual is None:
        # Every expected field counts as wrong.
        empty = {name: None for name in HEADER_FIELDS}
        actual = RecruitmentDraft.model_construct(**{**empty, "fees": [], "posts": []})
    score.actual_posts = len(actual.posts)

    for name, normaliser in HEADER_FIELDS.items():
        score.fields.append(_compare(name, normaliser, getattr(expected, name), getattr(actual, name)))

    pairs = match_posts(expected.posts, actual.posts)
    score.matched_posts = len(pairs)
    matched_expected = {id(e) for e, _ in pairs}
    for exp_post, act_post in pairs:
        for name, normaliser in POST_FIELDS.items():
            score.fields.append(_compare(f"posts.{name}", normaliser, getattr(exp_post, name), getattr(act_post, name)))
    # Posts the model missed: all their filled-in fields count as wrong.
    for exp_post in expected.posts:
        if id(exp_post) in matched_expected:
            continue
        for name, normaliser in POST_FIELDS.items():
            value = getattr(exp_post, name)
            if value is not None:
                score.fields.append(FieldResult(f"posts.{name}", normaliser(value), None))
    return score


@dataclass
class FieldSummary:
    correct: int = 0
    scored: int = 0
    hallucinated: int = 0

    @property
    def accuracy(self) -> Optional[float]:
        return self.correct / self.scored if self.scored else None


def summarise(cases: list[CaseScore]) -> tuple[dict[str, FieldSummary], FieldSummary]:
    per_field: dict[str, FieldSummary] = defaultdict(FieldSummary)
    overall = FieldSummary()
    for case in cases:
        for result in case.fields:
            summary = per_field[result.field]
            if result.scored:
                summary.scored += 1
                overall.scored += 1
                if result.correct:
                    summary.correct += 1
                    overall.correct += 1
            if result.hallucinated:
                summary.hallucinated += 1
                overall.hallucinated += 1
    return dict(per_field), overall
