from sarkari_core.rules import check_draft
from sarkari_core.schemas import RecruitmentDraft


def codes(payload: dict) -> set[tuple[str, str]]:
    return {(f.path, f.code) for f in check_draft(RecruitmentDraft.model_validate(payload))}


def test_clean_draft_has_no_flags(sample_draft):
    assert check_draft(sample_draft) == []


def test_missing_last_date_and_posts(sample_payload):
    sample_payload["application_end_date"] = None
    sample_payload["posts"] = []
    sample_payload["total_vacancies"] = None
    assert codes(sample_payload) == {("application_end_date", "missing"), ("posts", "missing")}


def test_corrigendum_without_posts_is_fine(sample_payload):
    sample_payload.update(document_kind="CORRIGENDUM", posts=[], total_vacancies=None)
    assert codes(sample_payload) == set()


def test_end_before_start(sample_payload):
    sample_payload["application_end_date"] = "2026-06-01"
    sample_payload["fee_payment_last_date"] = None
    assert ("application_end_date", "before_start") in codes(sample_payload)


def test_start_before_notification(sample_payload):
    sample_payload["notification_date"] = "2026-06-20"
    assert ("application_start_date", "before_notification") in codes(sample_payload)


def test_fee_deadline_far_before_last_date(sample_payload):
    sample_payload["fee_payment_last_date"] = "2026-06-15"
    assert ("fee_payment_last_date", "well_before_end") in codes(sample_payload)


def test_age_cutoff_far_from_notification(sample_payload):
    sample_payload["age_cutoff_date"] = "2028-01-01"
    assert ("age_cutoff_date", "far_from_notification") in codes(sample_payload)


def test_unusual_fee(sample_payload):
    sample_payload["fees"][0]["amount_inr"] = 25000
    assert ("fees[0].amount_inr", "unusual_amount") in codes(sample_payload)


def test_post_checks(sample_payload):
    post = sample_payload["posts"][0]
    post.update(min_age=35, max_age=30, min_qualification=None)
    post["vacancies_by_category"]["UR"] = 11
    found = codes(sample_payload)
    assert ("posts[0].max_age", "age_range") in found
    assert ("posts[0].min_qualification", "missing") in found
    assert ("posts[0].vacancies_by_category", "sum_mismatch") in found


def test_unusual_age(sample_payload):
    sample_payload["posts"][1]["max_age"] = 90
    assert ("posts[1].max_age", "unusual_age") in codes(sample_payload)


def test_posts_total_mismatch(sample_payload):
    sample_payload["total_vacancies"] = 31
    assert ("total_vacancies", "sum_mismatch") in codes(sample_payload)


def test_posts_total_not_checked_when_a_post_total_is_unknown(sample_payload):
    sample_payload["total_vacancies"] = 31
    sample_payload["posts"][1]["total_vacancies"] = None
    assert ("total_vacancies", "sum_mismatch") not in codes(sample_payload)


def test_blocking_and_warning_severity(sample_payload):
    sample_payload["application_end_date"] = "2026-06-01"
    sample_payload["fee_payment_last_date"] = None
    sample_payload["fees"][0]["amount_inr"] = 25000
    flags = {f.code: f.blocks for f in check_draft(RecruitmentDraft.model_validate(sample_payload))}
    assert flags == {"before_start": True, "unusual_amount": False}


def test_application_window_over_a_year(sample_payload):
    sample_payload["application_end_date"] = "2027-12-31"
    sample_payload["fee_payment_last_date"] = None
    assert ("application_end_date", "long_window") in codes(sample_payload)
