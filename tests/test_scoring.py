import pytest

from sarkari_core.schemas import RecruitmentDraft
from sarkari_crawler.scoring import match_posts, norm_advt, score_case, summarise


def overall(score):
    return summarise([score])[1]


def test_identical_drafts_score_perfectly(sample_draft):
    result = overall(score_case("c", sample_draft, sample_draft))
    assert result.accuracy == 1.0 and result.hallucinated == 0


def test_wrong_and_hallucinated_fields(sample_payload, sample_draft):
    actual = dict(sample_payload, application_end_date="2026-07-05", exam_name=None)
    expected = RecruitmentDraft.model_validate(dict(sample_payload, fee_payment_last_date=None))
    score = score_case("c", expected, RecruitmentDraft.model_validate(actual))
    wrong = {f.field for f in score.fields if f.scored and not f.correct}
    hallucinated = {f.field for f in score.fields if f.hallucinated}
    assert wrong == {"application_end_date"}
    assert hallucinated == {"fee_payment_last_date"}


def test_post_order_and_name_spelling_do_not_matter(sample_payload, sample_draft):
    posts = sample_payload["posts"]
    posts.reverse()
    posts[1]["name"] = "Assistant Section Officer (CSS)"
    score = score_case("c", sample_draft, RecruitmentDraft.model_validate(sample_payload))
    assert score.matched_posts == 2
    assert overall(score).accuracy == 1.0


def test_missed_posts_count_as_wrong(sample_payload, sample_draft):
    sample_payload["posts"] = sample_payload["posts"][:1]
    score = score_case("c", sample_draft, RecruitmentDraft.model_validate(sample_payload))
    assert (score.post_recall, score.post_precision) == (0.5, 1.0)
    missed = [f for f in score.fields if f.field.startswith("posts.") and f.actual is None and f.scored]
    assert len(missed) == 6  # the second post's filled-in fields, incl. default gender


def test_no_draft_scores_zero(sample_draft):
    result = overall(score_case("c", sample_draft, None))
    assert result.correct == 0 and result.scored > 0


def test_match_posts_ignores_unrelated_names(sample_draft):
    other = RecruitmentDraft.model_validate({"organisation_name": "x", "title": "y", "posts": [{"name": "Driver"}]})
    assert match_posts(sample_draft.posts, other.posts) == []


@pytest.mark.parametrize("raw", ["Advt. No. 05/2026", "Advertisement No 05/2026", "05/2026", "NOTICE NO. 05/2026"])
def test_norm_advt(raw):
    assert norm_advt(raw) == "05 2026"
