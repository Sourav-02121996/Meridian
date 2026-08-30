from dataclasses import dataclass, field
from typing import Callable, Literal

FieldType = Literal["text", "textarea", "select", "radio", "checkbox"]


@dataclass
class QuestionDescriptor:
    """Everything needed to match or draft an answer for one blocked required
    field — deliberately Playwright-free (no locator, no page) so it's safe to hand
    to non-apply_adapters code (routes, scheduler, the LLM drafting module) without
    leaking a live browser object past this package's boundary."""

    label: str
    field_type: FieldType
    # Populated for select/radio only: the literal, in-order option text the form
    # actually offers. A matched/drafted answer for these field types must be one of
    # these values verbatim — never coerced to a "close enough" option.
    options: list[str] = field(default_factory=list)


@dataclass
class AnswerAttempt:
    """A candidate answer to fill into a QuestionDescriptor's field, along with
    which tier produced it: "human_approved" (this exact job's own previously-
    approved JobBlockedQuestion answer — see scheduler._make_answer_lookup, tried
    before every other tier since it's a direct, explicit answer to this exact
    question, not an inference), "profile" (Tier B — semantic match against the
    workspace's own profile fields, see profile_similarity.py), "resume_rag"
    (Tier C — grounded LLM draft over retrieved résumé excerpts, see resume_rag.py),
    or "llm_guess" (Tier D — permissive LLM draft with no grounding requirement,
    see llm_drafting.draft_educated_guess). `confidence` is Tier B's cosine
    similarity score; left at 1.0 for every other source, which have no comparable
    scalar."""

    value: str
    source: Literal["human_approved", "profile", "resume_rag", "llm_guess"]
    confidence: float = 1.0


# Given one still-open page's blocked question, return an answer to try filling, or
# None to leave it unresolved. Runs while the browser is still open, so it's called
# once per unresolved field rather than in a tight loop — a Tier-B profile-similarity
# lookup is effectively free, but a caller chaining in a live LLM tier (see
# scheduler.py) means this can now block on a bounded-timeout network call per field.
# That's an accepted, deliberate tradeoff (see llm_drafting.py's module docstring for
# the policy this is chained with) — this hook itself still must never raise past its
# own bookkeeping; any failure must resolve to None.
AnswerLookup = Callable[[QuestionDescriptor], "AnswerAttempt | None"]


@dataclass
class AutoApplyResult:
    success: bool
    # One of: unsupported_multi_step, no_resume_file, custom_questions,
    # navigation_timeout, form_not_found, submit_not_found, fields_invalid_before_submit,
    # captcha_protected, submission_rejected, submission_request_failed,
    # listing_closed, confirmation_not_detected, unexpected_error. Kept specific
    # (rather than one catch-all "form_error") so a
    # batch's outcomes are diagnosable from the DB/logs without live reproduction.
    reason: str | None = None
    # Populated only when reason == "custom_questions": the blocked questions that
    # remained unresolved after every tier (profile similarity, résumé RAG, and the
    # educated-guess LLM tier, if enabled) was tried, so callers (scheduler.py) can
    # persist them for the user to answer instead of the text being discarded.
    unresolved_questions: list[QuestionDescriptor] = field(default_factory=list)
    # Sanitized response/error evidence from the submit step. Request bodies are
    # deliberately never included because they contain applicant PII.
    detail: str | None = None
