import pytest

from sarkari_core.enums import Confidence, State
from sarkari_core.quality import assess, notice_key
from sarkari_core.rules import check_draft
from sarkari_core.schemas import RecruitmentDraft, RecruitmentHeaderDraft


def test_clean_draft_is_high(sample_draft):
    result = assess(sample_draft, [])
    assert result.confidence == Confidence.HIGH and result.publishable
    assert result.flagged_fields == [] and result.failure_reason == ""


def test_warnings_and_unsure_fields_make_medium(sample_payload):
    sample_payload["total_vacancies"] = 31
    sample_payload["low_confidence_fields"] = ["age_cutoff_date", "total_vacancies"]
    result = assess(RecruitmentDraft.model_validate(sample_payload), [])
    assert result.confidence == Confidence.MEDIUM
    assert result.flagged_fields == ["total_vacancies", "age_cutoff_date"]


def test_failed_posts_step_is_low(sample_payload):
    sample_payload["posts"] = []
    result = assess(RecruitmentDraft.model_validate(sample_payload), ["posts: no JSON object in model output"])
    assert result.confidence == Confidence.LOW


def test_corrigendum_without_posts_is_high(sample_payload):
    sample_payload.update(document_kind="CORRIGENDUM", posts=[], total_vacancies=None, application_end_date=None)
    assert assess(RecruitmentDraft.model_validate(sample_payload), []).confidence == Confidence.HIGH


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"application_end_date": None}, "Last date to apply is missing"),
        ({"application_end_date": "2026-06-01", "fee_payment_last_date": None}, "before start date"),
    ],
)
def test_blocking_flags_stop_publishing(sample_payload, change, reason):
    sample_payload.update(change)
    result = assess(RecruitmentDraft.model_validate(sample_payload), [])
    assert result.confidence is None and not result.publishable
    assert reason in result.failure_reason


def test_no_draft_is_not_publishable():
    result = assess(None, ["header: organisation_name: Field required"])
    assert result.confidence is None and "organisation_name" in result.failure_reason


def test_flags_carry_severity(sample_payload):
    sample_payload["application_end_date"] = None
    flags = {f.code: f.as_dict()["severity"] for f in check_draft(RecruitmentDraft.model_validate(sample_payload))}
    assert flags["missing"] == "block"


@pytest.mark.parametrize(
    "advt, title, year, key",
    [
        ("Advt. No. 05/2026", "x", None, "adv:052026"),
        ("05/2026", "y", 2026, "adv:052026"),
        ("Advertisement No. CEN 01/2025", "RRB NTPC", 2025, "adv:cen012025"),
        (None, "Combined Graduate Level Examination, 2026", 2026, "title:combinedgraduatelevelexamination2026:2026"),
        ("—", "SSC GD 2026", None, "title:sscgd2026:"),
    ],
)
def test_notice_key(advt, title, year, key):
    assert notice_key(advt, title, year) == key


def test_notice_key_for_hindi_title_is_stable():
    first = notice_key(None, "भर्ती विज्ञापन", 2026)
    assert first.startswith("title:") and first == notice_key(None, "भर्ती विज्ञापन", 2026)
    assert first != notice_key(None, "दूसरा विज्ञापन", 2026)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Uttar Pradesh", State.UTTAR_PRADESH),
        ("uttar-pradesh", State.UTTAR_PRADESH),
        ("Orissa", State.ODISHA),
        ("Jammu & Kashmir", State.JAMMU_AND_KASHMIR),
        ("NCT of Delhi", State.DELHI),
        ("Central", None),
        ("All India", None),
        (None, None),
        ("", None),
    ],
)
def test_state_normalisation(raw, expected):
    header = RecruitmentHeaderDraft.model_validate({"organisation_name": "X", "title": "Y", "state": raw})
    assert header.state == expected


def test_unknown_state_is_dropped_and_flagged():
    # The published state comes from the organisation, so a bad value isn't worth failing over.
    header = RecruitmentHeaderDraft.model_validate({"organisation_name": "X", "title": "Y", "state": "Atlantis"})
    assert header.state is None and header.low_confidence_fields == ["state"]
