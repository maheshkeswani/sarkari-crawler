"""Sanity checks run on every draft before it is published.

Each failed check becomes a RuleFlag. A BLOCK flag stops the draft from being published;
a WARN flag publishes it with that field marked "verify on the official notice"
(see sarkari_core.quality). Checks only flag; they never change the draft.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .schemas import RecruitmentDraft

MAX_REASONABLE_FEE_INR = 2000
MAX_CUTOFF_DRIFT = timedelta(days=366)
MAX_APPLICATION_WINDOW = timedelta(days=366)

BLOCK = "block"
WARN = "warn"


@dataclass(frozen=True)
class RuleFlag:
    path: str
    code: str
    message: str
    severity: str = WARN

    @property
    def blocks(self) -> bool:
        return self.severity == BLOCK

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message, "severity": self.severity}


def check_draft(draft: RecruitmentDraft) -> list[RuleFlag]:
    flags: list[RuleFlag] = []
    flags += _check_required(draft)
    flags += _check_dates(draft)
    flags += _check_fees(draft)
    flags += _check_posts(draft)
    return flags


def _check_required(draft: RecruitmentDraft) -> list[RuleFlag]:
    flags = []
    if draft.document_kind.value != "NOTIFICATION":
        return flags  # a corrigendum states only what changed
    if draft.application_end_date is None:
        flags.append(RuleFlag("application_end_date", "missing", "Last date to apply is missing", BLOCK))
    if not draft.posts:
        flags.append(RuleFlag("posts", "missing", "No posts were extracted"))
    return flags


def _check_dates(draft: RecruitmentDraft) -> list[RuleFlag]:
    flags = []
    start, end = draft.application_start_date, draft.application_end_date
    if start and end and end < start:
        flags.append(
            RuleFlag("application_end_date", "before_start", f"Last date {end} is before start date {start}", BLOCK)
        )
    if start and end and end - start > MAX_APPLICATION_WINDOW:
        flags.append(
            RuleFlag("application_end_date", "long_window", f"Applications stay open from {start} to {end}, over a year")
        )
    if draft.notification_date and start and start < draft.notification_date:
        flags.append(
            RuleFlag(
                "application_start_date",
                "before_notification",
                f"Applications open {start}, before the notification date {draft.notification_date}",
            )
        )
    if draft.fee_payment_last_date and end and draft.fee_payment_last_date < end - timedelta(days=7):
        flags.append(
            RuleFlag(
                "fee_payment_last_date",
                "well_before_end",
                f"Fee deadline {draft.fee_payment_last_date} is more than a week before the last date {end}",
            )
        )
    anchor = _anchor_date(draft)
    cutoff = draft.age_cutoff_date
    if cutoff and anchor and abs(cutoff - anchor) > MAX_CUTOFF_DRIFT:
        flags.append(
            RuleFlag("age_cutoff_date", "far_from_notification", f"Age cut-off {cutoff} is over a year from {anchor}")
        )
    return flags


def _anchor_date(draft: RecruitmentDraft) -> Optional[date]:
    return draft.notification_date or draft.application_start_date or draft.application_end_date


def _check_fees(draft: RecruitmentDraft) -> list[RuleFlag]:
    return [
        RuleFlag(f"fees[{i}].amount_inr", "unusual_amount", f"Fee ₹{fee.amount_inr} is above ₹{MAX_REASONABLE_FEE_INR}")
        for i, fee in enumerate(draft.fees)
        if fee.amount_inr > MAX_REASONABLE_FEE_INR
    ]


def _check_posts(draft: RecruitmentDraft) -> list[RuleFlag]:
    flags = []
    post_totals = []
    for i, post in enumerate(draft.posts):
        prefix = f"posts[{i}]"
        if post.min_age is not None and post.max_age is not None and post.min_age >= post.max_age:
            flags.append(RuleFlag(f"{prefix}.max_age", "age_range", f"Min age {post.min_age} ≥ max age {post.max_age}"))
        for field_name in ("min_age", "max_age"):
            age = getattr(post, field_name)
            if age is not None and not 14 <= age <= 65:
                flags.append(RuleFlag(f"{prefix}.{field_name}", "unusual_age", f"Age {age} is outside 14–65"))
        if post.vacancies_by_category and post.total_vacancies is not None:
            category_sum = sum(post.vacancies_by_category.values())
            if category_sum != post.total_vacancies:
                flags.append(
                    RuleFlag(
                        f"{prefix}.vacancies_by_category",
                        "sum_mismatch",
                        f"Category vacancies add up to {category_sum}, total says {post.total_vacancies}",
                    )
                )
        if post.min_qualification is None:
            flags.append(RuleFlag(f"{prefix}.min_qualification", "missing", "Minimum qualification is missing"))
        post_totals.append(post.total_vacancies)

    if draft.total_vacancies is not None and draft.posts and all(t is not None for t in post_totals):
        posts_sum = sum(post_totals)
        if posts_sum != draft.total_vacancies:
            flags.append(
                RuleFlag(
                    "total_vacancies",
                    "sum_mismatch",
                    f"Posts add up to {posts_sum} vacancies, notification total says {draft.total_vacancies}",
                )
            )
    return flags
