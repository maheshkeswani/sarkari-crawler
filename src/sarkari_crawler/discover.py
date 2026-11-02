"""Find notice links on a source's listing page, and turn HTML notices into text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .classify import classify_link, is_candidate_link
from .urls import is_http_url, normalize_url

MAX_URL_LENGTH = 1000
MAX_TEXT_LENGTH = 500
_SHORT_LABEL_WORDS = 4  # "Download", "Click here", "English": use the row's text instead
_CONTEXT_TAGS = ("td", "div", "tr", "li", "p")
_ROW_TAGS = ("tr", "li", "p")  # don't look further up than the row the link sits in
_MAX_CONTEXT_CHARS = 300  # longer "context" is a page section, not a label
_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "#")


@dataclass(frozen=True)
class Link:
    url: str
    url_normalized: str
    text: str
    classification: str


def _label(anchor) -> str:
    text = anchor.get_text(" ", strip=True) or anchor.get("title", "") or ""
    if len(text.split()) >= _SHORT_LABEL_WORDS:
        return text
    for parent in anchor.parents:
        if parent.name not in _CONTEXT_TAGS:
            continue
        context = parent.get_text(" ", strip=True)
        if len(context) > _MAX_CONTEXT_CHARS:
            break
        if len(context) > len(text) and len(context.split()) >= _SHORT_LABEL_WORDS:
            return context
        if parent.name in _ROW_TAGS:
            break
    return text


def extract_links(html: Union[str, bytes], base_url: str, link_selector: Optional[str] = None) -> list[Link]:
    """Candidate notice links in page order, without duplicates.

    `link_selector` (a CSS selector from the source's parser_config) narrows the search
    to the notices table on pages full of unrelated links.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = soup.find("base", href=True)
    base_url = urljoin(base_url, base["href"]) if base else base_url
    anchors = soup.select(link_selector) if link_selector else soup.find_all("a", href=True)

    links, seen = [], set()
    for anchor in anchors:
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIP_SCHEMES):
            continue
        url = urljoin(base_url, href)
        if not is_http_url(url) or len(url) > MAX_URL_LENGTH:
            continue
        normalized = normalize_url(url)
        if normalized in seen:
            continue
        text = re.sub(r"\s+", " ", _label(anchor))[:MAX_TEXT_LENGTH]
        classification = classify_link(text, url)
        if not is_candidate_link(text, url, classification):
            continue
        seen.add(normalized)
        links.append(Link(url, normalized, text, classification))
    return links


def html_to_text(html: Union[str, bytes]) -> str:
    """The readable text of an HTML notice, without navigation, scripts or styles."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "iframe", "svg"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    lines = (line.strip() for line in main.get_text("\n").splitlines())
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text)
