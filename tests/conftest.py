import copy

import pymupdf
import pytest

from sarkari_core.schemas import RecruitmentDraft

SAMPLE_DRAFT = {
    "document_kind": "NOTIFICATION",
    "organisation_name": "Staff Selection Commission",
    "exam_name": "Combined Graduate Level Examination",
    "title": "Combined Graduate Level Examination, 2026",
    "advertisement_number": "F. No. HQ-PPII03/7/2026-PP_II",
    "year": 2026,
    "notification_date": "2026-06-09",
    "application_start_date": "2026-06-09",
    "application_end_date": "2026-07-04",
    "fee_payment_last_date": "2026-07-05",
    "age_cutoff_date": "2026-08-01",
    "total_vacancies": 30,
    "fees": [
        {"applies_to": ["UR", "OBC", "EWS"], "amount_inr": 100},
        {"applies_to": ["FEMALE", "SC", "ST", "PWBD", "EX_SERVICEMEN"], "amount_inr": 0},
    ],
    "apply_url": "https://ssc.gov.in",
    "posts": [
        {
            "name": "Assistant Section Officer",
            "department": "Central Secretariat Service",
            "pay_level": "Level 7",
            "total_vacancies": 20,
            "vacancies_by_category": {"UR": 10, "OBC": 5, "SC": 3, "ST": 1, "EWS": 1},
            "min_qualification": "GRADUATE",
            "disciplines": ["ANY"],
            "min_age": 20,
            "max_age": 30,
            "final_year_allowed": True,
        },
        {
            "name": "Junior Statistical Officer",
            "pay_level": "Level 6",
            "total_vacancies": 10,
            "min_qualification": "GRADUATE",
            "disciplines": ["STATISTICS", "MATHEMATICS"],
            "min_age": 18,
            "max_age": 32,
        },
    ],
}


@pytest.fixture
def sample_payload() -> dict:
    return copy.deepcopy(SAMPLE_DRAFT)


@pytest.fixture
def sample_draft(sample_payload) -> RecruitmentDraft:
    return RecruitmentDraft.model_validate(sample_payload)


def make_pdf(pages: list[str]) -> bytes:
    """Build a PDF with one text page per entry; an empty string makes a blank (scan-like) page."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def pdf_factory():
    return make_pdf
