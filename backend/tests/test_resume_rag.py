"""Tests for resume_rag.py's chunking and retrieval-then-draft pipeline. The LLM
call itself (llm_drafting.draft_from_context) is monkeypatched throughout — this
suite is only responsible for proving the chunk/retrieve/exclude logic around it,
not draft_from_context's own grounding behavior (covered in test_llm_drafting.py).
"""

from unittest.mock import patch

import app.resume_rag as rag
from app.apply_adapters.types import QuestionDescriptor

RESUME = """John Doe
Software Engineer

Experience
Backend Engineer at Acme Corp (2019-2023)
Built distributed systems in Python and Go.
Led a team of 4 engineers on the payments platform.

Frontend Engineer at Widgets Inc (2016-2019)
Built React applications for the marketing site.

Education
B.S. Computer Science, State University, 2016
"""


def test_chunk_resume_splits_on_blank_lines():
    chunks = rag.chunk_resume(RESUME)
    assert len(chunks) >= 4
    assert any("Backend Engineer at Acme Corp" in c for c in chunks)
    assert any("B.S. Computer Science" in c for c in chunks)
    # No chunk is just an artifact of stray whitespace.
    assert all(chunk.strip() for chunk in chunks)


def test_chunk_resume_splits_a_long_unbroken_block_by_line_windows():
    long_block = "\n".join(
        f"Bullet point number {i} about various accomplishments." for i in range(30)
    )
    chunks = rag.chunk_resume(long_block)
    assert len(chunks) > 1
    assert all(len(c) <= rag._MAX_CHUNK_CHARS for c in chunks)


def test_chunk_resume_respects_the_limit():
    long_block = "\n\n".join(f"Paragraph {i}" for i in range(200))
    chunks = rag.chunk_resume(long_block, limit=10)
    assert len(chunks) == 10


def test_draft_from_resume_rag_excludes_eeo_questions():
    question = QuestionDescriptor(label="Gender", field_type="text")
    with patch("app.resume_rag.draft_from_context") as mock_draft:
        assert rag.draft_from_resume_rag(question, RESUME) is None
    mock_draft.assert_not_called()


def test_draft_from_resume_rag_excludes_sensitive_questions():
    question = QuestionDescriptor(label="Do you require visa sponsorship?", field_type="text")
    with patch("app.resume_rag.draft_from_context") as mock_draft:
        assert rag.draft_from_resume_rag(question, RESUME) is None
    mock_draft.assert_not_called()


def test_draft_from_resume_rag_returns_none_for_empty_resume():
    question = QuestionDescriptor(label="Where did you go to school?", field_type="text")
    with patch("app.resume_rag.draft_from_context") as mock_draft:
        assert rag.draft_from_resume_rag(question, "   ") is None
    mock_draft.assert_not_called()


def test_draft_from_resume_rag_retrieves_the_most_relevant_chunk_and_drafts(monkeypatch):
    """Confirms retrieval actually narrows the LLM's context to the résumé
    excerpt(s) about education, not the whole résumé, for an education question."""
    question = QuestionDescriptor(label="Where did you get your degree?", field_type="text")

    def fake_retrieve(label, chunks):
        assert "school" in label.lower() or "degree" in label.lower()
        return [c for c in chunks if "B.S. Computer Science" in c]

    monkeypatch.setattr(rag, "_retrieve", fake_retrieve)
    with patch("app.resume_rag.draft_from_context", return_value="State University") as mock_draft:
        result = rag.draft_from_resume_rag(question, RESUME)

    assert result is not None
    assert result.value == "State University"
    assert result.source == "resume_rag"
    sent_context = mock_draft.call_args[0][1]
    assert "State University" in sent_context
    assert "Widgets Inc" not in sent_context  # the irrelevant chunk was left out


def test_draft_from_resume_rag_returns_none_when_nothing_retrieved(monkeypatch):
    monkeypatch.setattr(rag, "_retrieve", lambda label, chunks: [])
    question = QuestionDescriptor(label="What's your favorite color?", field_type="text")
    with patch("app.resume_rag.draft_from_context") as mock_draft:
        assert rag.draft_from_resume_rag(question, RESUME) is None
    mock_draft.assert_not_called()


def test_draft_from_resume_rag_returns_none_when_llm_draft_is_unknown(monkeypatch):
    monkeypatch.setattr(rag, "_retrieve", lambda label, chunks: chunks[:1])
    question = QuestionDescriptor(label="What's your favorite color?", field_type="text")
    with patch("app.resume_rag.draft_from_context", return_value=None):
        assert rag.draft_from_resume_rag(question, RESUME) is None
