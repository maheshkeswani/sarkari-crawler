"""Draft schemas: what the LLM extracts from one notification PDF.

A draft is stored as JSONB in `ingest_extractiondraft.payload` and is the only contract
between the crawler and the database's `publish_draft()` function, which reads it by
field name. Bump SCHEMA_VERSION on any incompatible change so old drafts can be told apart.

Validators are deliberately lenient about formatting (Indian date formats, "1,234",
"Graduate" vs "GRADUATE") and strict about meaning (unknown enum values fail).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from .enums import (
    Discipline,
    DocumentKind,
    FeeApplicant,
    GenderRestriction,
    QualificationLevel,
    RelaxationCategory,
    ReservationCategory,
    State,
)

# 2: added state, title_hi and cancelled.
SCHEMA_VERSION = 2

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y")
_EMPTY_STRINGS = {"", "-", "--", "na", "n/a", "nil", "null", "none", "not applicable", "not mentioned"}

# Spellings notifications use for the unreserved category.
_ENUM_ALIASES = {
    "GEN": "UR",
    "GENERAL": "UR",
    "UNRESERVED": "UR",
    "OPEN": "UR",
    "EXSM": "EX_SERVICEMEN",
    "ESM": "EX_SERVICEMEN",
    "EX_SERVICEMAN": "EX_SERVICEMEN",
    "PH": "PWBD",
    "PWD": "PWBD",
    "WOMEN": "FEMALE",
    "ALL_CANDIDATES": "ALL",
    # States: old names and common spellings.
    "ORISSA": "ODISHA",
    "UTTARANCHAL": "UTTARAKHAND",
    "PONDICHERRY": "PUDUCHERRY",
    "NCT_OF_DELHI": "DELHI",
    "NEW_DELHI": "DELHI",
    "JAMMU_&_KASHMIR": "JAMMU_AND_KASHMIR",
    "ANDAMAN_&_NICOBAR_ISLANDS": "ANDAMAN_AND_NICOBAR_ISLANDS",
    "ANDAMAN_AND_NICOBAR": "ANDAMAN_AND_NICOBAR_ISLANDS",
}
_WALK_IN = re.compile(r"walk[- ]?in|interview|साक्षात्कार", re.IGNORECASE)
# What models write for "not a state recruitment".
_CENTRAL_MARKERS = {"CENTRAL", "CENTRE", "CENTER", "ALL_INDIA", "INDIA", "CENTRAL_GOVERNMENT"}


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in _EMPTY_STRINGS)


def parse_lenient_date(value: Any) -> Optional[date]:
    if _is_empty(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value.strip())
        text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"unrecognised date: {value!r}")


def parse_lenient_int(value: Any) -> Optional[int]:
    if _is_empty(value):
        return None
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
        if re.fullmatch(r"\d+(\.0+)?", text):
            return int(float(text))
    raise ValueError(f"expected a whole number, got {value!r}")


def normalise_enum_value(value: Any) -> Any:
    if isinstance(value, str):
        key = re.sub(r"[\s\-/]+", "_", value.strip()).upper()
        return _ENUM_ALIASES.get(key, key)
    return value


LenientDate = Annotated[Optional[date], BeforeValidator(parse_lenient_date)]
LenientInt = Annotated[Optional[int], BeforeValidator(parse_lenient_int)]
Count = Annotated[int, BeforeValidator(parse_lenient_int), Field(ge=0)]
# Bounds sit on the inner int so that None passes through untouched.
OptionalCount = Annotated[Optional[Annotated[int, Field(ge=0)]], BeforeValidator(parse_lenient_int)]
Age = Annotated[Optional[Annotated[int, Field(ge=0, le=100)]], BeforeValidator(parse_lenient_int)]

QualificationField = Annotated[QualificationLevel, BeforeValidator(normalise_enum_value)]
DisciplineField = Annotated[Discipline, BeforeValidator(normalise_enum_value)]
ReservationField = Annotated[ReservationCategory, BeforeValidator(normalise_enum_value)]
RelaxationField = Annotated[RelaxationCategory, BeforeValidator(normalise_enum_value)]
FeeApplicantField = Annotated[FeeApplicant, BeforeValidator(normalise_enum_value)]
GenderField = Annotated[GenderRestriction, BeforeValidator(normalise_enum_value)]
DocumentKindField = Annotated[DocumentKind, BeforeValidator(normalise_enum_value)]


def _state_or_none(value: Any) -> Any:
    if _is_empty(value):
        return None
    value = normalise_enum_value(value)
    return None if value in _CENTRAL_MARKERS else value


StateField = Annotated[Optional[State], BeforeValidator(_state_or_none)]


def _is_known(enum: type[Enum], value: Any) -> bool:
    try:
        enum(normalise_enum_value(value))
        return True
    except ValueError:
        return False


def _drop_unknown_categories(data: Any) -> Any:
    """State notices use categories the central lists don't have (MOBC, EBC, BC-A, BPL...).
    One of them must not sink the whole notice: unknown values are dropped and the field is
    added to low_confidence_fields, so it's published marked "verify on the official notice".
    Runs on raw model output before validation; leaves anything it doesn't recognise alone."""
    if not isinstance(data, dict):
        return data
    data = dict(data)
    unsure: list[str] = []

    fees = data.get("fees")
    if isinstance(fees, list):
        kept = []
        for rule in fees:
            if isinstance(rule, dict) and isinstance(rule.get("applies_to"), list):
                known = [v for v in rule["applies_to"] if _is_known(FeeApplicant, v)]
                if len(known) < len(rule["applies_to"]):
                    unsure.append("fees")
                    if not known:
                        continue
                    rule = {**rule, "applies_to": known}
            kept.append(rule)
        data["fees"] = kept

    def relaxations(value: Any, path: str) -> Any:
        if not isinstance(value, list):
            return value
        known = [r for r in value if not isinstance(r, dict) or _is_known(RelaxationCategory, r.get("category"))]
        if len(known) < len(value):
            unsure.append(path)
        return known

    data["age_relaxations"] = relaxations(data.get("age_relaxations"), "age_relaxations")
    state = _state_or_none(data.get("state"))
    if state is not None and not _is_known(State, state):
        data["state"] = None
        unsure.append("state")

    posts = data.get("posts")
    if isinstance(posts, list):
        cleaned = []
        for i, post in enumerate(posts):
            if not isinstance(post, dict):
                cleaned.append(post)
                continue
            post = dict(post)
            vacancies = post.get("vacancies_by_category")
            if isinstance(vacancies, dict):
                known = {k: v for k, v in vacancies.items() if _is_known(ReservationCategory, k)}
                if len(known) < len(vacancies):
                    post["vacancies_by_category"] = known
                    unsure.append(f"posts[{i}].vacancies_by_category")
            disciplines = post.get("disciplines")
            if isinstance(disciplines, list) and not all(_is_known(Discipline, d) for d in disciplines):
                post["disciplines"] = list(dict.fromkeys(d if _is_known(Discipline, d) else "OTHER" for d in disciplines))
                unsure.append(f"posts[{i}].disciplines")
            qualification = post.get("min_qualification")
            if not _is_empty(qualification) and not _is_known(QualificationLevel, qualification):
                post["min_qualification"] = None
                unsure.append(f"posts[{i}].min_qualification")
            if not _is_empty(post.get("gender")) and not _is_known(GenderRestriction, post["gender"]):
                post["gender"] = None
                unsure.append(f"posts[{i}].gender")
            post["age_relaxation_override"] = relaxations(
                post.get("age_relaxation_override"), f"posts[{i}].age_relaxation_override"
            )
            cleaned.append(post)
        data["posts"] = cleaned

    if unsure:
        existing = data.get("low_confidence_fields")
        existing = existing if isinstance(existing, list) else []
        data["low_confidence_fields"] = list(dict.fromkeys(existing + unsure))
    return data


def _empty_to_none(value: Any) -> Any:
    return None if _is_empty(value) else value


OptionalText = Annotated[Optional[str], BeforeValidator(_empty_to_none)]


class _Draft(BaseModel):
    # LLMs add stray keys; ignoring them is safer than failing the whole draft.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class FeeRule(_Draft):
    applies_to: list[FeeApplicantField] = Field(default_factory=lambda: [FeeApplicant.ALL])
    amount_inr: Count


class AgeRelaxation(_Draft):
    category: RelaxationField
    years: Annotated[int, BeforeValidator(parse_lenient_int), Field(ge=0, le=20)]


class ImportantDate(_Draft):
    label: str = Field(min_length=1)
    on: LenientDate = None


class PostDraft(_Draft):
    name: str = Field(min_length=1)
    post_code: OptionalText = None
    department: OptionalText = None
    pay_level: OptionalText = None
    location: OptionalText = None
    total_vacancies: OptionalCount = None
    vacancies_by_category: dict[ReservationField, Count] = Field(default_factory=dict)
    vacancies_pwbd: OptionalCount = None
    vacancies_ex_servicemen: OptionalCount = None
    min_qualification: Optional[QualificationField] = None
    disciplines: list[DisciplineField] = Field(default_factory=list)
    min_age: Age = None
    max_age: Age = None
    age_relaxation_override: Optional[list[AgeRelaxation]] = None
    gender: GenderField = GenderRestriction.ANY
    final_year_allowed: Optional[bool] = None
    eligibility_notes: OptionalText = None

    @field_validator("vacancies_by_category", mode="before")
    @classmethod
    def _drop_empty_counts(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if not _is_empty(v)}
        return value

    @field_validator("disciplines", mode="before")
    @classmethod
    def _none_to_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("gender", mode="before")
    @classmethod
    def _default_gender(cls, value: Any) -> Any:
        return GenderRestriction.ANY if _is_empty(value) else value

    @field_validator("min_qualification", "final_year_allowed", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return None if _is_empty(value) else value


class RecruitmentHeaderDraft(_Draft):
    """Step 1 of extraction: everything except the posts table."""

    _lenient_categories = model_validator(mode="before")(_drop_unknown_categories)

    document_kind: DocumentKindField = DocumentKind.NOTIFICATION
    organisation_name: str = Field(min_length=1)
    # None for central recruitments.
    state: StateField = None
    exam_name: OptionalText = None
    title: str = Field(min_length=1)
    title_hi: OptionalText = None
    advertisement_number: OptionalText = None
    year: LenientInt = None
    notification_date: LenientDate = None
    application_start_date: LenientDate = None
    application_end_date: LenientDate = None
    fee_payment_last_date: LenientDate = None
    age_cutoff_date: LenientDate = None
    important_dates: list[ImportantDate] = Field(default_factory=list)
    total_vacancies: OptionalCount = None
    fees: list[FeeRule] = Field(default_factory=list)
    # None means "use CENTRAL_DEFAULT_AGE_RELAXATION".
    age_relaxations: Optional[list[AgeRelaxation]] = None
    official_website: OptionalText = None
    apply_url: OptionalText = None
    eligibility_notes: OptionalText = None
    corrigendum_summary: OptionalText = None
    # A corrigendum that cancels the whole recruitment.
    cancelled: Optional[bool] = None
    # Dotted paths the model was unsure about, e.g. "age_cutoff_date" or "posts[2].max_age".
    low_confidence_fields: list[str] = Field(default_factory=list)

    @field_validator("important_dates", "fees", "low_confidence_fields", mode="before")
    @classmethod
    def _none_to_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("cancelled", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return None if _is_empty(value) else value

    @field_validator("document_kind", mode="before")
    @classmethod
    def _default_kind(cls, value: Any) -> Any:
        return DocumentKind.NOTIFICATION if _is_empty(value) else value

    @model_validator(mode="after")
    def _walk_in_date_as_last_date(self):
        """Walk-in interviews have an interview date instead of a last date to apply. Models
        often file it under important_dates; use the latest one, marked for verification."""
        if self.application_end_date is None and self.document_kind == DocumentKind.NOTIFICATION:
            interview_dates = [d.on for d in self.important_dates if d.on and _WALK_IN.search(d.label)]
            if interview_dates:
                self.application_end_date = max(interview_dates)
                if "application_end_date" not in self.low_confidence_fields:
                    self.low_confidence_fields.append("application_end_date")
        return self


class PostsDraft(_Draft):
    """Step 2 of extraction: the posts table."""

    _lenient_categories = model_validator(mode="before")(_drop_unknown_categories)

    posts: list[PostDraft] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)

    @field_validator("posts", "low_confidence_fields", mode="before")
    @classmethod
    def _none_to_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value


class RecruitmentDraft(RecruitmentHeaderDraft):
    """A complete draft as stored in extraction_drafts.payload."""

    schema_version: int = SCHEMA_VERSION
    posts: list[PostDraft] = Field(default_factory=list)

    @classmethod
    def combine(cls, header: RecruitmentHeaderDraft, posts: PostsDraft) -> "RecruitmentDraft":
        data = header.model_dump()
        data["posts"] = [p.model_dump() for p in posts.posts]
        data["low_confidence_fields"] = list(dict.fromkeys(header.low_confidence_fields + posts.low_confidence_fields))
        return cls.model_validate(data)
