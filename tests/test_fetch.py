import httpx
import pytest

from sarkari_crawler.fetch import FetchError, Fetcher


def make_fetcher(routes: dict, calls: list | None = None, **kwargs):
    """routes: url -> httpx.Response, or a list of responses served in order."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        response = routes.get(url, httpx.Response(404))
        if isinstance(response, list):
            return response.pop(0)
        return response

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    sleeps = kwargs.pop("sleeps", [])
    clock = iter(float(i) for i in range(1000))
    return Fetcher(client=client, sleep=sleeps.append, clock=lambda: next(clock), jitter=lambda: 0.0, **kwargs)


PDF = httpx.Response(200, content=b"%PDF-1.7 fake", headers={"content-type": "application/pdf"})


def test_downloads_a_pdf():
    fetched = make_fetcher({"https://x.gov.in/a.pdf": PDF}).get("https://x.gov.in/a.pdf")
    assert fetched.is_pdf and not fetched.is_html and fetched.content.startswith(b"%PDF")


def test_pdf_detected_by_magic_bytes():
    response = httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/octet-stream"})
    assert make_fetcher({"https://x.gov.in/a": response}).get("https://x.gov.in/a").is_pdf


def test_respects_robots_txt():
    robots = httpx.Response(200, text="User-agent: *\nDisallow: /private/\n", headers={"content-type": "text/plain"})
    fetcher = make_fetcher({"https://x.gov.in/robots.txt": robots, "https://x.gov.in/a.pdf": PDF})
    assert fetcher.get("https://x.gov.in/a.pdf").is_pdf
    with pytest.raises(FetchError, match="robots.txt"):
        fetcher.get("https://x.gov.in/private/b.pdf")


def test_robots_txt_answered_with_a_web_page_allows_everything():
    page = httpx.Response(200, text="<html>Home</html>", headers={"content-type": "text/html"})
    fetcher = make_fetcher({"https://x.gov.in/robots.txt": page, "https://x.gov.in/a.pdf": PDF})
    assert fetcher.get("https://x.gov.in/a.pdf").is_pdf


def test_waits_between_requests_to_the_same_host():
    sleeps = []
    fetcher = make_fetcher({"https://x.gov.in/a.pdf": PDF, "https://x.gov.in/b.pdf": PDF}, sleeps=sleeps, min_host_delay=5.0)
    fetcher.get("https://x.gov.in/a.pdf")
    fetcher.get("https://x.gov.in/b.pdf")
    assert sleeps and all(s > 0 for s in sleeps)


def test_retries_server_errors_then_gives_up():
    calls = []
    url = "https://x.gov.in/a.pdf"
    fetcher = make_fetcher({url: [httpx.Response(503), httpx.Response(502), PDF]}, calls, respect_robots=False)
    assert fetcher.get(url).is_pdf
    assert calls.count(url) == 3

    fetcher = make_fetcher({url: [httpx.Response(503)] * 3}, respect_robots=False)
    with pytest.raises(FetchError, match="HTTP 503"):
        fetcher.get(url)


def test_client_errors_are_not_retried():
    calls = []
    fetcher = make_fetcher({}, calls, respect_robots=False)
    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher.get("https://x.gov.in/missing.pdf")
    assert len(calls) == 1


def test_size_limit():
    big = httpx.Response(200, content=b"%PDF" + b"x" * 2000, headers={"content-type": "application/pdf"})
    with pytest.raises(FetchError, match="byte limit"):
        make_fetcher({"https://x.gov.in/a.pdf": big}, respect_robots=False, max_bytes=1000).get("https://x.gov.in/a.pdf")
