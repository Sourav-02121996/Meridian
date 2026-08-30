"""Mocks httpx.post so this suite never needs a real Ollama instance running —
verifies llm_drafting.draft_answer degrades to None (never raises) on every failure
mode, and only ever returns a value that actually satisfies the question's own
constraints (grounded, verbatim option match, a checkbox answer normalized to
Yes/No, and never an EEO self-identification question)."""

from unittest.mock import MagicMock, patch

from app.apply_adapters import QuestionDescriptor
from app.config import get_settings
from app.llm_drafting import (
    draft_answer,
    draft_educated_guess,
    draft_from_context,
    is_sensitive_question,
)

RESUME = "Experienced backend engineer with 5 years in distributed systems."


def _enable_drafting(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_answer_drafting_enabled", True)


def _chat_response(content: str) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": content}}
    return response


def test_feature_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_answer_drafting_enabled", False)
    question = QuestionDescriptor(label="Why do you want to work here?", field_type="text")
    assert draft_answer(question, RESUME) is None


def test_connection_error_returns_none_not_raises(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to work here?", field_type="text")
    with patch("app.llm_drafting.httpx.post", side_effect=ConnectionError("no Ollama running")):
        assert draft_answer(question, RESUME) is None


def test_unknown_response_returns_none(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="What's your favorite color?", field_type="text")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("UNKNOWN")):
        assert draft_answer(question, RESUME) is None


def test_select_response_not_matching_options_returns_none(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(
        label="How did you hear about us?", field_type="select", options=["LinkedIn", "Referral"]
    )
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Twitter")):
        assert draft_answer(question, RESUME) is None


def test_select_response_matching_an_option_is_normalized(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(
        label="How did you hear about us?", field_type="select", options=["LinkedIn", "Referral"]
    )
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response(" linkedin ")):
        assert draft_answer(question, RESUME) == "LinkedIn"


def test_checkbox_response_is_drafted(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="I agree to the terms of service", field_type="checkbox")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Yes")):
        assert draft_answer(question, RESUME) == "Yes"


def test_checkbox_response_outside_yes_no_vocabulary_returns_none(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="I agree to the terms of service", field_type="checkbox")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Maybe")):
        assert draft_answer(question, RESUME) is None


def test_eeo_question_is_never_drafted(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Gender", field_type="text")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Female")) as mock_post:
        assert draft_answer(question, RESUME) is None
        mock_post.assert_not_called()


def test_eeo_question_not_in_fixed_alias_list_is_still_recognized(monkeypatch):
    """is_eeo_question matches by keyword, not just the exact EEO_LABELS strings —
    a custom-phrased veteran question should still be refused."""
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(
        label="Are you a protected veteran under VEVRAA?",
        field_type="select",
        options=["Yes", "No"],
    )
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Yes")) as mock_post:
        assert draft_answer(question, RESUME) is None
        mock_post.assert_not_called()


def test_valid_text_response_is_returned(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to work here?", field_type="text")
    with patch(
        "app.llm_drafting.httpx.post",
        return_value=_chat_response("I'm excited about the team's mission."),
    ):
        assert draft_answer(question, RESUME) == "I'm excited about the team's mission."


def test_profile_context_reaches_the_prompt(monkeypatch):
    """The actual fix for the "Country" -> wrong-guess problem: a profile value
    must show up in what's sent to Ollama, not just the résumé."""
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Country*", field_type="text")
    profile = {"location": "Boston, MA, USA"}
    with patch(
        "app.llm_drafting.httpx.post", return_value=_chat_response("United States")
    ) as mock_post:
        draft_answer(question, RESUME, profile)

    sent_prompt = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "Candidate Profile" in sent_prompt
    assert "Boston, MA, USA" in sent_prompt


def test_eeo_and_cover_letter_never_reach_the_prompt_even_if_present_in_profile(monkeypatch):
    """_PROFILE_CONTEXT_KEYS is the single source of truth for what's exposed —
    a caller accidentally passing a fuller profile dict (as scheduler.py's own
    _build_profile() does) must not leak EEO/cover-letter data into the prompt."""
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to work here?", field_type="text")
    profile = {
        "location": "Boston, MA, USA",
        "gender": "Male",
        "race_ethnicity": "Asian",
        "veteran_status": "I am not a veteran",
        "disability_status": "No, I do not have a disability",
        "cover_letter": "Dear hiring manager, ...",
    }
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Answer")) as mock_post:
        draft_answer(question, RESUME, profile)

    sent_prompt = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    for leaked in ("Male", "Asian", "not a veteran", "disability", "hiring manager"):
        assert leaked not in sent_prompt


def test_no_profile_still_works(monkeypatch):
    """Existing callers that don't pass a profile at all (none did before this
    change) must keep working exactly as before."""
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to work here?", field_type="text")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Answer")) as mock_post:
        assert draft_answer(question, RESUME) == "Answer"

    sent_prompt = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "Candidate Profile" not in sent_prompt


# ---------------------------------------------------------------------------
# is_sensitive_question
# ---------------------------------------------------------------------------


def test_is_sensitive_question_recognizes_visa_sponsorship():
    assert is_sensitive_question("Will you now or in the future require sponsorship?")


def test_is_sensitive_question_recognizes_background_check():
    assert is_sensitive_question("Do you consent to a background check?")


def test_is_sensitive_question_recognizes_security_clearance():
    assert is_sensitive_question("Do you currently hold a security clearance?")


def test_is_sensitive_question_recognizes_criminal_history():
    assert is_sensitive_question("Have you ever been convicted of a felony?")


def test_is_sensitive_question_false_for_unrelated_question():
    assert not is_sensitive_question("Why do you want to work here?")


# ---------------------------------------------------------------------------
# draft_from_context (Tier C's grounded-in-retrieved-excerpts drafter)
# ---------------------------------------------------------------------------


def test_draft_from_context_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_answer_drafting_enabled", False)
    question = QuestionDescriptor(label="Where did you study?", field_type="text")
    assert draft_from_context(question, "B.S. Computer Science, State University") is None


def test_draft_from_context_grounded_in_provided_excerpt(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Where did you study?", field_type="text")
    with patch(
        "app.llm_drafting.httpx.post", return_value=_chat_response("State University")
    ) as mock_post:
        assert draft_from_context(question, "B.S. Computer Science, State University") == (
            "State University"
        )
    sent_prompt = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "Relevant résumé excerpts" in sent_prompt
    assert "State University" in sent_prompt


def test_draft_from_context_excludes_eeo_questions(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Gender", field_type="text")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Female")) as mock_post:
        assert draft_from_context(question, "some context") is None
        mock_post.assert_not_called()


def test_draft_from_context_excludes_sensitive_questions(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Do you require visa sponsorship?", field_type="text")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("No")) as mock_post:
        assert draft_from_context(question, "some context") is None
        mock_post.assert_not_called()


def test_draft_from_context_unknown_returns_none(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="What's your shoe size?", field_type="text")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("UNKNOWN")):
        assert draft_from_context(question, "some unrelated context") is None


def test_draft_from_context_returns_none_for_empty_context(monkeypatch):
    _enable_drafting(monkeypatch)
    question = QuestionDescriptor(label="Where did you study?", field_type="text")
    with patch("app.llm_drafting.httpx.post") as mock_post:
        assert draft_from_context(question, "   ") is None
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# draft_educated_guess (Tier D's permissive drafter)
# ---------------------------------------------------------------------------


def _enable_guessing(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_educated_guess_enabled", True)


def test_draft_educated_guess_disabled_by_default_returns_none(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_educated_guess_enabled", False)
    question = QuestionDescriptor(label="Why do you want to join Twitch?", field_type="text")
    with patch("app.llm_drafting.httpx.post") as mock_post:
        assert draft_educated_guess(question, RESUME) is None
        mock_post.assert_not_called()


def test_draft_educated_guess_excludes_eeo_questions(monkeypatch):
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(label="Gender", field_type="text")
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Female")) as mock_post:
        assert draft_educated_guess(question, RESUME) is None
        mock_post.assert_not_called()


def test_draft_educated_guess_excludes_sensitive_questions(monkeypatch):
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(
        label="Do you have an active security clearance?", field_type="text"
    )
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Yes")) as mock_post:
        assert draft_educated_guess(question, RESUME) is None
        mock_post.assert_not_called()


def test_draft_educated_guess_includes_job_context_in_prompt_and_system_message(monkeypatch):
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to join Twitch?", field_type="text")
    with patch(
        "app.llm_drafting.httpx.post",
        return_value=_chat_response("I'm excited about live, interactive community platforms."),
    ) as mock_post:
        draft_educated_guess(
            question,
            RESUME,
            job_title="Software Engineer I, Payments",
            job_company="Twitch",
            job_description="Build payment infrastructure for creators.",
        )

    sent = mock_post.call_args.kwargs["json"]["messages"]
    system_message, user_message = sent[0]["content"], sent[1]["content"]
    assert "Twitch" in system_message
    assert "Software Engineer I, Payments" in system_message
    assert "Twitch" in user_message
    assert "Build payment infrastructure for creators." in user_message
    assert "UNKNOWN is never an acceptable response" in system_message


def test_draft_educated_guess_answers_a_subjective_question_the_grounded_tier_could_not(
    monkeypatch,
):
    """The whole point of this tier: draft_answer would return UNKNOWN for this
    exact question (nothing in a résumé says why a candidate wants to join a
    specific company) -- draft_educated_guess must not."""
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to join Twitch?", field_type="text")
    with patch(
        "app.llm_drafting.httpx.post",
        return_value=_chat_response("I love how Twitch turns viewers into communities."),
    ):
        answer = draft_educated_guess(question, RESUME, job_company="Twitch")
    assert answer == "I love how Twitch turns viewers into communities."


def test_draft_educated_guess_never_leaves_a_select_field_blank(monkeypatch):
    """Even when the model's raw response doesn't match any offered option
    verbatim, this tier must still pick one of the form's own real options --
    never None -- see _closest_option."""
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(
        label="How many years of professional experience do you have?",
        field_type="select",
        options=["0-2 years", "3-5 years", "6-10 years", "10+ years"],
    )
    # The model answers in free prose instead of picking a literal option.
    with (
        patch(
            "app.llm_drafting.httpx.post",
            return_value=_chat_response("About five years of experience"),
        ),
        patch(
            "app.llm_drafting.embed_text",
            side_effect=lambda text: {
                "About five years of experience": [0.0, 1.0, 0.0],
                "0-2 years": [1.0, 0.0, 0.0],
                "3-5 years": [0.0, 0.9, 0.1],
                "6-10 years": [0.0, 0.0, 1.0],
                "10+ years": [-1.0, 0.0, 0.0],
            }[text],
        ),
    ):
        answer = draft_educated_guess(question, RESUME)
    assert answer == "3-5 years"


def test_draft_educated_guess_defaults_an_unparseable_checkbox_to_yes(monkeypatch):
    """Never one of the permanently-excluded sensitive consents (those never reach
    this function at all) -- for a benign required acknowledgment checkbox,
    defaulting to Yes is what lets an otherwise-complete application actually
    submit, rather than getting stuck on one box forever."""
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(
        label="I certify the information provided is accurate", field_type="checkbox"
    )
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response("Sure, that's fine")):
        assert draft_educated_guess(question, RESUME) == "Yes"


def test_draft_educated_guess_truncates_long_free_text_at_a_sentence_boundary(monkeypatch):
    """The regression this fix is actually about: a real live Ollama response cut
    off mid-word ("...I appreciate the com") at a hard _MAX_ANSWER_LENGTH — a
    genuine, coherent answer must never be submitted looking broken like that."""
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to join Twitch?", field_type="text")
    long_answer = (
        "I'm drawn to Twitch's community-first approach to live streaming. "
        "My background building payment infrastructure lines up well with this role. "
        + "Filler sentence to push this well past the length limit. "
        * 20
    )
    with patch("app.llm_drafting.httpx.post", return_value=_chat_response(long_answer)):
        answer = draft_educated_guess(question, RESUME, job_company="Twitch")
    assert answer is not None
    assert answer.endswith(".")  # never cut off mid-sentence/mid-word
    assert len(answer) < len(long_answer)  # actually truncated, not passed through whole


def test_draft_educated_guess_returns_none_when_the_call_fails(monkeypatch):
    _enable_guessing(monkeypatch)
    question = QuestionDescriptor(label="Why do you want to join Twitch?", field_type="text")
    with patch("app.llm_drafting.httpx.post", side_effect=ConnectionError("no Ollama running")):
        assert draft_educated_guess(question, RESUME) is None
