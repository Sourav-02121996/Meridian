"""Detects and clicks through an initial "apply gate" page.

Some ATS (confirmed on Ashby) show a landing page with a single "Apply for this Job"
button — the actual application form (and its file input) doesn't exist in the DOM at
all until that's clicked. Mirrors submit.py's ranked, ambiguity-refusing approach, but
tuned for *opening* language ("Apply") rather than final-submission language, where
"Apply" alone is deliberately the weakest signal.
"""

import re

# Ordered weakest -> strongest; a candidate's tier is the last pattern that matches.
_GATE_TEXT_TIERS: list[re.Pattern] = [
    re.compile(r"\bapply\b", re.IGNORECASE),
    re.compile(r"\bapply now\b", re.IGNORECASE),
    re.compile(
        r"\bapply for this (job|role|position)\b|\bstart (your )?application\b", re.IGNORECASE
    ),
]
# Site-chrome nav links (e.g. a "Careers"/"Jobs" menu item) — excluded only on a
# *whole-name* match, since a substring check would also catch the word "Job" inside
# the legitimate gate text "Apply for this Job" (confirmed live on Ashby).
_EXCLUDE_PATTERN = re.compile(
    r"^\s*(careers?|jobs?|home|about|log ?in|sign ?in|search)\s*$", re.IGNORECASE
)
# Includes plain <a> tags deliberately: Lever's gate is a bare anchor with no
# role="button" attribute (confirmed live), not just <button>/[role=button].
_CANDIDATE_SELECTOR = "button, a, [role='button']"
_MAX_CANDIDATES = 20


def _accessible_name(locator) -> str:
    try:
        return (locator.inner_text() or locator.get_attribute("aria-label") or "").strip()
    except Exception:
        return ""


def _href(locator) -> str | None:
    try:
        return locator.get_attribute("href")
    except Exception:
        return None


def _bounding_box(locator) -> dict | None:
    try:
        return locator.bounding_box()
    except Exception:
        return None


def _same_target(a, b) -> bool:
    """True if two matched candidates are really the same clickable control, not two
    genuinely different actions. Covers two patterns confirmed live: (1) a CTA
    duplicated verbatim elsewhere on the page (Lever: two separate <a> tags with the
    identical href), and (2) a <button> nested inside an <a> both matching our
    selector (Ashby) — those occupy the same on-screen box."""
    href_a, href_b = _href(a), _href(b)
    if href_a and href_a == href_b:
        return True
    box_a, box_b = _bounding_box(a), _bounding_box(b)
    if not box_a or not box_b:
        return False
    return (
        abs(box_a["x"] - box_b["x"]) < 2
        and abs(box_a["y"] - box_b["y"]) < 2
        and abs(box_a["width"] - box_b["width"]) < 2
        and abs(box_a["height"] - box_b["height"]) < 2
    )


def find_apply_gate(page):
    """Returns the single best "open the application form" trigger, or None if
    nothing matches or multiple *distinct* candidates tie — callers must treat None
    as "leave the page as-is", never guess which control to click."""
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
        tier = 0
        for index, pattern in enumerate(_GATE_TEXT_TIERS, start=1):
            if pattern.search(name):
                tier = index
        if tier > 0:
            scored.append((tier, el))

    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    best = [el for score, el in scored if score == best_score]
    if len(best) == 1:
        return best[0]
    if all(_same_target(best[0], other) for other in best[1:]):
        return best[0]
    return None


def ensure_form_present(page, timeout_ms: int = 10_000) -> None:
    """If the application form doesn't appear to be on the page yet (no file input),
    try clicking a single unambiguous apply-gate trigger and give the form a bounded
    window to render. A no-op if the form is already present or no safe trigger can
    be identified — the caller's own form_not_found fallback handles that case."""
    if page.locator("input[type='file']").count() > 0:
        return
    gate = find_apply_gate(page)
    if gate is None:
        return
    try:
        gate.click()
        page.wait_for_selector("input[type='file']", timeout=timeout_ms, state="attached")
    except Exception:
        pass
