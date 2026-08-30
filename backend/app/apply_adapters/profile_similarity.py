"""Tier B: semantic-similarity match against the workspace's own applicant-profile
fields — replaces the old per-workspace Q&A bank as the fuzzy-matching layer between
Tier A's exact-alias matching (fields.py) and the LLM tiers (llm_drafting.py,
resume_rag.py).

Why profile fields instead of a bank of past answers: a custom screening question
rarely recurs verbatim across different companies' forms, so a bank of previously-
seen questions paid for real review/maintenance friction without resolving much on
a *new* company's form. Your own profile answers, by contrast, are relevant to
*every* form that asks about the same underlying thing (work authorization, current
company, desired salary, ...) no matter how that company happens to phrase the
question — so widening Tier A's fixed-alias matching into embedding space, instead
of building a second matcher against a different corpus, is both simpler and
actually generalizes.

Deliberately includes EEO fields (gender/race_ethnicity/veteran_status/disability_
status) and every legal/compliance-sensitive field (work_authorized, visa_
sponsorship, security_clearance, background_check_consent, drug_test_consent,
criminal_history, citizenship): matching a fuzzily-worded question to a value you
already explicitly typed into your own profile isn't an inference or a guess, it's
the exact same trusted-value fill Tier A already does for every other field, just
via a fuzzier match. The hard line is drawn one step further down the chain
instead — llm_drafting.is_eeo_question/is_sensitive_question keep the *LLM* tiers
(resume_rag.py, llm_drafting.draft_educated_guess) from ever inventing an answer to
one of these when your own profile value for it is blank.
"""

import numpy as np

from ..config import get_settings
from ..embeddings import embed_text, embed_texts
from .fields import EEO_LABELS, TEXT_LABELS, YES_NO_LABELS
from .types import AnswerAttempt, QuestionDescriptor

# Every profile key eligible for this tier, each mapped to the same alias phrasings
# Tier A already searches for exactly — reused rather than duplicated so widening an
# alias list in fields.py automatically widens what this tier can fuzzy-match too.
_PROFILE_ALIASES: dict[str, list[str]] = {**TEXT_LABELS, **YES_NO_LABELS, **EEO_LABELS}


class _Corpus:
    """Every (profile_key, alias_text, embedding) triple, embedded once and cached
    for the life of the process — this corpus is static (derived from fields.py's
    alias tables, not from any one workspace's data), so there's no reason to ever
    recompute it per batch run the way qa_bank.py's per-workspace bank had to."""

    def __init__(self, keys: list[str], aliases: list[str], vectors: np.ndarray):
        self.keys = keys
        self.aliases = aliases
        self.vectors = vectors

    @classmethod
    def build(cls) -> "_Corpus":
        pairs = [(key, alias) for key, aliases in _PROFILE_ALIASES.items() for alias in aliases]
        keys = [key for key, _ in pairs]
        aliases = [alias for _, alias in pairs]
        vectors = np.asarray(embed_texts(aliases), dtype=float) if aliases else np.zeros((0, 0))
        return cls(keys, aliases, vectors)


_corpus: _Corpus | None = None


def _get_corpus() -> _Corpus:
    global _corpus
    if _corpus is None:
        _corpus = _Corpus.build()
    return _corpus


def match_profile_field(question: QuestionDescriptor, profile: dict) -> AnswerAttempt | None:
    """Returns the best-matching profile field's own stated value, or None if
    nothing clears the confidence/ambiguity bar, the matched field is blank, or
    (for select/radio) the stored value isn't one of this specific form's own live
    options — same refuse-rather-than-guess posture the old bank matcher used."""
    corpus = _get_corpus()
    if not corpus.keys:
        return None
    settings = get_settings()
    query_vec = np.asarray(embed_text(question.label), dtype=float)
    scores = corpus.vectors @ query_vec

    best_by_key: dict[str, float] = {}
    for key, score in zip(corpus.keys, scores):
        score = float(score)
        if score > best_by_key.get(key, -1.0):
            best_by_key[key] = score
    ranked = sorted(best_by_key.items(), key=lambda pair: pair[1], reverse=True)

    best_key, best_score = ranked[0]
    if best_score < settings.profile_match_threshold:
        return None
    if len(ranked) > 1:
        second_key, second_score = ranked[1]
        # Refuse rather than guess when two distinct profile fields are both
        # plausible matches and disagree — same posture as submit.py's tied-
        # candidate handling and the old QABankMatcher's ambiguity margin.
        if best_score - second_score < settings.profile_match_ambiguity_margin:
            return None

    value = (profile.get(best_key) or "").strip()
    if not value:
        return None  # nothing stated for this field yet -- not a match, fall through

    if question.field_type in ("select", "radio"):
        live_options = {opt.strip().lower() for opt in question.options}
        if value.strip().lower() not in live_options:
            return None

    return AnswerAttempt(value=value, source="profile", confidence=best_score)
