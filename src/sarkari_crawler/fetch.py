"""Polite HTTP fetching for government websites.

- Identifies itself with a clear user agent and honours robots.txt.
- Waits between requests to the same host (government sites slow down at exam peaks).
- Caps downloads at MAX_BYTES and retries transient failures with backoff.
- Many government sites serve incomplete certificate chains or need legacy TLS
  renegotiation. A source can opt into lenient TLS with parser_config
  {"verify_tls": false}: no certificate check, legacy renegotiation allowed. Use it only
  for sites that fail otherwise; the trade-off is that a network attacker could alter
  what we read from that site.
"""

from __future__ import annotations

import logging
import random
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

# Override with SARKARI_USER_AGENT to add a contact URL once the repo is public.
USER_AGENT = "SarkariJobsBot/0.2 (government job notification indexer)"
MAX_BYTES = 30 * 1024 * 1024
MIN_HOST_DELAY_SECONDS = 2.0
MAX_ATTEMPTS = 3


class FetchError(Exception):
    pass


def lenient_tls_context() -> ssl.SSLContext:
    """For old government servers: no certificate check, legacy renegotiation, older ciphers."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    return context


@dataclass(frozen=True)
class Fetched:
    url: str  # after redirects
    content_type: str
    content: bytes

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type or self.content.startswith(b"%PDF")

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type and not self.is_pdf


class Fetcher:
    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        insecure_client: Optional[httpx.Client] = None,
        user_agent: str = USER_AGENT,
        min_host_delay: float = MIN_HOST_DELAY_SECONDS,
        max_bytes: int = MAX_BYTES,
        respect_robots: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ):
        headers = {"User-Agent": user_agent, "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8"}
        timeout = httpx.Timeout(45.0, connect=15.0)
        self._client = client or httpx.Client(headers=headers, timeout=timeout, follow_redirects=True)
        self._insecure_client = insecure_client
        self._insecure_kwargs = {"headers": headers, "timeout": timeout, "follow_redirects": True, "verify": lenient_tls_context()}
        self._user_agent = user_agent
        self._min_host_delay = min_host_delay
        self._max_bytes = max_bytes
        self._respect_robots = respect_robots
        self._sleep, self._clock, self._jitter = sleep, clock, jitter
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, Optional[robotparser.RobotFileParser]] = {}

    def get(self, url: str, verify_tls: bool = True) -> Fetched:
        client = self._client_for(verify_tls)
        if self._respect_robots and not self._allowed(client, url):
            raise FetchError(f"robots.txt disallows {url}")
        last_problem = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_host(url)
            try:
                return self._download(client, url)
            except httpx.TransportError as exc:
                last_problem = f"network error: {exc}"
            except _RetryableStatus as exc:
                last_problem = str(exc)
            if attempt < MAX_ATTEMPTS:
                self._sleep(3.0 * attempt)
        raise FetchError(f"{url}: {last_problem}")

    def _client_for(self, verify_tls: bool) -> httpx.Client:
        if verify_tls:
            return self._client
        if self._insecure_client is None:
            self._insecure_client = httpx.Client(**self._insecure_kwargs)
        return self._insecure_client

    def _download(self, client: httpx.Client, url: str) -> Fetched:
        with client.stream("GET", url) as response:
            if response.status_code >= 500 or response.status_code == 429:
                raise _RetryableStatus(f"HTTP {response.status_code}")
            if response.status_code != 200:
                raise FetchError(f"{url}: HTTP {response.status_code}")
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self._max_bytes:
                raise FetchError(f"{url}: {declared} bytes is over the {self._max_bytes} byte limit")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self._max_bytes:
                    raise FetchError(f"{url}: over the {self._max_bytes} byte limit")
                chunks.append(chunk)
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            return Fetched(str(response.url), content_type, b"".join(chunks))

    def _wait_for_host(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        last = self._last_request.get(host)
        if last is not None:
            wait = self._min_host_delay + self._jitter() - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _allowed(self, client: httpx.Client, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(client, origin)
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(self._user_agent, url)

    def _load_robots(self, client: httpx.Client, origin: str) -> Optional[robotparser.RobotFileParser]:
        """A missing or unreadable robots.txt allows everything (the common convention)."""
        try:
            self._wait_for_host(origin)
            response = client.get(f"{origin}/robots.txt")
        except httpx.HTTPError as exc:
            log.info("robots.txt unavailable for %s: %s", origin, exc)
            return None
        if response.status_code != 200 or "html" in response.headers.get("content-type", ""):
            return None  # many sites answer robots.txt with their home page
        parser = robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser


class _RetryableStatus(Exception):
    pass
