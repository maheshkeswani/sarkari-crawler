import hashlib

from sarkari_crawler.pdf import (
    HEADER_KEYWORDS,
    HEADER_TOKEN_BUDGET,
    POSTS_KEYWORDS,
    Page,
    candidate_pages,
    clean_text,
    estimate_tokens,
    latin_share,
    prepare_document,
    prepare_text,
    read_pages,
    score_page,
    select_pages,
    text_pages,
)

TITLE_PAGE = "Staff Selection Commission\nNotice: Combined Graduate Level Examination, 2026\nAdvt. No. 05/2026"
DATES_PAGE = "Important Dates\nLast date for receipt of online applications: 04-07-2026\nApplication fee: Rs 100"
VACANCY_PAGE = "Name of Post | Vacancies | UR | OBC | SC | ST | EWS\nAssistant Section Officer | 20 | 10 | 5 | 3 | 1 | 1"
SYLLABUS_PAGE = "Scheme of examination and syllabus. General Intelligence and Reasoning. Quantitative Aptitude."
HINDI_TEXT = "कर्मचारी चयन आयोग संयुक्त स्नातक स्तरीय परीक्षा अंतिम तिथि"


def page(number: int, text: str) -> Page:
    return Page(number, text, False, latin_share(text))


def test_latin_share():
    assert latin_share("") == 0.0
    assert latin_share("Last date") == 1.0
    assert latin_share(HINDI_TEXT) == 0.0
    assert 0.3 < latin_share(f"{HINDI_TEXT} Staff Selection Commission Last Date") < 0.7


def test_hindi_and_blank_pages_are_not_english():
    assert not page(1, HINDI_TEXT).is_english
    assert not page(1, "").is_english
    assert page(1, f"{HINDI_TEXT} Staff Selection Commission combined graduate level").is_english


def test_clean_text_collapses_whitespace_but_keeps_lines():
    assert clean_text("A   B\t C \n\n\n\n D ") == "A B C\n\nD"


def test_keyword_scoring_prefers_relevant_pages():
    assert score_page(DATES_PAGE, HEADER_KEYWORDS) > score_page(SYLLABUS_PAGE, HEADER_KEYWORDS)
    assert score_page(VACANCY_PAGE, POSTS_KEYWORDS) > score_page(DATES_PAGE, POSTS_KEYWORDS)
    # Category abbreviations only count in upper case.
    assert score_page("the street is scenic", [(r"\bST\b|\bSC\b", 1)]) == 0


def test_select_pages_keeps_first_english_page_and_relevant_ones_in_order():
    pages = [page(1, HINDI_TEXT), page(2, TITLE_PAGE), page(3, SYLLABUS_PAGE), page(4, VACANCY_PAGE), page(5, DATES_PAGE)]
    header = select_pages(pages, HEADER_KEYWORDS, token_budget=10_000)
    posts = select_pages(pages, POSTS_KEYWORDS, token_budget=10_000)
    assert [p.number for p in header] == [2, 5]
    assert [p.number for p in posts] == [2, 4]


def test_select_pages_respects_budget():
    pages = [page(1, TITLE_PAGE), page(2, DATES_PAGE), page(3, "Important dates, last date. " * 50)]
    budget = estimate_tokens(TITLE_PAGE) + estimate_tokens(DATES_PAGE)
    selected = select_pages(pages, HEADER_KEYWORDS, token_budget=budget)
    assert [p.number for p in selected] == [1, 2]


def test_select_pages_truncates_a_huge_first_page():
    selected = select_pages([page(1, "x" * 500)], HEADER_KEYWORDS, token_budget=100)
    assert 350 < len(selected[0].text) <= 400
    assert estimate_tokens(selected[0].text) <= 100


def test_devanagari_costs_more_tokens():
    assert estimate_tokens("abcd" * 100) == 100
    assert estimate_tokens(HINDI_TEXT) > len(HINDI_TEXT) / 4


HINDI_DATES_PAGE = "महत्वपूर्ण तिथियाँ\nऑनलाइन आवेदन की अंतिम तिथि: 04-07-2026\nआवेदन शुल्क: 100 रुपये"
HINDI_POSTS_PAGE = "पद का नाम: कनिष्ठ सहायक\nकुल रिक्तियाँ: 120\nशैक्षिक योग्यता: स्नातक\nआयु सीमा: 18-40 वर्ष"


def test_hindi_keywords_score():
    assert score_page(HINDI_DATES_PAGE, HEADER_KEYWORDS) > score_page(HINDI_POSTS_PAGE, HEADER_KEYWORDS)
    assert score_page(HINDI_POSTS_PAGE, POSTS_KEYWORDS) > score_page(HINDI_DATES_PAGE, POSTS_KEYWORDS)


def test_hindi_first_notice_keeps_its_hindi_pages():
    # A state notice that is all Hindi except an English line in the letterhead.
    pages = [page(1, "Uttar Pradesh Subordinate Services Selection Commission"), page(2, HINDI_DATES_PAGE), page(3, HINDI_POSTS_PAGE)]
    assert [p.number for p in candidate_pages(pages)] == [1, 2, 3]
    assert [p.number for p in select_pages(pages, HEADER_KEYWORDS, token_budget=10_000)] == [1, 2, 3]  # page 3 has "आयु सीमा"
    assert [p.number for p in select_pages(pages, POSTS_KEYWORDS, token_budget=10_000)] == [1, 3]


def test_bilingual_notice_drops_its_hindi_half():
    pages = [page(1, TITLE_PAGE), page(2, HINDI_DATES_PAGE), page(3, DATES_PAGE), page(4, VACANCY_PAGE)]
    assert [p.number for p in candidate_pages(pages)] == [1, 3, 4]


def test_text_pages_split_at_paragraphs():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 100 for i in range(10))
    pages = text_pages(text, chunk_chars=1500)
    assert len(pages) > 1
    assert all(len(p.text) <= 1500 for p in pages)
    assert "".join(p.text for p in pages).count("Paragraph") == 10


def test_prepare_text_for_html_notices():
    text = "\n\n".join([TITLE_PAGE, SYLLABUS_PAGE, DATES_PAGE, VACANCY_PAGE])
    document = prepare_text(text, sha256="abc", source_url="https://x.gov.in/notice")
    assert document.sha256 == "abc" and document.header_pages
    assert "Important Dates" in document.header_pages[0].text


def test_default_budgets_fit_groq_free_tier():
    from sarkari_core.prompts import render
    from sarkari_crawler.extractors.chat import MAX_TOKENS
    from sarkari_crawler.pdf import POSTS_TOKEN_BUDGET

    header_call = estimate_tokens(render("header", "")) + HEADER_TOKEN_BUDGET + MAX_TOKENS["header"]
    posts_call = estimate_tokens(render("posts", "")) + POSTS_TOKEN_BUDGET + MAX_TOKENS["posts"]
    assert max(header_call, posts_call) < 8_000  # Groq free: 8,000 tokens per minute


def test_read_pages_from_real_pdf(pdf_factory):
    pages = read_pages(pdf_factory([TITLE_PAGE, ""]), ocr=False)
    assert [p.number for p in pages] == [1, 2]
    assert "Combined Graduate Level" in pages[0].text
    assert pages[0].is_english
    assert pages[1].text == "" and not pages[1].ocr


def test_prepare_document(pdf_factory):
    data = pdf_factory([TITLE_PAGE, SYLLABUS_PAGE, VACANCY_PAGE, DATES_PAGE])
    document = prepare_document(data, source_url="https://ssc.gov.in/x.pdf", ocr=False)
    assert document.sha256 == hashlib.sha256(data).hexdigest()
    assert document.source_url == "https://ssc.gov.in/x.pdf"
    assert [p.number for p in document.header_pages] == [1, 4]
    assert [p.number for p in document.posts_pages] == [1, 3]


def test_prepare_document_with_no_text(pdf_factory):
    document = prepare_document(pdf_factory(["", ""]), ocr=False)
    assert document.header_pages == [] and document.posts_pages == []
