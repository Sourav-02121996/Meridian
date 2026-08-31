"""Drives apply_adapters.engine.fill_and_submit directly against local fixture
pages instead of a live application URL — the seam engine.py's own module docstring
anticipates (issue #21), so adapter behavior is covered deterministically without
ever submitting to a real employer or depending on network access.
"""

from pathlib import Path
from time import sleep

import app.apply_adapters.engine as engine_module
from app.apply_adapters.engine import fill_and_submit
from app.apply_adapters.platforms import resolve_platform
from app.apply_adapters.profile_similarity import match_profile_field
from app.apply_adapters.types import AnswerAttempt

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = resolve_platform(None)  # generic adapter — nothing here is platform-specific
PROFILE = {"name": "Jane Doe", "email": "jane@example.com", "phone": "555-0100"}
RESUME_BYTES = b"%PDF-1.4 fake resume content for testing"


def _goto(page, fixture_name: str) -> None:
    page.goto(f"file://{FIXTURES / fixture_name}")


def test_fully_answerable_form_succeeds(page):
    _goto(page, "fully_answerable.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is True
    assert result.reason is None
    assert result.unresolved_questions == []


def test_explicit_submit_response_confirms_success_without_page_copy(page):
    """An ATS server acknowledgement is stronger evidence than waiting for a
    particular thank-you sentence. Phase 2 must recognize it and persist only a
    sanitized endpoint/status diagnostic, never the submitted request body."""
    page.route(
        "https://ats.test/**",
        lambda route: route.fulfill(
            status=201,
            content_type="application/json",
            body='{"success": true, "applicationId": "abc-123"}',
        ),
    )
    _goto(page, "submission_response.html")

    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)

    assert result.success is True
    assert result.reason is None
    assert result.detail == "ATS submit response 201 from https://ats.test/application/submit"
    assert "ApplicationFormSubmit" not in result.detail


def test_submit_api_rejection_preserves_server_error(page):
    """A GraphQL/HTTP rejection can leave native :invalid at zero. It must not be
    collapsed into confirmation_not_detected; the actionable server response is
    returned without replaying or exposing the request payload."""
    page.route(
        "https://ats.test/**",
        lambda route: route.fulfill(
            status=422,
            content_type="application/json",
            body='{"errors": [{"message": "Location is not specific enough"}]}',
        ),
    )
    _goto(page, "submission_response.html")

    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)

    assert result.success is False
    assert result.reason == "submission_rejected"
    assert "422" in (result.detail or "")
    assert "Location is not specific enough" in (result.detail or "")
    assert "ApplicationFormSubmit" not in (result.detail or "")


def test_nested_graphql_form_rejection_is_not_marked_as_success(page):
    """Ashby returns HTTP 200 plus non-null GraphQL data even when it rejects a
    form. Its real validation signal is nested below applicationFormResult, so
    this must become submission_rejected rather than an applied job."""
    page.route(
        "https://ats.test/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"data":{"submitApplicationFormAction":'
                '{"applicationFormResult":{"errorMessages":'
                '["Missing entry for required field: work authorization"],'
                '"formErrors":[{"message":"Missing entry for required field"}]}}}}'
            ),
        ),
    )
    _goto(page, "submission_response.html")

    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)

    assert result.success is False
    assert result.reason == "submission_rejected"
    assert "Missing entry for required field: work authorization" in (result.detail or "")


def test_ashby_serializes_field_saves_before_the_next_interaction(page):
    """A response to Ashby's ApiSetFormValue contains the complete form state.
    The engine must wait for each save to finish before editing another field, or
    a delayed response can restore a previously completed answer to blank."""

    def delayed_field_save(route):
        sleep(0.15)
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body="{}",
        )

    page.route("https://ats.test/field-save", delayed_field_save)
    _goto(page, "ashby_serialized_field_saves.html")

    result = fill_and_submit(
        page,
        PROFILE,
        RESUME_BYTES,
        "resume.pdf",
        resolve_platform("ashby"),
    )

    assert result.success is True
    assert page.evaluate("window.maxConcurrentSaves") == 1


def test_custom_text_question_is_captured(page):
    _goto(page, "custom_text_question.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is False
    assert result.reason == "custom_questions"
    assert len(result.unresolved_questions) == 1
    question = result.unresolved_questions[0]
    assert question.label == "Why do you want to work here?"
    assert question.field_type == "text"
    assert question.options == []


def test_custom_radio_group_is_deduplicated(page):
    _goto(page, "custom_radio_group.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is False
    assert result.reason == "custom_questions"
    # Three required radios sharing one `name` must produce exactly one captured
    # question, not one per radio input.
    assert len(result.unresolved_questions) == 1
    question = result.unresolved_questions[0]
    assert question.label == "How did you hear about us?"
    assert question.field_type == "radio"
    assert question.options == ["LinkedIn", "Referral", "Other"]


def test_custom_select_question_captures_options(page):
    _goto(page, "custom_select_question.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is False
    assert result.reason == "custom_questions"
    assert len(result.unresolved_questions) == 1
    question = result.unresolved_questions[0]
    assert question.label == "How did you learn about this opportunity?"
    assert question.field_type == "select"
    # The blank placeholder option must be excluded, real options kept in order.
    assert question.options == ["LinkedIn", "Indeed", "Employee Referral"]


def test_yesno_toggle_known_question_resolved_by_profile(page):
    """Ashby's button-pair Yes/No widget (see fields.py's _yesno_toggle_* helpers)
    for a question fields.YES_NO_LABELS already recognizes (work_authorized) must
    be resolved by Tier A alone, exactly like a native radio group would be."""
    _goto(page, "ashby_yesno_toggle_known.html")
    result = fill_and_submit(
        page, {**PROFILE, "work_authorized": "Yes"}, RESUME_BYTES, "resume.pdf", CONFIG
    )
    assert result.success is True
    assert result.reason is None
    assert result.unresolved_questions == []
    assert page.get_by_text("Yes").first.get_attribute("aria-pressed") == "true"


def test_yesno_toggle_unknown_question_is_captured(page):
    """The regression this fix is actually about: a yes/no toggle whose label Tier A
    doesn't recognize must surface as a captured custom_questions entry — not
    silently reach Submit unanswered the way it did before find_unhandled_required_
    fields learned to look for this shape (it carries no required/aria-required
    attribute anywhere, so the old `[required], [aria-required='true']` scan alone
    could never have caught it)."""
    _goto(page, "ashby_yesno_toggle_unknown.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is False
    assert result.reason == "custom_questions"
    assert len(result.unresolved_questions) == 1
    question = result.unresolved_questions[0]
    assert question.label == "Do you consent to a background check?"
    assert question.field_type == "radio"
    assert question.options == ["Yes", "No"]


def test_answer_lookup_resolves_a_yesno_toggle_question(page):
    """Tier B/C proof for this widget shape, mirroring
    test_answer_lookup_resolves_a_previously_blocked_question: an answer_lookup
    resolving the same question that, unassisted, would have blocked the whole
    application results in the toggle actually being clicked and the form
    successfully submitted."""
    _goto(page, "ashby_yesno_toggle_unknown.html")

    def answer_lookup(question):
        if question.label == "Do you consent to a background check?":
            return AnswerAttempt(value="Yes", source="profile")
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )
    assert result.success is True
    assert result.reason is None
    assert page.get_by_text("Yes").first.get_attribute("aria-pressed") == "true"


def test_broken_label_known_field_resolved_by_profile(page):
    """Ashby's dangling `label for="..."` pattern (see fields.py's
    _control_for_broken_label) isn't specific to the yes/no toggle -- confirmed live
    on the real "Location" field too, a totally different control type
    (role="combobox" autocomplete, not a button pair). A known profile key must
    still get filled via the DOM-proximity fallback once get_by_label() alone comes
    back empty."""
    _goto(page, "ashby_broken_label_known.html")
    result = fill_and_submit(
        page, {**PROFILE, "location": "Boston, MA"}, RESUME_BYTES, "resume.pdf", CONFIG
    )
    assert result.success is True
    assert result.reason is None
    assert result.unresolved_questions == []


def test_broken_label_unknown_field_is_captured(page):
    """The generalization this fix is actually about: *any* Ashby field using this
    broken for=/id shape -- known alias or not -- must surface as a captured
    custom_questions entry rather than silently vanish. Before this fix, a field
    like this wasn't just unfillable, it was invisible: find_unhandled_required_
    fields' `[required], [aria-required='true']` scan alone could never have caught
    it, since neither attribute exists anywhere on this widget."""
    _goto(page, "ashby_broken_label_unknown.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is False
    assert result.reason == "custom_questions"
    assert len(result.unresolved_questions) == 1
    question = result.unresolved_questions[0]
    assert question.label == "What is your favorite programming language?"
    assert question.field_type == "text"
    assert question.options == []


def test_answer_lookup_fills_a_freeform_combobox_question(page):
    """The regression this fix is actually about: a required question rendered as
    role="combobox" but with no real backing option list (see fields.py's
    _fill_react_select "saw_any_option" fallback) must still get filled once an
    answer is found -- before this fix, the Q&A bank/LLM could *match* this
    question just fine, but _fill_answer always routed any combobox through the
    strict select-a-real-option mechanism, which has nothing to select here and
    silently gave up instead of falling back to typing the answer directly."""
    _goto(page, "greenhouse_freeform_combobox_question.html")

    def answer_lookup(question):
        if question.label == "Describe your experience with our product.*":
            return AnswerAttempt(value="I've used it for three years.", source="profile")
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )
    assert result.success is True
    assert result.reason is None
    assert page.locator("#essay_question").input_value() == "I've used it for three years."


def test_greenhouse_city_react_select_uses_full_profile_location(page):
    """Twitch's `Location (City)` is a Greenhouse react-select, not a freeform
    input. A complete profile location must be selected from its offered options
    during Tier A instead of being sent to the Ashby-style autocomplete path."""
    _goto(page, "greenhouse_city_react_select.html")
    city = "Boston, Massachusetts, United States"

    result = fill_and_submit(
        page,
        {**PROFILE, "location": city},
        RESUME_BYTES,
        "resume.pdf",
        CONFIG,
    )

    assert result.success is True
    assert result.reason is None
    assert page.locator("#candidate-location-selected").inner_text() == city


def test_greenhouse_city_react_select_refuses_an_ambiguous_short_city(page):
    """`Boston` is not a factual selection when several cities begin with that
    text. The answer must remain unresolved instead of silently choosing the
    first option Greenhouse happened to return."""
    _goto(page, "greenhouse_city_react_select.html")

    def answer_lookup(question):
        if question.label == "Location (City)*":
            return AnswerAttempt(value="Boston", source="human_approved")
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )

    assert result.success is False
    assert result.reason == "custom_questions"
    assert [question.label for question in result.unresolved_questions] == ["Location (City)*"]
    assert page.locator("#candidate-location-selected").inner_text() == ""


def test_greenhouse_city_react_select_replays_an_exact_approved_answer(page):
    """The retry path uses the same control-specific selection and exact visible
    commitment check as Tier A, so a human-approved city cannot be mistaken for
    a plain typed-but-unselected value."""
    _goto(page, "greenhouse_city_react_select.html")
    city = "Boston, Massachusetts, United States"

    def answer_lookup(question):
        if question.label == "Location (City)*":
            return AnswerAttempt(value=city, source="human_approved")
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )

    assert result.success is True
    assert result.reason is None
    assert page.locator("#candidate-location-selected").inner_text() == city


def test_greenhouse_country_react_select_accepts_a_shortened_selected_label(page):
    """Greenhouse presents `United States +1` in the country menu but renders
    only `+1` once selected. A changed, non-empty selected label is valid proof
    of commitment when the exact menu label is intentionally shortened."""
    _goto(page, "greenhouse_country_react_select.html")

    result = fill_and_submit(
        page,
        {**PROFILE, "country": "United States"},
        RESUME_BYTES,
        "resume.pdf",
        CONFIG,
    )

    assert result.success is True
    assert result.reason is None
    assert page.locator("#country-selected").inner_text() == "+1"


def test_ashby_control_model_captures_marker_only_controls(page):
    """Harvey's live Frontend Platform form marks both its id-less Location
    autocomplete and four-option hybrid-work radio group as required only on a
    dangling label. Both must be reported, with the radio group represented once
    and with its exact offered answers preserved for review."""
    _goto(page, "ashby_frontend_control_model.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)

    assert result.success is False
    assert result.reason == "custom_questions"
    assert [question.label for question in result.unresolved_questions] == [
        "Location",
        (
            "This role is tied to the office location listed in the job posting. "
            "Team members are expected to work from the office 3 days per week as "
            "part of Harvey's hybrid work model. Are you currently based in the "
            "listed location and able to work in person 3 days per week?"
        ),
    ]
    assert result.unresolved_questions[1].field_type == "radio"
    assert result.unresolved_questions[1].options == [
        "Yes, I'm based in this location and able to work from the office 3 days per week",
        "No, I'm not based in this location but willing to relocate",
        "No, I'm only able to work remotely",
        "Other (optional context)",
    ]


def test_ashby_control_model_does_not_treat_an_option_as_a_yesno_question(page):
    """The phrase `willing to relocate` occurs only inside one answer option on
    the Harvey question. It must not make Tier A translate the profile's plain
    willing_to_relocate=Yes into the materially different claim that the
    candidate already lives in the listed office location."""
    _goto(page, "ashby_frontend_control_model.html")
    result = fill_and_submit(
        page,
        {**PROFILE, "willing_to_relocate": "Yes"},
        RESUME_BYTES,
        "resume.pdf",
        CONFIG,
    )

    assert result.success is False
    assert result.reason == "custom_questions"
    assert not page.locator("#hybrid_based").is_checked()
    assert not page.locator("#hybrid_relocate").is_checked()
    assert any(
        question.label.startswith("This role is tied to the office location")
        for question in result.unresolved_questions
    )


def test_ashby_control_model_replays_approved_answers(page):
    """A Retry auto-apply must use the captured control kind: choose a real
    Location suggestion despite the input having no id, then choose one exact
    native-radio option rather than substring-matching a different answer."""
    _goto(page, "ashby_frontend_control_model.html")

    def answer_lookup(question):
        answers = {
            "Location": "Boston, Massachusetts, United States",
            (
                "This role is tied to the office location listed in the job posting. "
                "Team members are expected to work from the office 3 days per week as "
                "part of Harvey's hybrid work model. Are you currently based in the "
                "listed location and able to work in person 3 days per week?"
            ): "No, I'm not based in this location but willing to relocate",
        }
        value = answers.get(question.label)
        return AnswerAttempt(value=value, source="human_approved") if value else None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )

    assert result.success is True
    assert result.reason is None
    assert page.get_by_role("combobox").input_value() == "Boston, Massachusetts, United States"
    assert page.locator("#hybrid_relocate").is_checked()
    assert not page.locator("#hybrid_based").is_checked()


def test_ashby_location_alias_survives_relocation_text_elsewhere(page):
    """The live Harvey page contains `location` in its hybrid question and option
    labels. The exact standalone Location label must still win over those longer
    substring matches so Tier A can fill the profile value directly."""
    _goto(page, "ashby_frontend_control_model.html")
    looked_up = []

    def answer_lookup(question):
        looked_up.append(question.label)
        if question.label.startswith("This role is tied to the office location"):
            return AnswerAttempt(
                value="No, I'm not based in this location but willing to relocate",
                source="human_approved",
            )
        return None

    result = fill_and_submit(
        page,
        {**PROFILE, "location": "Boston, Massachusetts, United States"},
        RESUME_BYTES,
        "resume.pdf",
        CONFIG,
        answer_lookup=answer_lookup,
    )

    assert result.success is True
    assert "Location" not in looked_up
    assert page.get_by_role("combobox").input_value() == "Boston, Massachusetts, United States"


def test_ashby_control_model_respects_an_already_checked_radio(page):
    """The structural required audit reads the live checked state as well as its
    own filled-id bookkeeping, so a value selected by page state or an earlier
    tier is not incorrectly sent back to review."""
    _goto(page, "ashby_frontend_control_model.html")
    page.locator("#hybrid_relocate").check()

    def answer_lookup(question):
        if question.label == "Location":
            return AnswerAttempt(
                value="Boston, Massachusetts, United States", source="human_approved"
            )
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )

    assert result.success is True
    assert result.reason is None


def test_later_field_survives_an_earlier_field_shifting_the_dom(page):
    """The regression this fix is actually about: filling "Option A" inserts a new
    required element ahead of "Option B" in DOM order (see fixture) -- Option B
    must still resolve correctly despite that, because find_unhandled_required_
    fields now rebinds every captured field to a stable id-based selector instead
    of a positional nth() locator that gets silently re-resolved against the
    now-different DOM by the time anything acts on it."""
    _goto(page, "dynamic_dom_shift.html")

    def answer_lookup(question):
        if question.label in ("Option A", "Option B"):
            return AnswerAttempt(value="Yes", source="profile")
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )
    assert result.success is True
    assert result.reason is None
    assert page.locator("#opt_a").is_checked()
    assert page.locator("#opt_b").is_checked()


def test_bracketed_id_field_is_rebound_safely(page):
    """The regression this fix is actually about: a required field whose real id
    contains CSS-special characters (`[`/`]`, confirmed live on a real Greenhouse
    checkbox-group option) must still resolve -- before this fix, rebinding to
    it via a raw f"#{id}" CSS selector raised a SyntaxError (not just a
    non-match), which _fill_answer's own try/except swallowed as a plain
    "couldn't fill it" false negative."""
    _goto(page, "bracketed_id_checkbox.html")

    def answer_lookup(question):
        if question.label == "Bracketed option":
            return AnswerAttempt(value="Yes", source="profile")
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )
    assert result.success is True
    assert result.reason is None
    assert page.locator("#question_123\\[\\]_456").is_checked()


def test_required_checkbox_group_with_one_checked_still_submits(page):
    """The regression this fix is actually about: correctly checking exactly one
    box in a "select all that apply" group (leaving the rest unchecked, per real
    profile/answer_lookup values) must not be treated as a broken form just
    because native HTML has no way to mark the unchecked siblings anything but
    individually :invalid -- see fields.py's count_genuinely_invalid."""
    _goto(page, "required_checkbox_group.html")

    def answer_lookup(question):
        value = {"TikTok": "No", "Instagram": "Yes", "YouTube": "No"}.get(question.label)
        return AnswerAttempt(value=value, source="profile") if value else None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )
    assert result.success is True
    assert result.reason is None
    assert page.locator("#platform_instagram").is_checked()
    assert not page.locator("#platform_tiktok").is_checked()
    assert not page.locator("#platform_youtube").is_checked()


def test_required_checkbox_group_checks_explicit_none_when_every_answer_is_no(page):
    """A required Greenhouse-style multi-select group with an explicit `None`
    option represents all per-option No answers by checking that one option. It
    must not leave every required checkbox unchecked and block Submit."""
    _goto(page, "required_checkbox_group_with_none.html")

    def answer_lookup(question):
        return AnswerAttempt(value="No", source="human_approved")

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )

    assert result.success is True
    assert page.locator("#platform_none").is_checked()
    assert not page.locator("#platform_tiktok").is_checked()
    assert not page.locator("#platform_youtube").is_checked()


def test_hidden_validation_proxy_does_not_block_submission(page):
    """The regression this fix is actually about: a leftover hidden, id-less/
    name-less validation-proxy input (confirmed live backing Greenhouse's
    react-select-style widgets) must not block submission just because it's
    natively :invalid -- there's no id/name for anything to ever have targeted
    it with an answer in the first place. See fields.py's count_genuinely_invalid."""
    _goto(page, "hidden_validation_proxy.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is True
    assert result.reason is None


def test_native_submit_dispatches_when_only_hidden_validation_proxies_remain(page):
    """A native submit button performs HTML validation before its JavaScript
    submit handler runs. Once the final gate has proven every remaining invalid
    node is an id-less hidden proxy, the engine must bypass only that native block
    so Greenhouse can make its server-side submission request."""
    _goto(page, "native_submit_hidden_proxy.html")

    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)

    assert result.success is True
    assert result.reason is None
    assert page.locator("#confirmation").inner_text() == "Application has been submitted"


def test_tier_b_profile_similarity_resolves_a_rephrased_question_end_to_end(page):
    """True end-to-end proof for Tier B, with the *real* sentence-transformers
    model -- no mocked embeddings anywhere in this test. "What company do you
    currently work for?" shares no fixed alias substring with fields.py's
    TEXT_LABELS["current_company"] ("Current Company"/"Current Employer"), so
    Tier A alone must leave it unresolved; profile_similarity.match_profile_field
    is the only thing standing between this question and custom_questions."""
    _goto(page, "tier_b_profile_similarity_question.html")
    profile = {**PROFILE, "current_company": "Acme Corp"}

    def answer_lookup(question):
        return match_profile_field(question, profile)

    result = fill_and_submit(
        page, profile, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )
    assert result.success is True
    assert result.reason is None
    assert page.locator("#employer_question").input_value() == "Acme Corp"


def test_visible_captcha_form_is_refused_before_any_fill(page):
    """A genuinely interactive CAPTCHA (confirmed live: Lever's own opt-in
    hCaptcha toggle, e.g. Symplicity's posting) must short-circuit the whole
    attempt *before* any field is touched or the résumé is uploaded, not just
    before Submit is clicked -- so retrying the same job never risks a real
    submission going through on some attempts and not others, no matter how many
    times it's retried."""
    _goto(page, "captcha_protected.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is False
    assert result.reason == "captcha_protected"
    assert result.unresolved_questions == []
    # Proof nothing was even attempted: the required fields are still empty and no
    # file was attached.
    assert page.locator("#first_name").input_value() == ""
    assert page.locator("#email").input_value() == ""
    assert page.locator("#confirmation").inner_text() == ""


def test_invisible_captcha_element_does_not_block_the_attempt(page):
    """The regression this fix is actually about: an *invisible* reCAPTCHA
    (confirmed live -- Twitch, GitLab, and a live Anthropic posting on
    job-boards.greenhouse.io all share the exact same sitekey, size=invisible)
    is Greenhouse's own platform-wide default, not an employer opt-in -- treating
    it the same as a visible challenge would make auto-apply never fire on any
    current Greenhouse posting. Must proceed through the whole form and submit
    normally."""
    _goto(page, "invisible_captcha_element.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is True
    assert result.reason is None
    assert page.locator("#first_name").input_value() == "Jane"


def test_invisible_captcha_via_live_frame_url_does_not_block_the_attempt(page):
    """Same as above, but detected via a real (data:) iframe navigation carrying
    size=invisible in its own URL -- the actual signal a live reCAPTCHA anchor
    iframe's src carries, rather than the element/data-size fallback."""
    _goto(page, "invisible_captcha_frame.html")
    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)
    assert result.success is True
    assert result.reason is None


def test_invisible_captcha_that_suppresses_submit_is_reported_precisely(page, monkeypatch):
    """Invisible CAPTCHA is permitted initially, but a completed form that never
    emits a submission response or confirmation must be reported as CAPTCHA
    protected rather than an ambiguous confirmation timeout."""
    monkeypatch.setattr(engine_module, "_CONFIRMATION_TIMEOUT_MS", 100)
    monkeypatch.setattr(engine_module, "_CONFIRMATION_POLL_MS", 50)
    _goto(page, "invisible_captcha_suppresses_submit.html")

    result = fill_and_submit(page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG)

    assert result.success is False
    assert result.reason == "captcha_protected"
    assert "no ATS submission response" in (result.detail or "")


def test_answer_lookup_resolves_a_previously_blocked_question(page):
    """The key end-to-end proof that Tier B's engine wiring actually works: an
    answer_lookup callback resolving the same question that, unassisted, would have
    blocked the whole application (see test_custom_text_question_is_captured)
    results in the field actually being filled and the form successfully submitted."""
    _goto(page, "custom_text_question.html")

    def answer_lookup(question):
        if question.label == "Why do you want to work here?":
            return AnswerAttempt(value="I'm excited about the team's mission.", source="profile")
        return None

    result = fill_and_submit(
        page, PROFILE, RESUME_BYTES, "resume.pdf", CONFIG, answer_lookup=answer_lookup
    )
    assert result.success is True
    assert result.reason is None
    assert page.locator("#custom_q").input_value() == "I'm excited about the team's mission."
