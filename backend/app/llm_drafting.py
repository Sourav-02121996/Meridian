"""LLM-backed answer drafting for application questions, via a locally-running
Ollama instance (free — no API budget needed, since it runs on the user's own
machine). Three different functions, used at different points in the tier chain
(see scheduler.py's answer_lookup and apply_adapters/engine.py's fill_and_submit):

  - draft_answer: the original grounded-in-the-full-résumé drafter. Kept exactly as
    it always behaved (still used directly by a couple of call sites/tests) —
    UNKNOWN whenever the résumé/profile doesn't actually contain the answer.
  - draft_from_context (Tier C): the same grounded, "UNKNOWN is fine" policy as
    draft_answer, but grounded in a caller-supplied context string (resume_rag.py's
    retrieved résumé excerpts) instead of the entire résumé — see resume_rag.py's
    module docstring for why retrieval-then-draft beats dumping the whole résumé in
    for a small local model.
  - draft_educated_guess (Tier D): a deliberately more permissive drafter for
    subjective/motivational questions ("Why do you want to join Twitch?", "How many
    years of experience do you have?") that draft_answer/draft_from_context will
    always return UNKNOWN for, since there's nothing in a résumé to ground them in.
    Gated behind its own `llm_educated_guess_enabled` setting, separate from
    `llm_answer_drafting_enabled` — a materially bigger risk decision than "let the
    LLM draft grounded answers" (see the module-level exclusion list below), so it
    isn't silently bundled into a flag someone may have already turned on for the
    safer tiers.

Hard rules, enforced here rather than left to the prompt alone (so they hold
regardless of which call site is used):
  - draft_answer/draft_from_context are grounded strictly in the résumé/context
    text — anything the model can't answer truthfully from that text must come back
    as UNKNOWN, which this module treats identically to any other failure: no draft.
  - EEO self-identification questions (race, gender, veteran status, disability)
    are never drafted by ANY of the three functions, at any confidence — a résumé
    has no factual basis to infer these from, so answering one isn't a "guess",
    it's inventing a candidate's protected-class self-identification from nothing.
    These stay profile-driven only (Tier A/B, via fields.py's EEO_LABELS) or fall
    to human review.
  - The same permanent exclusion applies to every legal/compliance-sensitive
    question (work authorization, visa sponsorship, security clearance, background
    check/drug test consent, criminal history, citizenship) — see
    is_sensitive_question. A wrong guess on one of these has real legal/employment
    consequences that a wrong guess on "why do you want to join this company"
    doesn't; unlike EEO, these numbers/answers already have workspace profile
    fields backing them (Tier A/B), so the *only* thing permanently excluded here
    is the LLM ever inventing one from nothing when your own profile is blank.
"""

import logging

import httpx

from .apply_adapters import QuestionDescriptor, canonicalize_country
from .apply_adapters.fields import EEO_LABELS, TEXT_LABELS, YES_NO_LABELS
from .config import get_settings
from .embeddings import embed_text
from .models import JobBlockedQuestion, Workspace

log = logging.getLogger("meridian.llm_drafting")

_TIMEOUT_SECONDS = 20.0
_MAX_ANSWER_LENGTH = 500
# Tier D's free-text answers run a little longer than a grounded one-liner (a
# genuine "why this company" answer is naturally a few sentences) — confirmed
# live: a hard cut at _MAX_ANSWER_LENGTH sliced a real Ollama response off
# mid-sentence ("...I appreciate the com"), which would submit a visibly broken
# sentence to a real employer. Given its own, longer budget, then trimmed at a
# sentence boundary (see _truncate_at_sentence) instead of an arbitrary character.
_MAX_GUESS_ANSWER_LENGTH = 700

# Flattened, lowercased keyword list from EEO_LABELS' own label aliases (fields.py)
# — reused here so "is this an EEO question" recognizes the same phrasing already
# trusted for Tier A's known-field matching, even on a custom-labeled question our
# fixed alias list doesn't otherwise match verbatim.
_EEO_KEYWORDS = [alias.lower() for aliases in EEO_LABELS.values() for alias in aliases]

# Every profile key whose real-world answer carries legal/compliance weight, kept
# permanently out of both LLM drafting tiers (see module docstring) — matched by
# the same alias phrasings fields.py already recognizes for Tier A/profile_
# similarity.py's Tier B, so a custom phrasing of one of these is caught here too,
# not just the exact alias strings.
_SENSITIVE_PROFILE_KEYS = {
    "work_authorized",
    "visa_sponsorship",
    "security_clearance",
    "background_check_consent",
    "drug_test_consent",
    "criminal_history",
    "citizenship",
}
_SENSITIVE_KEYWORDS = [
    alias.lower()
    for key in _SENSITIVE_PROFILE_KEYS
    for alias in (TEXT_LABELS.get(key, []) + YES_NO_LABELS.get(key, []))
]

# Workspace-profile keys worth handing the model as trusted context, distinct from
# résumé prose it otherwise has to infer from — confirmed live on a bare "Country*"
# field (Twitch) and "current country of residence" (GitLab): with no profile
# context at all, the model latched onto some unrelated résumé detail and answered
# a plausible-looking but wrong country, rather than just reading the candidate's
# own stated location. Deliberately excludes EEO fields (gender/race/veteran/
# disability), the sensitive fields above, and free-text cover_letter — those never
# belong in this prompt regardless of what the caller's profile dict happens to
# contain, so this whitelist is the single source of truth for what actually
# reaches Ollama, not something every caller has to remember to filter themselves.
_PROFILE_CONTEXT_KEYS = [
    "location",
    "city",
    "state",
    "country",
    "current_company",
    "current_title",
    "work_authorized",
    "visa_sponsorship",
    "willing_to_relocate",
    "start_date",
]

_SYSTEM_PROMPT = (
    "You are drafting ONE short answer to a job application screening question on "
    "behalf of a candidate. Only use facts explicitly present in the résumé text or "
    "the Candidate Profile provided below. If a Candidate Profile section is "
    "present, it contains facts the candidate typed in themselves — trust it over "
    "any inference you might otherwise draw from the résumé's prose for anything "
    "it covers. A question asking specifically for a country (e.g. 'country of "
    "residence', 'which country will you be located in') must be answered with "
    "ONLY the country name itself (e.g. 'United States'), derived from the "
    "profile's Location field — never the full city/state/address, and never "
    "guessed from an unrelated résumé detail like a school, past employer, or "
    "name. If neither the résumé nor the profile contains enough information to "
    "answer truthfully, respond with exactly UNKNOWN and nothing else. Never invent employer names, dates, "
    "degrees, or skills not present in either. For a multiple-choice question, "
    "respond with exactly one of the given options, verbatim, and nothing else. "
    'For a yes/no or checkbox-style question, respond with exactly "Yes" or "No" '
    "and nothing else."
)

# Tier D's own system prompt: same "never invent a hard, checkable fact" boundary
# as _SYSTEM_PROMPT, but UNKNOWN is deliberately not offered as an escape hatch —
# the entire point of this tier is to always produce a genuine best-effort answer
# for exactly the questions (motivational/subjective, or "pick the best of these
# offered options") that _SYSTEM_PROMPT's grounding requirement guarantees would
# otherwise come back UNKNOWN every time.
_GUESS_SYSTEM_PROMPT = (
    "You are drafting ONE answer to a job application screening question, on behalf "
    "of a candidate applying for {job_title} at {job_company}. Aim for an "
    "answer a recruiter reading this specific application would find genuine, "
    "relevant, and well-suited to this role — grounded in the résumé and Candidate "
    "Profile below wherever they're relevant, but where they don't fully answer the "
    "question, make the single most reasonable, favorable inference a strong, "
    "genuine candidate for this exact role would honestly give. Never invent a "
    "specific employer, job title, academic degree, certification, or tool/"
    "technology that isn't evidenced in the résumé or profile — a fabricated, "
    "checkable fact is never acceptable, but a genuine best-effort answer to a "
    "subjective or open-ended question always is. You must always give your best "
    "answer — UNKNOWN is never an acceptable response from you, no matter how "
    "little the résumé says about it. Keep a free-text answer to 2-4 sentences, "
    "and always end on a complete sentence. For a multiple-choice question, respond with "
    "exactly one of the given options, verbatim, and nothing else — if none seems "
    "like a perfect fit, pick whichever is the most reasonable choice anyway, "
    "never leave it unanswered. For a yes/no or checkbox-style question, respond "
    'with exactly "Yes" or "No" and nothing else.'
)


def is_eeo_question(label: str) -> bool:
    """True if `label` is (or looks like) an EEO self-identification question —
    race/ethnicity, gender, veteran status, or disability — regardless of field
    type or exact phrasing. See module docstring for why these are never drafted."""
    lowered = label.lower()
    return any(keyword in lowered for keyword in _EEO_KEYWORDS)


def is_sensitive_question(label: str) -> bool:
    """True if `label` looks like a legal/compliance-sensitive question (work
    authorization, visa sponsorship, security clearance, background check/drug
    test consent, criminal history, citizenship) — see module docstring for why
    these are excluded from both LLM tiers exactly like EEO questions are, even
    though they aren't protected-class self-identification."""
    lowered = label.lower()
    return any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS)


def _build_profile_context(profile: dict | None) -> str:
    if not profile:
        return ""
    lines = [
        f"- {key.replace('_', ' ').capitalize()}: {profile[key]}"
        for key in _PROFILE_CONTEXT_KEYS
        if (profile.get(key) or "").strip()
    ]
    return "\n".join(lines)


def _build_prompt(
    question: QuestionDescriptor, resume_text: str, profile: dict | None = None
) -> str:
    return _build_prompt_generic(question, "Résumé", resume_text, profile)


def _build_prompt_generic(
    question: QuestionDescriptor,
    context_label: str,
    context_text: str,
    profile: dict | None = None,
    job_context: str = "",
) -> str:
    parts = []
    if job_context:
        parts.append(f"{job_context}\n")
    parts.append(f"{context_label}:\n{context_text.strip()}\n")
    profile_context = _build_profile_context(profile)
    if profile_context:
        parts.append(
            f"Candidate Profile (explicitly provided by the candidate):\n{profile_context}\n"
        )
    parts.append(f"Question: {question.label}")
    if question.options:
        parts.append(
            "Options (respond with exactly one of these, verbatim): " + " | ".join(question.options)
        )
    return "\n".join(parts)


def _postprocess(content: str, question: QuestionDescriptor) -> str | None:
    text = content.strip()
    if not text or text.upper() == "UNKNOWN":
        return None
    if question.field_type in ("select", "radio"):
        for option in question.options:
            if option.strip().lower() == text.lower():
                return option  # normalize to the form's own exact casing/spelling
        return None  # never accept a paraphrase for a field that must pick a literal option
    if question.field_type == "checkbox":
        # _fill_answer's checkbox branch (apply_adapters/fields.py) only understands
        # this small truthy vocabulary — anything else would silently uncheck a box
        # that should've been checked (or vice versa), so reject rather than guess.
        return text if text.strip().lower() in ("yes", "true", "agree", "checked", "no") else None
    return text[:_MAX_ANSWER_LENGTH]


def _call_ollama(system_prompt: str, user_prompt: str) -> str | None:
    """Shared network call for every drafting function in this module. Returns the
    raw model response text, or None on absolutely any failure — connection
    refused, timeout, malformed response, anything. Callers post-process the raw
    text themselves (grounded vs. permissive tiers accept different things)."""
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except Exception:
        log.info("LLM call unavailable — falling back to plain review")
        return None


def draft_answer(
    question: QuestionDescriptor, resume_text: str, profile: dict | None = None
) -> str | None:
    """Best-effort local LLM draft for one question, grounded in the entire résumé
    text. `profile` is optional (existing callers that don't have one yet still
    work, just without the disambiguating context) — pass the same profile dict
    scheduler.py already builds via _build_profile() for anything beyond a bare
    résumé. Returns None on absolutely any failure — feature disabled, Ollama not
    running, connection refused, timeout, malformed response, UNKNOWN, an EEO/
    sensitive question, or an answer that fails post-validation. Callers MUST treat
    None identically to "no draft available" and MUST NOT let an exception here
    escape into the batch run."""
    settings = get_settings()
    if not settings.llm_answer_drafting_enabled:
        return None
    if is_eeo_question(question.label) or is_sensitive_question(question.label):
        return None  # policy carve-out — never drafted, regardless of confidence
    if not resume_text.strip():
        return None
    content = _call_ollama(_SYSTEM_PROMPT, _build_prompt(question, resume_text, profile))
    if content is None:
        return None
    return _postprocess(content, question)


def draft_from_context(
    question: QuestionDescriptor, context_text: str, profile: dict | None = None
) -> str | None:
    """Tier C: the same grounded, "UNKNOWN is fine" policy as draft_answer, but
    grounded in a caller-supplied context string — resume_rag.py's retrieved
    résumé excerpts most relevant to this specific question, instead of the whole
    résumé. Gated behind the same `llm_answer_drafting_enabled` setting as
    draft_answer (this is still the grounded tier, not the permissive one)."""
    settings = get_settings()
    if not settings.llm_answer_drafting_enabled:
        return None
    if is_eeo_question(question.label) or is_sensitive_question(question.label):
        return None
    if not context_text.strip():
        return None
    prompt = _build_prompt_generic(question, "Relevant résumé excerpts", context_text, profile)
    content = _call_ollama(_SYSTEM_PROMPT, prompt)
    if content is None:
        return None
    return _postprocess(content, question)


def _closest_option(text: str, options: list[str]) -> str:
    """Last-resort pick for Tier D's never-blank guarantee on a select/radio field:
    when the model's raw response doesn't match any offered option verbatim, embed
    the response and every option and pick whichever option is closest — still a
    real, offered choice, never an invented one. Falls back to the first option if
    embedding fails for any reason, since *some* answer is required here, not the
    single best one."""
    try:
        import numpy as np

        response_vec = np.asarray(embed_text(text or options[0]), dtype=float)
        option_vecs = np.asarray([embed_text(option) for option in options], dtype=float)
        best_index = int(np.argmax(option_vecs @ response_vec))
        return options[best_index]
    except Exception:
        return options[0]


def _truncate_at_sentence(text: str, limit: int) -> str:
    """Trims to the last complete sentence that fits within `limit`, instead of an
    arbitrary character cutoff that can slice a genuine, coherent answer off
    mid-word (confirmed live — see _MAX_GUESS_ANSWER_LENGTH's own comment). Falls
    back to the last whole word, and only to a hard character cut as a final
    resort for a single run-on sentence longer than the entire limit."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for punct in (". ", "! ", "? "):
        cut = window.rfind(punct)
        if cut != -1:
            return window[: cut + 1].strip()
    cut = window.rfind(" ")
    return (window[:cut] if cut != -1 else window).strip()


def _postprocess_guess(content: str, question: QuestionDescriptor) -> str:
    """Tier D's own post-processing: unlike _postprocess, this never returns None
    for a select/radio/checkbox field — see module docstring for why a forced pick
    among the form's own offered choices is safe here in a way inventing free text
    wouldn't be. Free text is accepted as-is (trusting the model further than the
    grounded tiers do, since permissiveness is this tier's entire purpose) unless
    the model still ignored the "never UNKNOWN" instruction, in which case there's
    nothing safe to force — the one case this can still return ""."""
    text = content.strip()
    if question.field_type in ("select", "radio"):
        if not question.options:
            return _truncate_at_sentence(text, _MAX_GUESS_ANSWER_LENGTH)
        for option in question.options:
            if option.strip().lower() == text.lower():
                return option
        return _closest_option(text, question.options)
    if question.field_type == "checkbox":
        lowered = text.strip().lower()
        if lowered in ("yes", "true", "agree", "checked"):
            return "Yes"
        if lowered == "no":
            return "No"
        # Unparseable response for a required checkbox that isn't one of the
        # permanently-excluded sensitive consents (those never reach this
        # function at all — see is_sensitive_question) is almost always a benign
        # acknowledgment/consent gate blocking submission either way; defaulting
        # to "Yes" is what maximizes a successful, genuine submission rather than
        # leaving an otherwise-complete application stuck on one checkbox.
        return "Yes"
    if not text or text.upper() == "UNKNOWN":
        return ""
    return _truncate_at_sentence(text, _MAX_GUESS_ANSWER_LENGTH)


def draft_educated_guess(
    question: QuestionDescriptor,
    resume_text: str,
    profile: dict | None = None,
    job_title: str = "",
    job_company: str = "",
    job_description: str = "",
) -> str | None:
    """Tier D: a deliberately permissive draft for a question that draft_answer/
    draft_from_context will always refuse (nothing in the résumé grounds it) —
    "Why do you want to join {company}?", "How many years of experience do you
    have?", or a select/radio field where nothing clears Tier B's confidence bar.
    Gated behind `llm_educated_guess_enabled`, a separate opt-in from
    `llm_answer_drafting_enabled` (see module docstring). Never applied to an EEO
    or legal/compliance-sensitive question, regardless of setting. Returns None
    only when the feature is off, the question is excluded, the call fails
    outright, or (free text only) the model still couldn't produce anything —
    select/radio/checkbox fields always get *some* answer once this function is
    actually invoked (see _postprocess_guess)."""
    settings = get_settings()
    if not settings.llm_educated_guess_enabled:
        return None
    if is_eeo_question(question.label) or is_sensitive_question(question.label):
        return None
    system_prompt = _GUESS_SYSTEM_PROMPT.format(
        job_title=job_title or "an open role", job_company=job_company or "this company"
    )
    job_context = ""
    if job_title or job_company or job_description:
        job_context = (
            f"Job: {job_title or 'Unknown title'} at {job_company or 'Unknown company'}\n"
            f"Job description:\n{(job_description or '').strip()[:3000]}"
        )
    prompt = _build_prompt_generic(
        question, "Résumé", resume_text or "(no résumé text available)", profile, job_context
    )
    content = _call_ollama(system_prompt, prompt)
    if content is None:
        return None
    answer = _postprocess_guess(content, question)
    return answer or None


def draft_pending_drafts_for_job(
    db, workspace: Workspace, blocked_questions: list[JobBlockedQuestion]
) -> None:
    """Fills in drafted_answer/drafted_by_model on any still-pending, not-yet-drafted
    rows from this attempt, for a human to review in the Needs-Review panel — never
    auto-submitted. Uses the same grounded, résumé-only policy as draft_answer
    (never the permissive Tier D guess tier): a *suggested* answer shown to a human
    before they approve it can afford to honestly say "I don't know" by simply not
    drafting anything, in a way a live, no-review submission cannot. Takes the
    actual ORM rows record_blocked_questions already created/updated (rather than
    re-querying) so this works correctly regardless of whether they've been flushed
    to the DB yet."""
    settings = get_settings()
    # Only the subset _build_profile_context actually reads — not the full profile
    # scheduler.py's own _build_profile() builds for the live form-fill path (that
    # one also carries EEO fields fill_known_fields needs; those never belong here).
    profile = {
        "location": workspace.profile_location,
        "city": workspace.profile_city,
        "state": workspace.profile_state,
        "country": canonicalize_country(workspace.profile_country),
        "current_company": workspace.profile_current_company,
        "current_title": workspace.profile_current_title,
        "work_authorized": workspace.profile_work_authorized,
        "visa_sponsorship": workspace.profile_visa_sponsorship,
        "willing_to_relocate": workspace.profile_willing_to_relocate,
        "start_date": workspace.profile_start_date,
    }
    for bq in blocked_questions:
        if bq.status != "pending" or bq.drafted_answer:
            continue
        question = QuestionDescriptor(
            label=bq.question_text, field_type=bq.field_type, options=bq.options
        )
        draft = draft_answer(question, workspace.resume_text, profile)
        if draft is not None:
            bq.drafted_answer = draft
            bq.drafted_by_model = settings.ollama_model
