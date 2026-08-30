"""Pure-Python tests for profile_similarity.match_profile_field — deliberately
injects fake embedding vectors rather than calling the real sentence-transformers
model (same approach the old QABankMatcher tests used), so this suite runs fast and
never needs the model download. Vectors are hand-chosen purely to control cosine
similarity precisely; they carry no semantic meaning of their own.
"""

import pytest

import app.apply_adapters.profile_similarity as ps
from app.apply_adapters.types import QuestionDescriptor
from app.config import get_settings


@pytest.fixture(autouse=True)
def _reset_corpus_cache():
    """The alias corpus is built once and cached at module scope (it's static,
    derived from fields.py, not per-workspace) — but that means a stale corpus
    built under one test's monkeypatched embeddings would leak into the next
    test. Force a rebuild every test."""
    ps._corpus = None
    yield
    ps._corpus = None


def _patch_embeddings(monkeypatch, vector_by_text: dict, default=(0.0, 0.0, 1.0)):
    monkeypatch.setattr(ps, "embed_text", lambda text: list(vector_by_text.get(text, default)))
    monkeypatch.setattr(
        ps, "embed_texts", lambda texts: [list(vector_by_text.get(t, default)) for t in texts]
    )


def test_matches_a_known_field_via_alias(monkeypatch):
    query = "Will you need sponsorship now or later?"
    _patch_embeddings(
        monkeypatch,
        {
            "sponsorship now or in the future": (1.0, 0.0, 0.0),
            query: (1.0, 0.0, 0.0),
        },
    )
    attempt = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="text"), {"visa_sponsorship": "No"}
    )
    assert attempt is not None
    assert attempt.value == "No"
    assert attempt.source == "profile"


def test_returns_none_below_threshold(monkeypatch):
    query = "Totally unrelated question"
    # The query gets its own distinct vector, orthogonal to every alias's default —
    # every alias in the corpus scores 0 against it.
    _patch_embeddings(monkeypatch, {query: (1.0, 0.0, 0.0)})
    attempt = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="text"), {"visa_sponsorship": "No"}
    )
    assert attempt is None


def test_returns_none_when_two_distinct_fields_are_ambiguous(monkeypatch):
    query = "Are you willing to relocate or travel for this role?"
    settings = get_settings()
    # Two different profile keys' aliases both score just barely apart -- well
    # inside the ambiguity margin -- so this must refuse even though open_to_
    # relocation happens to also be a "Yes"/"No"-shaped field.
    _patch_embeddings(
        monkeypatch,
        {
            "willing to relocate": (1.0, 0.0, 0.0),
            "18 years": (1.0 - settings.profile_match_ambiguity_margin / 2, 0.01, 0.0),
            query: (1.0, 0.0, 0.0),
        },
    )
    attempt = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="text"),
        {"willing_to_relocate": "Yes", "is_18_or_older": "Yes"},
    )
    assert attempt is None


def test_returns_none_when_matched_profile_field_is_blank(monkeypatch):
    query = "What's your current company?"
    _patch_embeddings(monkeypatch, {"Current Company": (1.0, 0.0, 0.0), query: (1.0, 0.0, 0.0)})
    attempt = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="text"), {"current_company": ""}
    )
    assert attempt is None


def test_select_requires_answer_among_live_options(monkeypatch):
    query = "What country are you located in?"
    _patch_embeddings(monkeypatch, {"Country": (1.0, 0.0, 0.0), query: (1.0, 0.0, 0.0)})
    rejected = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="select", options=["Canada", "Mexico"]),
        {"country": "United States"},
    )
    accepted = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="select", options=["United States", "Canada"]),
        {"country": "United States"},
    )
    assert rejected is None
    assert accepted is not None and accepted.value == "United States"


def test_eeo_field_is_matched_like_any_other_profile_field(monkeypatch):
    """Deliberate: Tier B fuzzy-matches EEO fields too, since filling in a value
    the candidate already explicitly typed into their own profile is never a
    guess — see profile_similarity.py's module docstring."""
    query = "What is your gender identity?"
    _patch_embeddings(monkeypatch, {"Gender": (1.0, 0.0, 0.0), query: (1.0, 0.0, 0.0)})
    attempt = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="text"), {"gender": "Non-binary"}
    )
    assert attempt is not None
    assert attempt.value == "Non-binary"


def test_sensitive_field_is_matched_like_any_other_profile_field(monkeypatch):
    """Same reasoning as the EEO case above: work_authorized/security_clearance/
    etc. are matched by Tier B when your profile already has a stated value —
    it's only the LLM tiers (llm_drafting.is_sensitive_question) that are
    permanently excluded from ever inventing one of these from nothing."""
    query = "Do you currently hold an active security clearance?"
    _patch_embeddings(monkeypatch, {"Security Clearance": (1.0, 0.0, 0.0), query: (1.0, 0.0, 0.0)})
    attempt = ps.match_profile_field(
        QuestionDescriptor(label=query, field_type="text"), {"security_clearance": "None"}
    )
    assert attempt is not None
    assert attempt.value == "None"
