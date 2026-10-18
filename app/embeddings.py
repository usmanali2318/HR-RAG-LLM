"""Gemini embeddings. Documents and queries use different task types, and vectors are L2-normalised
(only the full 3072-dim output is pre-normalised by the API, so we normalise reduced sizes ourselves)."""
import math
import time

from google import genai
from google.genai import types

from .config import settings

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    last = None
    for attempt in range(4):
        try:
            resp = _get_client().models.embed_content(
                model=settings.embed_model,
                contents=texts,
                config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=settings.embed_dim),
            )
            return [_normalise(list(e.values)) for e in resp.embeddings]
        except Exception as exc:  # rate limits and transient errors
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Embedding request failed: {last}")


def embed_documents(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        out.extend(_embed(texts[i:i + batch_size], "RETRIEVAL_DOCUMENT"))
        if i + batch_size < len(texts):
            time.sleep(1)
    return out


def embed_query(text: str) -> list[float]:
    return _embed([text], "RETRIEVAL_QUERY")[0]
