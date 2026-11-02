"""Build extractors from a spec string, so switching provider is configuration, not code.

Spec: comma-separated "<provider>:<model>" entries, primary first, e.g.
    groq:<model>,openrouter:<vendor>/<model>:free
Only the first ":" separates provider from model; OpenRouter model ids contain ":" themselves.
"""

from __future__ import annotations

import os
from typing import Optional

from .chat import PROVIDERS, ChatExtractor, FallbackExtractor

EXTRACTORS_ENV = "SARKARI_EXTRACTORS"


def get_extractor(spec: Optional[str] = None, env: Optional[dict[str, str]] = None):
    env = os.environ if env is None else env
    spec = spec or env.get(EXTRACTORS_ENV, "")
    entries = [entry.strip() for entry in spec.split(",") if entry.strip()]
    if not entries:
        raise ValueError(f"no extractor configured: pass --extractor or set {EXTRACTORS_ENV}")

    extractors = []
    for entry in entries:
        provider_name, sep, model = entry.partition(":")
        if not sep or not model:
            raise ValueError(f"bad extractor spec {entry!r}, expected <provider>:<model>")
        provider = PROVIDERS.get(provider_name)
        if provider is None:
            raise ValueError(f"unknown provider {provider_name!r}; known: {', '.join(PROVIDERS)}")
        api_key = env.get(provider.api_key_env)
        if not api_key:
            raise ValueError(f"{provider.api_key_env} is not set (needed for {entry})")
        extractors.append(ChatExtractor(provider, model, api_key))

    return extractors[0] if len(extractors) == 1 else FallbackExtractor(extractors)


__all__ = ["ChatExtractor", "FallbackExtractor", "PROVIDERS", "get_extractor"]
