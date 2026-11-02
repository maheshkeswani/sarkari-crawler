from datetime import date

import pytest
from pydantic import ValidationError

from sarkari_core.enums import Discipline, QualificationLevel, ReservationCategory
from sarkari_core.schemas import (
    SCHEMA_VERSION,
    PostDraft,
    PostsDraft,
    RecruitmentDraft,
    RecruitmentHeaderDraft,
    parse_lenient_date,
    parse_lenient_int,
)


@pytest.mark.parametrize(
    "raw",
    ["2026-11-15", "15-11-2026", "15/11/2026", "15.11.2026", "15 November 2026", "15th November 2026", "15 Nov, 2026"],
)
def test_dates_accept_indian_formats(raw):
    assert parse_lenient_date(raw) == date(2026, 11, 15)


@pytest.mark.parametrize("raw", [None, "", " ", "NA", "N/A", "-", "Not mentioned"])
def test_empty_markers_become_none(raw):
    assert parse_lenient_date(raw) is None
    assert parse_lenient_int(raw) is None


@pytest.mark.parametrize("raw", ["31-02-2026", "next Monday", "2026"])
def test_invalid_dates_fail(raw):
    with pytest.raises(ValueError):
        parse_lenient_date(raw)


@pytest.mark.parametrize("raw, expected", [("1,234", 1234), ("₹100", 100), ("Rs. 850", 850), (12.0, 12), ("7", 7)])
def test_ints_accept_formatting(raw, expected):
    assert parse_lenient_int(raw) == expected


@pytest.mark.parametrize("raw", ["about 10", True, 1.5, "ten"])
def test_invalid_ints_fail(raw):
    with pytest.raises(ValueError):
        parse_lenient_int(raw)


def test_post_normalises_enum_spellings_and_drops_empty_counts():
    post = PostDraft.model_validate(
        {
            "name": "Clerk",
            "vacancies_by_category": {"General": "1,200", "obc": 5, "SC": None, "ST": "NA"},
            "min_qualification": "graduate",
            "disciplines": ["any"],
            "gender": "",
            "final_year_allowed": "NA",
        }
    )
    assert post.vacancies_by_category == {ReservationCategory.UR: 1200, ReservationCategory.OBC: 5}
    assert post.min_qualification is QualificationLevel.GRADUATE
    assert post.disciplines == [Discipline.ANY]
    assert post.gender.value == "ANY"
    assert post.final_year_allowed is None


def test_unknown_enum_value_fails():
    with pytest.raises(ValidationError):
        PostDraft.model_validate({"name": "Clerk", "min_qualification": "Masters in anything"})


def test_negative_vacancies_fail():
    with pytest.raises(ValidationError):
        PostDraft.model_validate({"name": "Clerk", "total_vacancies": -3})


def test_extra_keys_are_ignored(sample_payload):
    sample_payload["confidence"] = "high"
    sample_payload["posts"][0]["notes_from_model"] = "..."
    draft = RecruitmentDraft.model_validate(sample_payload)
    assert not hasattr(draft, "confidence")


def test_null_lists_become_empty():
    header = RecruitmentHeaderDraft.model_validate(
        {"organisation_name": "UPSC", "title": "CSE 2026", "fees": None, "important_dates": None, "low_confidence_fields": None}
    )
    assert header.fees == [] and header.important_dates == [] and header.low_confidence_fields == []
    assert PostsDraft.model_validate({"posts": None}).posts == []


def test_combine_merges_steps_and_deduplicates_low_confidence(sample_payload):
    posts = sample_payload.pop("posts")
    header = RecruitmentHeaderDraft.model_validate({**sample_payload, "low_confidence_fields": ["age_cutoff_date"]})
    posts_draft = PostsDraft.model_validate({"posts": posts, "low_confidence_fields": ["posts[1].max_age", "age_cutoff_date"]})

    draft = RecruitmentDraft.combine(header, posts_draft)

    assert draft.schema_version == SCHEMA_VERSION
    assert len(draft.posts) == 2
    assert draft.low_confidence_fields == ["age_cutoff_date", "posts[1].max_age"]


def test_json_round_trip(sample_draft):
    restored = RecruitmentDraft.model_validate_json(sample_draft.model_dump_json())
    assert restored == sample_draft


def test_v2_header_fields(sample_payload):
    sample_payload.update(state="Bihar", title_hi="संयुक्त स्नातक स्तरीय परीक्षा", cancelled="N/A")
    draft = RecruitmentDraft.model_validate(sample_payload)
    assert draft.state.value == "BIHAR" and draft.title_hi.startswith("संयुक्त") and draft.cancelled is None
    assert draft.schema_version == 2
    dumped = draft.model_dump(mode="json")
    assert dumped["state"] == "BIHAR" and dumped["cancelled"] is None


# The first real GPT-OSS-120B reply (APSC, 2026-11-02): Assam-specific categories.
APSC_REPLY = {
    "document_kind": "NOTIFICATION", "organisation_name": "Assam Public Service Commission", "state": "ASSAM",
    "title": "Research Assistant under Labour Welfare Department", "advertisement_number": "12 /2026", "year": 2026,
    "application_end_date": "2026-09-10",
    "fees": [
        {"applies_to": ["GENERAL"], "amount_inr": 297},
        {"applies_to": ["OBC", "MOBC"], "amount_inr": 197},
        {"applies_to": ["SC", "ST", "BPL", "PWBD"], "amount_inr": 47},
        {"applies_to": ["BPL"], "amount_inr": 0},
    ],
    "age_relaxations": [{"category": "SC", "years": 5}, {"category": "MOBC", "years": 3}],
    "low_confidence_fields": ["exam_name"],
}


def test_state_categories_are_dropped_and_flagged_not_fatal():
    header = RecruitmentHeaderDraft.model_validate(APSC_REPLY)
    assert [[a.value for a in f.applies_to] for f in header.fees] == [["UR"], ["OBC"], ["SC", "ST", "PWBD"]]
    assert [r.category.value for r in header.age_relaxations] == ["SC"]
    assert header.low_confidence_fields == ["exam_name", "fees", "age_relaxations"]


def test_post_categories_are_dropped_and_flagged_not_fatal():
    posts = PostsDraft.model_validate(
        {"posts": [{"name": "A", "vacancies_by_category": {"UR": 3, "MBC": 1}, "disciplines": ["ARTS", "GEOGRAPHY"],
                    "min_qualification": "Matric pass", "gender": "Both"}]}
    )
    post = posts.posts[0]
    assert {k.value: v for k, v in post.vacancies_by_category.items()} == {"UR": 3}
    assert [d.value for d in post.disciplines] == ["ARTS", "OTHER"]
    assert post.min_qualification is None and post.gender.value == "ANY"
    assert posts.low_confidence_fields == [
        "posts[0].vacancies_by_category", "posts[0].disciplines", "posts[0].min_qualification", "posts[0].gender",
    ]


def test_known_values_are_untouched(sample_payload):
    draft = RecruitmentDraft.model_validate(sample_payload)
    assert draft.low_confidence_fields == []


# The real reply for an ESIC walk-in notice (2026-11-02): the date is only in important_dates.
ESIC_WALK_IN = {
    "document_kind": "NOTIFICATION", "organisation_name": "Employees' State Insurance Corporation",
    "title": "Walk-in interview on 13.11.2026 for the appointment of Full Time Contractual Specialists",
    "advertisement_number": "05/2026", "notification_date": "2026-10-28", "application_end_date": None,
    "important_dates": [{"label": "Interview Date", "on": "2026-11-13"}], "low_confidence_fields": [],
}


def test_walk_in_interview_date_becomes_the_last_date():
    header = RecruitmentHeaderDraft.model_validate(ESIC_WALK_IN)
    assert header.application_end_date == date(2026, 11, 13)
    assert header.low_confidence_fields == ["application_end_date"]


def test_other_important_dates_are_not_used_as_the_last_date():
    payload = {**ESIC_WALK_IN, "important_dates": [{"label": "Exam date", "on": "2026-12-01"}]}
    assert RecruitmentHeaderDraft.model_validate(payload).application_end_date is None
