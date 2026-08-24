"""Best-effort Greenhouse auto-apply adapter.

This is intentionally narrow. It only understands the small, standardized set of
fields every Greenhouse job-board application form ships with: first/last name,
email, phone, a resume file upload, and optionally a LinkedIn URL. It refuses to
guess at anything else — if the form has a required field it doesn't recognize
(a custom screening question, a multi-step flow, a login wall) or it can't confirm
the submission went through, it aborts *without* submitting and reports back why,
so the job falls into the manual review queue instead of a half-filled or wrong
application. Selectors are matched by visible label text where possible, since
that's more resilient to markup changes than hardcoded element ids.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .config import get_settings

log = logging.getLogger("meridian.greenhouse")

_LABELS = {
    "first_name": ["First Name"],
    "last_name": ["Last Name"],
    "email": ["Email"],
    "phone": ["Phone"],
    "linkedin": ["LinkedIn Profile", "LinkedIn"],
}
_HANDLED_FIELD_TOKENS = ("first_name", "last_name", "email", "phone", "resume", "linkedin")
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
                filled = _fill_known_fields(page, profile)

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
                filled.add("resume")

                if _has_unhandled_required_fields(page, filled):
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


def _fill_known_fields(page, profile: dict) -> set[str]:
    filled: set[str] = set()
    name = (profile.get("name") or "").strip()
    if name:
        first, _, last = name.partition(" ")
        if _fill_label(page, _LABELS["first_name"], first):
            filled.add("first_name")
        if last and _fill_label(page, _LABELS["last_name"], last):
            filled.add("last_name")
    if profile.get("email") and _fill_label(page, _LABELS["email"], profile["email"]):
        filled.add("email")
    if profile.get("phone") and _fill_label(page, _LABELS["phone"], profile["phone"]):
        filled.add("phone")
    if profile.get("linkedin") and _fill_label(page, _LABELS["linkedin"], profile["linkedin"]):
        filled.add("linkedin")
    return filled


def _fill_label(page, labels: list[str], value: str) -> bool:
    for label in labels:
        try:
            field = page.get_by_label(label, exact=False)
            if field.count():
                field.first.fill(value)
                return True
        except Exception:
            continue
    return False


def _has_unhandled_required_fields(page, filled: set[str]) -> bool:
    """True if the form has a required field beyond the standard set we just filled —
    e.g. a custom screening question. We deliberately don't try to answer those."""
    required = page.locator("[required], [aria-required='true']")
    count = min(required.count(), 30)
    for i in range(count):
        field = required.nth(i)
        try:
            identity = (field.get_attribute("name") or field.get_attribute("id") or "").lower()
        except Exception:
            continue
        if not any(token in identity for token in _HANDLED_FIELD_TOKENS):
            return True
    return False
