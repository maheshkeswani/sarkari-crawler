"""URL normalisation, so the same notice found twice is stored once.

The normalised form drops the scheme, "www.", fragments, tracking parameters and a
trailing slash, and sorts the query. Path case is kept: some servers care about it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

_TRACKING_PARAMS = {"fbclid", "gclid"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port != _DEFAULT_PORTS.get(parts.scheme) else ""
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
        )
    )
    return f"{host}{port}{path}" + (f"?{query}" if query else "")


def is_http_url(url: str) -> bool:
    return urlsplit(url).scheme in ("http", "https") and bool(urlsplit(url).hostname)
