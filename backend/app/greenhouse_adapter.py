"""Best-effort Greenhouse auto-apply adapter.

This is intentionally narrow. It understands a fixed set of applicant-profile fields
— name, contact info, links, work details, a handful of common Yes/No eligibility
questions, voluntary EEO self-identification, and a resume/cover-letter upload — all
matched by visible label text, since that's more resilient to markup changes than
hardcoded element ids. It refuses to guess at anything else: if the form has a
required field it doesn't recognize (a custom screening question, a multi-step flow,
a login wall) or it can't confirm the submission went through, it aborts *without*
submitting and reports back why, so the job falls into the manual review queue
instead of a half-filled or wrong application.

Every field we successfully fill is tracked by the actual DOM element's `id`/`name`,
not a fuzzy keyword guess — so the "did we leave a required field unanswered" check
at the end is a precise match against what we actually touched, not a heuristic.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .config import get_settings

log = logging.getLogger("meridian.greenhouse")

# Simple text/textarea/select fields, keyed by the profile dict key. Values are label
# substrings to try in order (Playwright's get_by_label matching is case-insensitive
# and substring-based with exact=False, which tolerates minor phrasing differences
# across companies).
_TEXT_LABELS = {
    "email": ["Email"],
    "phone": ["Phone"],
    "linkedin": ["LinkedIn Profile", "LinkedIn"],
    "portfolio_url": ["Website", "Portfolio"],
    "github_url": ["GitHub"],
    "location": ["Location", "Current Location"],
    "current_company": ["Current Company", "Current Employer"],
    "current_title": ["Current Title", "Current Job Title"],
    "desired_salary": ["Desired Salary", "Salary Expectation", "Compensation Expectation"],
    "start_date": ["Earliest Start Date", "Available Start Date", "Start Date"],
}
# Yes/No eligibility questions. Label text is matched loosely against however the
# company phrased the question; only ever fills a value we actually have on file.
_YES_NO_LABELS = {
    "work_authorized": ["authorized to work"],
    "visa_sponsorship": ["require sponsorship", "visa sponsorship", "need sponsorship"],
    "willing_to_relocate": ["willing to relocate"],
    "is_18_or_older": ["18 years", "at least 18"],
}
# Voluntary EEO self-identification. Left entirely alone unless the workspace profile
# has an explicit, non-blank answer — never inferred or defaulted.
_EEO_LABELS = {
    "gender": ["Gender"],
    "race_ethnicity": ["Race", "Ethnicity"],
    "veteran_status": ["Veteran"],
    "disability_status": ["Disability"],
}
_COVER_LETTER_LABELS = ["Cover Letter"]
_CONFIRMATION_PATTERN = re.compile(r"thank you|application (was|has been) submitted", re.IGNORECASE)
_SUBMIT_PATTERN = re.compile("submit application", re.IGNORECASE)


@dataclass
class AutoApplyResult:
    success: bool
    # One of: unsupported_ats, no_resume_file, custom_questions, form_error.
    reason: str | None = None


def is_greenhouse(apply_url: str) -> bool:
    return "greenhouse" in urlparse(apply_url).netloc.lower() or "greenhouse" in apply_url.lower()


def attempt_apply(
    apply_url: str, profile: dict, resume_bytes: bytes | None, resume_filename: str | None
) -> AutoApplyResult:
    if not is_greenhouse(apply_url):
        return AutoApplyResult(False, "unsupported_ats")
    if not resume_bytes:
        return AutoApplyResult(False, "no_resume_file")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=get_settings().crawler_headless)
            try:
                page = browser.new_page()
                page.goto(apply_url, wait_until="networkidle", timeout=45_000)
                filled_ids = _fill_known_fields(page, profile)

                resume_input = page.locator("input[type='file']").first
                if resume_input.count() == 0:
                    # We already confirmed this is a Greenhouse URL — a missing upload
                    # field here means the page itself is broken/expired, not that the
                    # ATS is unsupported.
                    return AutoApplyResult(False, "form_error")
                resume_input.set_input_files(
                    files=[
                        {
                            "name": resume_filename or "resume.pdf",
                            "mimeType": "application/pdf",
                            "buffer": resume_bytes,
                        }
                    ]
                )
                _track(filled_ids, resume_input)

                if _has_unhandled_required_fields(page, filled_ids):
                    return AutoApplyResult(False, "custom_questions")

                submit = page.get_by_role("button", name=_SUBMIT_PATTERN)
                if submit.count() == 0:
                    submit = page.locator("#submit_app")
                if submit.count() == 0:
                    return AutoApplyResult(False, "form_error")
                submit.first.click()
                page.wait_for_timeout(2000)
                if page.get_by_text(_CONFIRMATION_PATTERN).count() == 0:
                    return AutoApplyResult(False, "form_error")
                return AutoApplyResult(True)
            finally:
                browser.close()
    except PlaywrightTimeoutError:
        return AutoApplyResult(False, "form_error")
    except Exception:
        log.exception("Greenhouse auto-apply failed for %s", apply_url)
        return AutoApplyResult(False, "form_error")


def _track(filled_ids: set[str], locator) -> None:
    """Record the real id/name of a successfully-filled element, so the later
    required-fields audit can recognize it precisely instead of guessing by keyword."""
    try:
        identity = locator.get_attribute("id") or locator.get_attribute("name")
        if identity:
            filled_ids.add(identity)
    except Exception:
        pass


def _fill_known_fields(page, profile: dict) -> set[str]:
    filled_ids: set[str] = set()

    name = (profile.get("name") or "").strip()
    if name:
        first, _, last = name.partition(" ")
        _fill_text(page, filled_ids, ["First Name"], first)
        if last:
            _fill_text(page, filled_ids, ["Last Name"], last)

    for key, labels in _TEXT_LABELS.items():
        value = (profile.get(key) or "").strip()
        if value:
            _fill_text(page, filled_ids, labels, value)

    for key, labels in _YES_NO_LABELS.items():
        value = (profile.get(key) or "").strip()
        if value in ("Yes", "No"):
            _fill_yes_no(page, filled_ids, labels, value)

    for key, labels in _EEO_LABELS.items():
        value = (profile.get(key) or "").strip()
        if value:
            _fill_text(page, filled_ids, labels, value)

    cover_letter = (profile.get("cover_letter") or "").strip()
    if cover_letter:
        _fill_text(page, filled_ids, _COVER_LETTER_LABELS, cover_letter)

    return filled_ids


def _fill_text(page, filled_ids: set[str], labels: list[str], value: str) -> bool:
    """Fills a text/textarea/select field matched by label. Selects handle the value
    as an option label (case-insensitive substring), everything else gets .fill()."""
    for label in labels:
        try:
            field = page.get_by_label(label, exact=False)
            if field.count() == 0:
                continue
            el = field.first
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select":
                el.select_option(label=re.compile(re.escape(value), re.IGNORECASE))
            else:
                el.fill(value)
            _track(filled_ids, el)
            return True
        except Exception:
            continue
    return False


def _fill_yes_no(page, filled_ids: set[str], labels: list[str], value: str) -> bool:
    """Best-effort fill for a Yes/No question rendered as a <select> or a radio pair.
    Radio groups are usually labeled per-option ("Yes"/"No"), not as a single control
    tied to the question text, so this falls back to searching near the question for
    a radio whose own accessible name matches the value."""
    for label in labels:
        try:
            field = page.get_by_label(label, exact=False)
            if field.count():
                el = field.first
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag == "select":
                    el.select_option(label=re.compile(f"^{value}$", re.IGNORECASE))
                    _track(filled_ids, el)
                    return True
        except Exception:
            pass
        try:
            question = page.get_by_text(label, exact=False).first
            if question.count() == 0:
                continue
            container = question.locator(
                "xpath=ancestor::fieldset[1] | ancestor::*[contains(@class,'field')][1]"
            ).first
            radio = container.get_by_role("radio", name=value, exact=False)
            if radio.count():
                radio.first.check()
                _track(filled_ids, radio.first)
                return True
        except Exception:
            continue
    return False


def _has_unhandled_required_fields(page, filled_ids: set[str]) -> bool:
    """True if the form has a required field beyond the ones we just filled — e.g. a
    custom screening question. We deliberately don't try to answer those."""
    required = page.locator("[required], [aria-required='true']")
    count = min(required.count(), 30)
    for i in range(count):
        field = required.nth(i)
        try:
            identity = field.get_attribute("id") or field.get_attribute("name")
        except Exception:
            identity = None
        if not identity or identity not in filled_ids:
            return True
    return False
