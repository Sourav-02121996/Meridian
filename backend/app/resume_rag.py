"""Tier C: résumé RAG. Chunks the workspace's résumé text, embeds each chunk once
(reusing the same sentence-transformers model already loaded for résumé/job-
description scoring — see embeddings.py — so no second embedding stack or vector
database is introduced), and retrieves only the handful of chunks most relevant to
one specific question before handing them to the LLM (llm_drafting.draft_from_
context) — rather than dumping the entire résumé into every prompt the way the
original draft_answer always has.

Why retrieval helps here even though a résumé easily fits in a local model's
context window whole: the win isn't fitting more text in, it's cutting *noise*
out. Confirmed by an existing, already-documented failure mode (see llm_drafting's
_PROFILE_CONTEXT_KEYS comment): handed the whole résumé, a small local model can
latch onto an unrelated section and answer a plausible-looking but wrong detail.
Retrieval scopes the model's attention to only the lines that are actually about
what's being asked, which is strictly narrower context than the full résumé, never
wider — so grounding, not new information, is what this buys.

Deliberately excludes EEO and legal/compliance-sensitive questions before ever
chunking/embedding anything — see llm_drafting.is_eeo_question/is_sensitive_
question and this module's own docstring cross-references for why.
"""

import re

import numpy as np

from .apply_adapters.types import AnswerAttempt, QuestionDescriptor
from .config import get_settings
from .embeddings import embed_text, embed_texts
from .llm_drafting import draft_from_context, is_eeo_question, is_sensitive_question

# Cap on both ends: a chunk shorter than this is almost always a stray blank line
# or a one-word section header with nothing retrievable in it on its own; longer
# than this and a single chunk starts covering more than one real idea (a whole
# job's worth of bullets in one block), which blunts retrieval's whole point of
# narrowing context down to just what's relevant.
_MIN_CHUNK_CHARS = 8
_MAX_CHUNK_CHARS = 500


def _split_long_paragraph(paragraph: str) -> list[str]:
    """A paragraph with no internal blank lines (common: a PDF-extracted résumé
    often collapses an entire "Experience" entry's bullets into one blank-line-
    delimited block) gets grouped back up by *line*, into windows capped at
    _MAX_CHUNK_CHARS, instead of staying one giant chunk that would swamp
    retrieval's per-chunk relevance scoring with everything at once."""
    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > _MAX_CHUNK_CHARS and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_resume(resume_text: str, limit: int = 80) -> list[str]:
    """Splits résumé text into paragraph-ish chunks: primarily on blank lines (the
    natural section/entry boundary PDF text extraction usually preserves — see
    routes/settings.py's PdfReader.extract_text() call), falling back to a
    line-window split for any paragraph that's still too long on its own. `limit`
    bounds the total so a pathologically unbroken résumé can't turn one apply
    attempt into an unbounded number of embedding calls."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", resume_text) if p.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= _MAX_CHUNK_CHARS:
            chunks.append(paragraph)
        else:
            chunks.extend(_split_long_paragraph(paragraph))
    return [c for c in chunks if len(c) >= _MIN_CHUNK_CHARS][:limit]


def _retrieve(question_label: str, chunks: list[str]) -> list[str]:
    settings = get_settings()
    query_vec = np.asarray(embed_text(question_label), dtype=float)
    vectors = np.asarray(embed_texts(chunks), dtype=float)
    scores = vectors @ query_vec
    ranked = sorted(zip(scores.tolist(), chunks), key=lambda pair: pair[0], reverse=True)
    return [
        chunk
        for score, chunk in ranked[: settings.resume_rag_top_k]
        if score >= settings.resume_rag_match_threshold
    ]


def draft_from_resume_rag(
    question: QuestionDescriptor, resume_text: str, profile: dict | None = None
) -> AnswerAttempt | None:
    """Tier C's actual entry point: chunk + retrieve + draft, or None if the
    question is excluded, the résumé is empty, nothing retrieved clears the
    relevance bar, or the grounded LLM draft itself comes back UNKNOWN. Never
    guesses — see llm_drafting.draft_from_context for the grounding rule this
    still enforces; a question with no real support in the résumé correctly falls
    through to Tier D (if enabled) or human review, not a fabricated answer here."""
    if is_eeo_question(question.label) or is_sensitive_question(question.label):
        return None
    if not resume_text.strip():
        return None
    chunks = chunk_resume(resume_text)
    if not chunks:
        return None
    retrieved = _retrieve(question.label, chunks)
    if not retrieved:
        return None
    context = "\n\n".join(retrieved)
    answer = draft_from_context(question, context, profile)
    if answer is None:
        return None
    return AnswerAttempt(value=answer, source="resume_rag")
