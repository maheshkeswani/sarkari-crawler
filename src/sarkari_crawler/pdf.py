"""PDF or HTML notice → the pages worth sending to a text-only LLM.

1. Extract each page's text layer with PyMuPDF.
2. Pages with almost no text are scans; OCR them with Tesseract (eng+hin) if installed.
3. Bilingual notices repeat everything in English, so when there is enough English text
   the Hindi pages are dropped (Devanagari costs about twice the tokens). Hindi-first
   notices, common for state recruitments, keep all their pages.
4. Score the pages by keywords and keep the best ones within a token budget, separately
   for the header step and the posts step. The budgets keep each LLM call under Groq's
   free limit of 8,000 tokens per minute (prompt + page text + reply).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import shutil
from dataclasses import dataclass
from typing import Optional

import pymupdf

from sarkari_core.extractor import DocumentText, PageText

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 50  # fewer characters than this on a page means it's probably a scan
MIN_LATIN_SHARE = 0.3  # bilingual two-column pages sit around 0.5; Hindi-only pages near 0
# Below this share of English text, the notice is Hindi-first and every page is a candidate.
MIN_ENGLISH_TEXT_SHARE = 0.3
# Page text per call. The prompt adds about 1,000 tokens and the reply is capped
# separately (see extractors.chat), so each call stays under 8,000 tokens.
HEADER_TOKEN_BUDGET = 3_000
POSTS_TOKEN_BUDGET = 3_500
HTML_CHUNK_CHARS = 3_000  # an HTML notice is split into pseudo-pages of this size

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_LATIN = re.compile(r"[A-Za-z]")

# (pattern, weight). Patterns match case-insensitively, except ones containing \b,
# which are upper-case category abbreviations and match case-sensitively.
HEADER_KEYWORDS: list[tuple[str, int]] = [
    (r"important dates?", 3),
    (r"last date", 3),
    (r"closing date", 3),
    (r"application fee|fee payable|examination fee", 3),
    (r"how to apply|apply online", 2),
    (r"age limit|age as on|crucial date", 2),
    (r"advertisement no|advt\.? no|notice no", 2),
    (r"corrigendum|addendum|extension of", 3),
    (r"schedule", 1),
    # Hindi
    (r"अंतिम तिथि|अन्तिम तिथि|महत्वपूर्ण तिथि|महत्त्वपूर्ण तिथि", 3),
    (r"आवेदन शुल्क|परीक्षा शुल्क|शुल्क", 2),
    (r"शुद्धि ?पत्र|संशोधन|तिथि विस्तार", 3),
    (r"विज्ञापन सं|विज्ञापन क्रमांक", 2),
    (r"आयु सीमा", 2),
]
POSTS_KEYWORDS: list[tuple[str, int]] = [
    (r"vacanc", 3),
    (r"name of (the )?post|post name|post code", 3),
    (r"essential qualification|educational qualification|eligibility", 3),
    (r"age limit", 2),
    (r"pay level|pay scale|level-\d", 2),
    (r"\bUR\b|\bOBC\b|\bEWS\b|\bSC\b|\bST\b|\bPwBD\b", 1),
    # Hindi
    (r"रिक्ति|रिक्तियों|रिक्त पद", 3),
    (r"पद का नाम|पदनाम|पदों का विवरण", 3),
    (r"शैक्षिक योग्यता|शैक्षणिक योग्यता|अर्हता", 3),
    (r"आयु सीमा", 2),
    (r"वेतनमान|पे लेवल|वेतन", 2),
]


@dataclass(frozen=True)
class Page:
    number: int
    text: str
    ocr: bool
    latin_share: float

    @property
    def is_english(self) -> bool:
        return bool(self.text.strip()) and self.latin_share >= MIN_LATIN_SHARE


def estimate_tokens(text: str) -> int:
    """Rough token count: about 4 characters per token for English, 2 for Devanagari."""
    devanagari = len(_DEVANAGARI.findall(text))
    return math.ceil((len(text) - devanagari) / 4 + devanagari / 2)


def latin_share(text: str) -> float:
    """Share of Latin letters among Latin + Devanagari letters (0.0 for no letters)."""
    latin = len(_LATIN.findall(text))
    devanagari = len(_DEVANAGARI.findall(text))
    total = latin + devanagari
    return latin / total if total else 0.0


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _ocr_page(page: "pymupdf.Page") -> str:
    try:
        textpage = page.get_textpage_ocr(language="eng+hin", dpi=300, full=True)
        return page.get_text("text", textpage=textpage)
    except Exception as exc:  # PyMuPDF raises plain RuntimeError for tessdata problems
        log.warning("OCR failed on page %s: %s", page.number + 1, exc)
        return ""


def read_pages(pdf_bytes: bytes, ocr: Optional[bool] = None) -> list[Page]:
    """Return every page's text, OCR-ing scanned pages when Tesseract is installed."""
    if ocr is None:
        ocr = tesseract_available()
    pages = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text = page.get_text("text")
            used_ocr = False
            if len(text.strip()) < MIN_TEXT_CHARS and ocr:
                text = _ocr_page(page)
                used_ocr = True
            text = clean_text(text)
            pages.append(Page(page.number + 1, text, used_ocr, latin_share(text)))
    return pages


def score_page(text: str, keywords: list[tuple[str, int]]) -> int:
    score = 0
    for pattern, weight in keywords:
        flags = 0 if r"\b" in pattern else re.IGNORECASE
        score += weight * min(len(re.findall(pattern, text, flags)), 5)
    return score


def candidate_pages(pages: list[Page]) -> list[Page]:
    """English pages, or every page with text when the notice is mostly Hindi."""
    with_text = [p for p in pages if p.text.strip()]
    english = [p for p in with_text if p.is_english]
    english_chars = sum(len(p.text) for p in english)
    total_chars = sum(len(p.text) for p in with_text)
    if english and english_chars >= MIN_ENGLISH_TEXT_SHARE * total_chars:
        return english
    return with_text


def _truncate_to_tokens(text: str, token_budget: int) -> str:
    cut = int(len(text) * token_budget / max(estimate_tokens(text), 1))
    while cut > 0 and estimate_tokens(text[:cut]) > token_budget:
        cut -= max(1, cut // 20)
    return text[:cut]


def select_pages(pages: list[Page], keywords: list[tuple[str, int]], token_budget: int) -> list[Page]:
    """Pick candidate pages by keyword score within `token_budget`, returned in page order.

    The first candidate page is always kept: it carries the title and advertisement number.
    """
    candidates = candidate_pages(pages)
    if not candidates:
        return []
    chosen = [candidates[0]]
    used = estimate_tokens(candidates[0].text)
    ranked = sorted(candidates[1:], key=lambda p: score_page(p.text, keywords), reverse=True)
    for page in ranked:
        if score_page(page.text, keywords) == 0:
            break
        cost = estimate_tokens(page.text)
        if used + cost > token_budget:
            continue
        chosen.append(page)
        used += cost
    if used > token_budget:  # a single huge first page
        first = chosen[0]
        chosen[0] = Page(first.number, _truncate_to_tokens(first.text, token_budget), first.ocr, first.latin_share)
    return sorted(chosen, key=lambda p: p.number)


def _document_text(pages: list[Page], sha256: str, source_url: str, header_budget: int, posts_budget: int) -> DocumentText:
    def as_page_text(selected: list[Page]) -> list[PageText]:
        return [PageText(p.number, p.text, p.ocr) for p in selected]

    return DocumentText(
        header_pages=as_page_text(select_pages(pages, HEADER_KEYWORDS, header_budget)),
        posts_pages=as_page_text(select_pages(pages, POSTS_KEYWORDS, posts_budget)),
        sha256=sha256,
        source_url=source_url,
    )


def prepare_document(
    pdf_bytes: bytes,
    source_url: str = "",
    ocr: Optional[bool] = None,
    header_budget: int = HEADER_TOKEN_BUDGET,
    posts_budget: int = POSTS_TOKEN_BUDGET,
) -> DocumentText:
    pages = read_pages(pdf_bytes, ocr=ocr)
    scanned_without_text = [p.number for p in pages if not p.text and not p.ocr]
    if scanned_without_text:
        log.warning("Pages %s have no text layer and OCR is unavailable", scanned_without_text)
    return _document_text(pages, hashlib.sha256(pdf_bytes).hexdigest(), source_url, header_budget, posts_budget)


def text_pages(text: str, chunk_chars: int = HTML_CHUNK_CHARS) -> list[Page]:
    """Split plain text (an HTML notice) into pseudo-pages at paragraph breaks."""
    chunks, current = [], ""
    for paragraph in clean_text(text).split("\n\n"):
        if current and len(current) + len(paragraph) > chunk_chars:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return [Page(i, chunk, False, latin_share(chunk)) for i, chunk in enumerate(chunks, start=1)]


def prepare_text(
    text: str,
    sha256: str,
    source_url: str = "",
    header_budget: int = HEADER_TOKEN_BUDGET,
    posts_budget: int = POSTS_TOKEN_BUDGET,
) -> DocumentText:
    return _document_text(text_pages(text), sha256, source_url, header_budget, posts_budget)


def join_pages(pages: list[PageText]) -> str:
    return "\n\n".join(f"--- Page {p.number} ---\n{p.text}" for p in pages)
