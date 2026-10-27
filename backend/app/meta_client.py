"""Client factory for the Meta Model API.

The API is OpenAI-compatible, so we use the openai SDK with a different base URL.
Meta's cookbook says the older official Meta client package is stale; do not use it.
"""

from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from .config import settings


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    if not settings.api_key:
        raise RuntimeError(
            "MODEL_API_KEY is not set. Copy backend/.env.example to backend/.env and add your key."
        )
    return AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key)
