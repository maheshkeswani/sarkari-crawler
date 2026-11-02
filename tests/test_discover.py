from sarkari_crawler.classify import NOISE, NOTIFICATION, UNCLASSIFIED
from sarkari_crawler.discover import extract_links, html_to_text

LISTING = """
<html><head><base href="https://psc.example.gov.in/notices/"></head><body>
<nav><a href="/recruitment">Recruitment</a><a href="/about">About us</a></nav>
<table id="notices">
  <tr><td>12/10/2026</td><td>Advertisement No. 05/2026 for Assistant Engineer (Civil) posts</td>
      <td><a href="docs/advt-05-2026.pdf">Download</a></td></tr>
  <tr><td>10/10/2026</td><td><a href="docs/result-cgl.pdf">Final result of Combined Exam 2025</a></td></tr>
  <tr><td>09/10/2026</td><td><a href="/view/notice?id=77">Recruitment of 120 Junior Assistants (Notice details)</a></td></tr>
  <tr><td>08/10/2026</td><td><a href="docs/advt-05-2026.pdf#page=2">Same file again</a></td></tr>
  <tr><td><a href="javascript:void(0)">Print</a> <a href="mailto:a@b.gov.in">Mail</a></td></tr>
</table>
<div class="other"><a href="https://elsewhere.gov.in/x.pdf">Annual report</a></div>
</body></html>
"""


def test_extract_links_finds_notices_in_page_order():
    links = extract_links(LISTING, "https://psc.example.gov.in/notices/index.html")
    assert [link.url for link in links] == [
        "https://psc.example.gov.in/notices/docs/advt-05-2026.pdf",
        "https://psc.example.gov.in/notices/docs/result-cgl.pdf",
        "https://psc.example.gov.in/view/notice?id=77",
        "https://elsewhere.gov.in/x.pdf",
    ]
    first, result, html_notice, other = links
    # "Download" is too short a label, so the table row's text is used.
    assert "Advertisement No. 05/2026" in first.text and first.classification == NOTIFICATION
    assert result.classification == NOISE
    assert html_notice.classification == NOTIFICATION
    assert first.url_normalized == "psc.example.gov.in/notices/docs/advt-05-2026.pdf"
    assert other.classification == NOISE  # "annual report"


def test_link_selector_narrows_the_search():
    links = extract_links(LISTING, "https://psc.example.gov.in/", link_selector="#notices a")
    assert "elsewhere.gov.in" not in " ".join(link.url for link in links)
    assert len(links) == 3


def test_unlabelled_pdf_is_kept_unclassified():
    links = extract_links('<a href="/f/123.pdf">Download</a>', "https://x.gov.in/")
    assert [(link.url, link.classification) for link in links] == [("https://x.gov.in/f/123.pdf", UNCLASSIFIED)]


def test_html_to_text_drops_navigation_and_scripts():
    html = """<html><body><nav>Home | About</nav><script>var x = 1;</script>
    <main><h1>Recruitment of Teachers 2026</h1><p>Last date: <b>15 Nov 2026</b></p></main>
    <footer>Copyright</footer></body></html>"""
    text = html_to_text(html)
    assert "Recruitment of Teachers 2026" in text and "15 Nov 2026" in text
    assert "Home" not in text and "var x" not in text and "Copyright" not in text
