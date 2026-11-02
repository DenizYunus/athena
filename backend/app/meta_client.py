"""Client factory for the Meta Model API.

The API is OpenAI-compatible, so we use the openai SDK with a different base URL.
Meta's cookbook says the older official Meta client package is stale; do not use it.
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from openai import AsyncOpenAI

from .config import settings


@lru_cache(maxsize=8)
def _client_for(api_key: str) -> AsyncOpenAI:
    # The SDK default (600 s, two retries) outlasts the app, which gives up after 120 s and lets
    # the learner check again while the first call is still running.
    return AsyncOpenAI(
        base_url=settings.base_url,
        api_key=api_key,
        timeout=httpx.Timeout(settings.model_timeout_s, connect=10.0),
        max_retries=settings.model_max_retries,
    )


def get_client(api_key: str | None = None) -> AsyncOpenAI:
    """The server's key from .env, or a per-request key the mobile app sends in a header."""
    key = (api_key or "").strip() or settings.api_key
    if not key:
        raise RuntimeError(
            "No Meta API key. Set MODEL_API_KEY in backend/.env, or enter the key in the app's settings "
            "(sent as the X-Model-API-Key header)."
        )
    return _client_for(key)
