"""A narrated, step-by-step walkthrough of the exact same functions attempt_apply
uses in production — split apart and printed at each stage instead of running
silently inside one opaque fill_and_submit() call. This file behaves like any other
test when run normally (headless, asserts pass, no output shown), but it's designed
to be *watched*:

    PWDEBUG=1 pytest tests/test_manual_walkthrough.py -s -v

PWDEBUG=1 is a Playwright-native environment variable — no code here reacts to it
directly. It forces every browser this process launches into headed mode with the
Playwright Inspector attached, letting you step through (or just watch, and slow
down at will) exactly what production code does: reveal, fill, detect, resolve,
submit. `-s` un-captures the print() narration so it interleaves with your stepping.

See DEBUGGING.md for the rest of the toolkit (verbose logs, watching a real batch
run, inspecting the DB afterward).
"""

from pathlib import Path

from app.apply_adapters.engine import _give_up
from app.apply_adapters.fields import fill_known_fields, find_unhandled_required_fields, track
from app.apply_adapters.platforms import resolve_platform
from app.apply_adapters.reveal import ensure_form_present
from app.apply_adapters.submit import find_submit_control
from app.apply_adapters.types import AnswerAttempt

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = resolve_platform(None)
PROFILE = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "555-0100",
}
RESUME_BYTES = b"%PDF-1.4 fake resume content for testing"


def test_watch_a_fully_answerable_application(page):
    print(
        "\n--- STEP 1: navigate to the fixture page (this is what page.goto() does"
        " for a real ATS URL in attempt_apply) ---"
    )
    page.goto(f"file://{FIXTURES / 'fully_answerable.html'}")

    print(
        "--- STEP 2: ensure_form_present — no-op here since the form is already"
        " on the page; on Ashby-style ATS this is where a landing-page 'Apply'"
        " button gets clicked first ---"
    )
    ensure_form_present(page)

    print(
        "--- STEP 3: fill_known_fields — matches each profile field to the form's"
        " own label text (First Name, Email, ...) ---"
    )
    filled_ids = fill_known_fields(page, PROFILE, extra_text_labels=CONFIG.extra_text_labels)
    print(f"    filled element ids/names: {sorted(filled_ids)}")

    print(
        "--- STEP 4: attach the résumé file (and track() it, exactly like"
        " fill_and_submit does, so it counts as 'handled' below) ---"
    )
    resume_input = page.locator("input[type='file']").first
    resume_input.set_input_files(
        files=[{"name": "resume.pdf", "mimeType": "application/pdf", "buffer": RESUME_BYTES}]
    )
    track(filled_ids, resume_input)

    print(
        "--- STEP 5: find_unhandled_required_fields — anything required and still"
        " unfilled would show up here as a QuestionDescriptor ---"
    )
    unhandled = find_unhandled_required_fields(page, filled_ids)
    print(f"    unhandled required fields: {[f.descriptor for f in unhandled]}")
    assert unhandled == []  # this fixture has nothing left over

    print(
        "--- STEP 6: find_submit_control — ranked search for the real submit"
        " button, refusing to guess if more than one candidate ties ---"
    )
    submit = find_submit_control(page)
    assert submit is not None
    submit.click()
    page.wait_for_timeout(500)

    print("--- STEP 7: confirmation check ---")
    confirmed = page.get_by_text(CONFIG.confirmation_pattern).count() > 0
    print(f"    confirmation text found: {confirmed}")
    assert confirmed


def test_watch_a_custom_question_get_resolved_by_a_bank_match(page):
    print(
        "\n--- Same flow, but this fixture has one question fill_known_fields"
        " doesn't recognize — watch it get caught, then resolved by a stand-in"
        " Q&A-bank answer_lookup instead of falling back to needs_review ---"
    )
    page.goto(f"file://{FIXTURES / 'custom_text_question.html'}")
    ensure_form_present(page)
    filled_ids = fill_known_fields(page, PROFILE, extra_text_labels=CONFIG.extra_text_labels)
    resume_input = page.locator("input[type='file']").first
    resume_input.set_input_files(
        files=[{"name": "resume.pdf", "mimeType": "application/pdf", "buffer": RESUME_BYTES}]
    )
    track(filled_ids, resume_input)

    unhandled = find_unhandled_required_fields(page, filled_ids)
    print(f"    blocked question(s) before any answer_lookup: {[f.descriptor for f in unhandled]}")
    assert len(unhandled) == 1
    assert unhandled[0].descriptor.label == "Why do you want to work here?"

    print(
        "--- simulating what qa_bank.QABankMatcher.match() would return for a"
        " previously-answered question with this same label ---"
    )
    from app.apply_adapters.fields import _fill_answer

    attempt = AnswerAttempt(value="I'm excited about the team's mission.", source="profile")
    filled = _fill_answer(page, unhandled[0], attempt.value)
    print(f"    filled via bank match: {filled}")
    assert filled
    assert page.locator("#custom_q").input_value() == "I'm excited about the team's mission."

    still_unhandled = find_unhandled_required_fields(page, filled_ids | {unhandled[0].identity})
    print(
        f"    remaining unresolved after the bank match: {[f.descriptor for f in still_unhandled]}"
    )
    assert still_unhandled == []

    submit = find_submit_control(page)
    submit.click()
    page.wait_for_timeout(500)
    assert page.get_by_text(CONFIG.confirmation_pattern).count() > 0
    print(
        "--- done: this is exactly the path a job hits once you've answered its"
        " question once via the Q&A bank UI — see qa_bank.py/engine.py ---"
    )


def test_watch_give_up_reasons_get_logged(caplog):
    """No browser here — just a reminder that every non-success outcome is logged
    at the point it's decided (engine.py's _give_up), not just on a hard crash. Set
    LOG_LEVEL=DEBUG (see config.py) for per-field tracing on top of this."""
    import logging

    with caplog.at_level(logging.INFO, logger="meridian.apply_adapters"):
        result = _give_up("https://example.com/apply", "greenhouse", "form_not_found")
    assert result.reason == "form_not_found"
    assert "form_not_found" in caplog.text
    print(f"\n--- logged: {caplog.text.strip()} ---")
