"""Tiny shared wrapper around the one sentence-transformers model this project
already loads for résumé/job-description scoring (see scorer.get_model()) — used
by every cosine-similarity matcher in the app (apply_adapters/profile_similarity.py,
resume_rag.py) so there's exactly one place that knows how to turn text into a
normalized embedding, instead of three modules each reimplementing the same two
lines slightly differently.
"""

import numpy as np

from .scorer import get_model


def embed_text(text: str) -> list[float]:
    """A single normalized embedding vector, as a plain list (JSON/DB-friendly)."""
    return embed_texts([text])[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch form of embed_text — encoding many strings in one model call is
    meaningfully faster than one-at-a-time for a résumé's worth of chunks or a
    profile-field alias corpus, and every caller here already has the full batch
    available upfront."""
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(list(texts), normalize_embeddings=True)
    return [np.asarray(vector, dtype=float).tolist() for vector in vectors]
