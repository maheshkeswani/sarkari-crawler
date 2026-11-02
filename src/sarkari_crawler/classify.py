"""Keyword rules that decide what is worth an LLM call, before spending any quota.

Applied to a link's text and URL when it's found, and again to the downloaded text
when the link alone wasn't conclusive. Results, admit cards, answer keys, tenders and
syllabi are noise; only notifications and corrigenda go to the LLM.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

NOTIFICATION = "NOTIFICATION"
CORRIGENDUM = "CORRIGENDUM"
NOISE = "NOISE"
UNCLASSIFIED = "UNCLASSIFIED"

_NOISE = re.compile(
    "|".join(
        [
            r"\bresults?\b",
            r"merit list",
            r"select(ion|ed)? list",
            r"waiting list",
            r"provisional list",
            r"rejection list",
            r"answer key",
            r"admit card",
            r"e-?admit",
            r"call letter",
            r"hall ticket",
            r"cut[- ]?off",
            r"\bmarks\b",
            r"score ?card",
            r"interview (schedule|programme|program)",
            r"(document verification|dv|pet|pst|physical test|skill test) schedule",
            r"syllabus",
            r"tender",
            r"quotation",
            r"expression of interest",
            r"\beoi\b",
            r"\brti\b",
            r"recruitment rules",
            r"annual report",
            r"roll numbers?",
            r"objections?",
            r"selection panel",
            r"skill test",
            r"shortlist",
            r"list of (eligible|ineligible|qualified|selected|candidates)",
            r"provisionally (selected|allotted)",
            r"dashboard",
            r"interview (letter|schedule|call|programme|program|date)",
            r"viva[- ]?voce",
            r"admission certificate",
            r"screening test",
            r"press (release|note)",
            r"appointment letter",
            r"document (upload|verification)",
            r"preference",
            r"absentee",
            r"schedule of (the )?(interview|exam|examination|test)",
            r"service rules",
            r"\brules,? (19|20)\d\d",
            r"\(amendment\) rules",
            r"mock test",
            r"question papers?",
            r"answer (booklet|sheet)",
            r"calendar",
            r"annexures?",
            r"postpone",
            r"re-?schedul",
            r"opt[- ]?out",
            r"completion of (the )?recruitment",
            r"deficiency of documents",
            r"empan[ae]ll?ed",  # sites misspell it
            r"dearness allowance|महंगाई भत्त",
            # Hindi
            r"परिणाम",
            r"प्रवेश पत्र",
            r"उत्तर कुंजी",
            r"निविदा",
            r"पाठ्यक्रम",
            r"साक्षात्कार कार्यक्रम",
            r"चयन सूची",
            r"प्रतीक्षा सूची",
        ]
    ),
    re.IGNORECASE,
)

_CORRIGENDUM = re.compile(
    "|".join(
        [
            r"corrigend",
            r"addend",
            r"extension of (the )?(last|closing) date",
            r"(last|closing) date (is |has been )?extended",
            r"date extended",
            r"re-?open",
            r"amendment",
            r"\bcancell?(ed|ation)\b",
            # Hindi
            r"शुद्धि ?पत्र",
            r"संशोधन",
            r"तिथि (में )?विस्तार",
            r"निरस्त",
        ]
    ),
    re.IGNORECASE,
)

_NOTIFICATION = re.compile(
    "|".join(
        [
            r"recruit",
            r"advertisement",
            r"\badvt\b",
            r"vacanc",
            r"notification",
            r"employment notice",
            r"apply online",
            r"applications? (are )?invited",
            r"invites? (online )?applications?",
            r"\bbharti\b",
            r"\bposts? of\b",
            # Hindi
            r"भर्ती",
            r"विज्ञापन",
            r"रिक्ति",
            r"आवेदन आमंत्रित",
            r"आवेदन पत्र आमंत्रित",
        ]
    ),
    re.IGNORECASE,
)


# Links that announce a recruitment but lead to an application portal or a login, not a notice.
_PORTAL = re.compile(
    r"apply online|apply here|click here to apply|log ?in|sign ?in|registration|register|helpdesk|faq",
    re.IGNORECASE,
)


def _url_words(url: str) -> str:
    path = unquote(urlsplit(url).path)
    return re.sub(r"[_\-/.+]+", " ", path)


def classify(text: str) -> str:
    """Classify short text such as a link's label. Noise wins over everything else."""
    if _NOISE.search(text):
        return NOISE
    if _CORRIGENDUM.search(text):
        return CORRIGENDUM
    if _NOTIFICATION.search(text):
        return NOTIFICATION
    return UNCLASSIFIED


def classify_link(text: str, url: str) -> str:
    """The link text decides. A PDF's file name fills in when the text says nothing; an HTML
    link's path doesn't (".../notifications?page=9" says nothing about the link)."""
    by_text = classify(text)
    if by_text != UNCLASSIFIED or not is_pdf_url(url):
        return by_text
    return classify(_url_words(url))


def is_pdf_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def is_candidate_link(text: str, url: str, classification: str) -> bool:
    """Worth storing: every PDF (noise is stored too, so it's never looked at again), and
    HTML pages whose label clearly announces a recruitment. Short labels like a
    "Recruitment" menu item are skipped; they lead to listing pages, not notices."""
    if is_pdf_url(url):
        return True
    return (
        classification in (NOTIFICATION, CORRIGENDUM)
        and len(text.split()) >= 4
        and not _PORTAL.search(text)
    )


def classify_text(text: str) -> str:
    """Classify a downloaded document by its opening text (the title area) and body.

    Notices mention results and admit cards in passing ("admit cards will be issued"),
    so noise counts only in the opening lines, and only without recruitment words there.
    """
    head = text[:800]
    if _CORRIGENDUM.search(head):
        return CORRIGENDUM
    if _NOISE.search(head) and not _NOTIFICATION.search(head):
        return NOISE
    if len(_NOTIFICATION.findall(text[:6000])) >= 2:
        return NOTIFICATION
    return UNCLASSIFIED
