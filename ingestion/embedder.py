"""Voyage embeddings.

Model is fixed in `.env` (VOYAGE_EMBEDDING_MODEL). Changing it means re-embedding
the entire corpus, so the model name is also recorded indirectly via the
DB column dimension (set at migration time).
"""
from __future__ import annotations

from functools import lru_cache

import voyageai

from shared.config import get_settings


@lru_cache(maxsize=1)
def _client() -> voyageai.Client:
    return voyageai.Client(api_key=get_settings().voyage_api_key)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed chunks for storage."""
    if not texts:
        return []
    s = get_settings()
    result = _client().embed(
        texts=texts,
        model=s.voyage_embedding_model,
        input_type="document",
    )
    return result.embeddings


def embed_query(query: str) -> list[float]:
    """Embed a search query. Different `input_type` for asymmetric models."""
    s = get_settings()
    result = _client().embed(
        texts=[query],
        model=s.voyage_embedding_model,
        input_type="query",
    )
    return result.embeddings[0]
