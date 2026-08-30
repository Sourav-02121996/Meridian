"""Ranked, safety-conscious submit-control detection shared by every ATS adapter.

Replaces a single exact-phrase match ("submit application") with a scored search
across common element types and label variants, so it works across Greenhouse,
Lever, Ashby, and friends without hardcoding any one platform's copy (issue #20).
Deliberately conservative: intermediate multi-step actions (Next, Continue, Save
draft) are excluded outright rather than risked as a final submission, and a tie
between equally-ranked distinct candidates is treated as ambiguous — refusing to
click — rather than guessed.
"""

import re

# Ordered weakest -> strongest signal. A candidate's tier is the *last* pattern in
# this list that matches its accessible name, so "Submit Application" always outranks
# a bare "Apply", even though both are valid submit language.
_TEXT_TIERS: list[re.Pattern] = [
    re.compile(r"\bapply\b", re.IGNORECASE),
    re.compile(r"\bapply now\b|\bsend\b.*\bapplication\b", re.IGNORECASE),
    re.compile(r"\bsubmit\b", re.IGNORECASE),
    re.compile(r"\bsubmit\b.*\bapplication\b|\bcomplete\b.*\bapplication\b", re.IGNORECASE),
]
# Anything matching this is a mid-flow navigation or a destructive/unrelated action,
# never a final submission — excluded even if it also contains submit-like words.
_EXCLUDE_PATTERN = re.compile(
    r"\b(back|cancel|skip|delete|remove|save (as )?draft|save and continue|next|continue)\b",
    re.IGNORECASE,
)
_NATIVE_SUBMIT_SELECTOR = "button[type='submit'], input[type='submit']"
_CANDIDATE_SELECTOR = (
    "button, input[type='submit'], input[type='button'], a[role='button'], [role='button']"
)
# Cap how many candidates get evaluated — a pathological page with hundreds of
# buttons shouldn't turn one apply attempt into a slow DOM walk.
_MAX_CANDIDATES = 40


def _accessible_name(locator) -> str:
    try:
        tag = locator.evaluate("e => e.tagName.toLowerCase()")
        if tag == "input":
            value = locator.get_attribute("value") or locator.get_attribute("aria-label") or ""
        else:
            value = locator.inner_text() or locator.get_attribute("aria-label") or ""
        return value.strip()
    except Exception:
        return ""


def _text_tier(name: str) -> int:
    """Highest matching tier (1-indexed), or 0 if nothing in _TEXT_TIERS matches."""
    tier = 0
    for index, pattern in enumerate(_TEXT_TIERS, start=1):
        if pattern.search(name):
            tier = index
    return tier


def _is_native_submit(locator) -> bool:
    try:
        return bool(
            locator.evaluate(
                "e => (e.tagName.toLowerCase() === 'button' || "
                "e.tagName.toLowerCase() === 'input') && e.type === 'submit'"
            )
        )
    except Exception:
        return False


def find_submit_control(page):
    """Returns the single best-ranked submit control, or None if none can be safely
    identified. Callers MUST treat None as "refuse to submit" — never fall back to
    clicking an arbitrary candidate."""
    candidates = page.locator(_CANDIDATE_SELECTOR)
    count = min(candidates.count(), _MAX_CANDIDATES)
    scored: list[tuple[int, object]] = []
    for i in range(count):
        el = candidates.nth(i)
        try:
            if not el.is_visible():
                continue
        except Exception:
            continue
        name = _accessible_name(el)
        if not name or _EXCLUDE_PATTERN.search(name):
            continue
        tier = _text_tier(name)
        if tier == 0:
            continue
        # A native submit control ranks above any text tier on its own, since it's a
        # much stronger structural signal than matching copy on a styled <a>/<div>.
        score = tier + (10 if _is_native_submit(el) else 0)
        scored.append((score, el))

    if not scored:
        # Structural fallback: exactly one native submit control anywhere on the page
        # is still a strong enough signal to trust, even with unrecognized text.
        native = page.locator(_NATIVE_SUBMIT_SELECTOR)
        return native.first if native.count() == 1 else None

    best_score = max(score for score, _ in scored)
    best = [el for score, el in scored if score == best_score]
    # A tie between distinct elements at the top score is ambiguous — refuse rather
    # than pick one arbitrarily.
    return best[0] if len(best) == 1 else None
