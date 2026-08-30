"""Orchestrates one auto-apply attempt: resolve the platform, launch a real browser,
navigate to the application, and delegate the actual filling/submission to the shared
engine (fields.py, submit.py, reveal.py).

`fill_and_submit` is deliberately split out from `attempt_apply`: it takes an
already-loaded Playwright `page` and does no navigation or browser lifecycle of its
own. That's the seam automated tests (issue #21) drive directly against local fixture
pages instead of a live application URL, so adapter behavior can be covered
deterministically without ever submitting to a real employer.

Every non-success outcome is logged at INFO with the platform/URL/reason at the point
it's decided, not just on a hard crash — diagnosing which branch fired for which job
used to require reproducing the run live; it shouldn't.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..config import get_settings
from .fields import (
    _fill_answer,
    _UnhandledField,
    count_genuinely_invalid,
    fill_known_fields,
    find_unhandled_required_fields,
    track,
)
from .platforms import (
    LISTING_CLOSED_PATTERN,
    SUCCESS_URL_PATTERN,
    WORKDAY_UNSUPPORTED_REASON,
    PlatformConfig,
    is_workday,
    resolve_platform,
)
from .reveal import ensure_form_present
from .submit import find_submit_control
from .types import AnswerLookup, AutoApplyResult

log = logging.getLogger("meridian.apply_adapters")

# Bounded wait for a concrete "the form is actually here" signal after navigation,
# used instead of wait_until="networkidle" (see platforms.PlatformConfig.wait_until).
_FORM_READY_TIMEOUT_MS = 15_000
# See attempt_apply's call site for why this exists at all — a required-field
# selector resolving confirms the DOM nodes are present, not that the page's own
# JS/React hydration has actually finished.
_HYDRATION_SETTLE_MS = 2_500
# See fill_and_submit's final-gate comment for why this exists — a bound on how
# many times to re-apply every already-resolved field's value in response to
# the page's own :invalid check still finding something wrong, before giving up
# honestly instead of clicking Submit on a form that isn't really valid.
_MAX_FINAL_VALIDATION_ROUNDS = 3
# Bounded wait for the post-submit confirmation to actually appear, polled instead
# of a single fixed sleep. Confirmed live (Ashby/Harvey): the click fires a real
# submission request immediately, but résumé upload + the org's own backend
# validation can measurably outlast a short fixed wait before the confirmation
# panel renders — a fixed 2s sleep silently misreported an application that most
# likely did go through as "confirmation_not_detected", with no way to tell the two
# apart afterward. Polled the same way _FORM_READY_TIMEOUT_MS already is above,
# for the same reason: a concrete signal, checked repeatedly, beats a guessed delay.
_CONFIRMATION_TIMEOUT_MS = 15_000
_CONFIRMATION_POLL_MS = 500
# Bounded wait for each filled field's own async save-to-backend call to actually
# settle before trusting the form's validity or clicking Submit — see the call
# site's comment (just before the final :invalid gate) for why this exists.
_FIELD_SYNC_SETTLE_TIMEOUT_MS = 10_000
_FIELD_SYNC_SETTLE_MS = 1_500
# Ashby's field-value mutations are asynchronous and carry a full form snapshot.
# After a mutation completes, allow a short quiet interval for its React state
# update before changing another field.  This is not a guessed global sleep: the
# preceding wait is tied to Ashby's actual field-save request lifecycle.
_ASHBY_FIELD_SAVE_TIMEOUT_MS = 10_000
_ASHBY_FIELD_SAVE_QUIET_MS = 300
_ASHBY_FIELD_SAVE_START_GRACE_MS = 600
# Confirmed live, and bigger in scope than first thought: Twitch's and GitLab's
# Greenhouse postings (job-boards.greenhouse.io) *and* a live Anthropic posting on
# the same domain all load the exact same reCAPTCHA Enterprise sitekey, with
# size=invisible -- this is Greenhouse's own newer embed applying invisible
# reCAPTCHA platform-wide, not an employer opt-in the way it first appeared.
# "Invisible" mode almost never surfaces a challenge to solve; it scores traffic
# silently in the background and usually still lets the request through, so
# refusing outright on its presence alone would make auto-apply never fire on any
# current Greenhouse posting. A *visible*, genuinely interactive challenge is a
# different matter -- confirmed live on Lever's own opt-in CAPTCHA toggle
# (Symplicity's posting renders a real, always-visible hCaptcha widget, no
# invisible mode) -- automated filling can't solve one of those, and clicking
# Submit anyway risks either silently failing bot-detection server-side or
# hanging on a challenge that never resolves. So only a *visible* CAPTCHA is
# refused before ever touching the form; an invisible one is allowed to proceed,
# and whatever actually happens is reported honestly afterward by the existing
# submission_rejected/listing_closed/confirmation_not_detected checks below.
_CAPTCHA_HOST_PATTERN = re.compile(r"recaptcha|hcaptcha", re.IGNORECASE)
_INVISIBLE_CAPTCHA_MARKER = "size=invisible"
# Element-based fallback for a fixture page (no live src to load) or a widget
# rendered as a static container div rather than an already-loaded iframe.
_CAPTCHA_ELEMENT_SELECTOR = (
    "iframe[title*='captcha' i], iframe[title*='recaptcha' i], .g-recaptcha, .h-captcha"
)
CAPTCHA_PROTECTED_REASON = "captcha_protected"

# Submission endpoints differ by ATS, and GraphQL often puts the operation name
# in the POST body rather than the URL. This deliberately inspects request data
# only in memory for classification; request bodies are never persisted/logged
# because they contain the candidate's personal information.
_SUBMIT_REQUEST_PATTERN = re.compile(
    r"submit(?:application)?|application(?:form)?submit|application[_-]?submit",
    re.IGNORECASE,
)
_ASHBY_FIELD_SAVE_OPERATIONS = frozenset({"ApiSetFormValue", "ApiSetFormValueToFile"})


def _safe_response_url(url: str) -> str:
    """Strips query/fragment data before a submission endpoint is persisted."""
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
    except Exception:
        return url.split("?", 1)[0][:500]


def _response_error(body: str) -> str | None:
    """Returns a bounded server/GraphQL error description, if present."""
    if not body.strip():
        return None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        lowered = body.lower()
        if any(token in lowered for token in ("error", "rejected", "invalid", "failed")):
            return body.strip()[:3000]
        return None
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if errors:
            return json.dumps(errors, ensure_ascii=False)[:3000]
        error = payload.get("error")
        if error:
            return str(error)[:3000]
        if payload.get("success") is False:
            return str(payload.get("message") or payload)[:3000]
        nested_error = _nested_form_error(payload)
        if nested_error:
            return nested_error
    return None


def _nested_form_error(value) -> str | None:
    """Find ATS validation errors embedded inside a GraphQL ``data`` payload.

    Ashby's submit mutation is transport-successful even when its form is
    rejected: it responds with HTTP 200 and non-null ``data``, but puts the
    failure in ``applicationFormResult.errorMessages`` / ``formErrors``.  Search
    those explicitly instead of treating any GraphQL data object as an accepted
    application.  Only response messages are retained; request content is never
    inspected or persisted here.
    """
    if isinstance(value, dict):
        for key in ("errorMessages", "formErrors"):
            if key in value:
                message = _format_nested_error(value[key])
                if message:
                    return message
        for child in value.values():
            message = _nested_form_error(child)
            if message:
                return message
    elif isinstance(value, list):
        for child in value:
            message = _nested_form_error(child)
            if message:
                return message
    return None


def _format_nested_error(value) -> str | None:
    if isinstance(value, str):
        return value.strip()[:3000] or None
    if isinstance(value, dict):
        for key in ("message", "error", "detail"):
            message = value.get(key)
            if isinstance(message, str) and message.strip():
                return message.strip()[:3000]
        return "ATS returned a form validation error"
    if isinstance(value, list):
        for item in value:
            message = _format_nested_error(item)
            if message:
                return message
    return None


def _response_is_explicit_success(status: int, body: str) -> bool:
    if not 200 <= status < 300:
        return False
    if status in (201, 202, 204) or not body.strip():
        return True
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        lowered = body.lower()
        return any(token in lowered for token in ("success", "submitted", "received"))
    if not isinstance(payload, dict):
        return False
    if payload.get("success") is True:
        return True
    # A non-null GraphQL data payload with no errors is the server's explicit
    # success acknowledgement for operations such as ApiApplicationFormSubmit.
    # _response_error has already ruled out Ashby's nested form failures.
    return "data" in payload and payload.get("data") is not None and not payload.get("errors")


class _AshbyFieldSaveBarrier:
    """Serializes Ashby's per-field GraphQL writes without logging their bodies."""

    def __init__(self, page):
        self._page = page
        self._inflight = 0
        self._generation = 0
        self._last_change = time.monotonic()
        page.on("request", self._on_request)
        page.on("requestfinished", self._on_request_finished)
        page.on("requestfailed", self._on_request_finished)

    @staticmethod
    def _is_field_save_request(request) -> bool:
        try:
            if request.method.upper() != "POST":
                return False
            payload = json.loads(request.post_data or "{}")
            return payload.get("operationName") in _ASHBY_FIELD_SAVE_OPERATIONS
        except (AttributeError, TypeError, ValueError):
            return False

    def _on_request(self, request) -> None:
        if self._is_field_save_request(request):
            self._inflight += 1
            self._generation += 1
            self._last_change = time.monotonic()

    def _on_request_finished(self, request) -> None:
        if self._is_field_save_request(request):
            self._inflight = max(0, self._inflight - 1)
            self._generation += 1
            self._last_change = time.monotonic()

    def settle(self) -> None:
        """Wait until a field save has settled, or briefly confirm none was sent.

        A value can be saved before this callback starts; that is already safe
        because it is no longer in flight.  Otherwise we wait for every observed
        save to finish and for React's form state to be quiet before allowing the
        next interaction.  A bounded grace period keeps plain native controls
        (which do not create a save mutation) inexpensive.
        """
        started_at = time.monotonic()
        starting_generation = self._generation
        deadline = started_at + (_ASHBY_FIELD_SAVE_TIMEOUT_MS / 1000)
        while time.monotonic() < deadline:
            self._page.wait_for_timeout(50)
            now = time.monotonic()
            observed = self._generation != starting_generation
            quiet_for_ms = (now - self._last_change) * 1000
            if observed and self._inflight == 0 and quiet_for_ms >= _ASHBY_FIELD_SAVE_QUIET_MS:
                return
            if not observed and (now - started_at) * 1000 >= _ASHBY_FIELD_SAVE_START_GRACE_MS:
                return
        log.warning("Timed out waiting for an Ashby field-save mutation to settle")


@dataclass
class _SubmitObservation:
    outcome: str  # success | rejected | request_failed | ambiguous
    detail: str


class _SubmissionMonitor:
    """Captures only the request triggered by Submit and converts its response to
    a durable, sanitized outcome. Installed immediately before the click, after
    field-save traffic has already settled."""

    def __init__(self, page):
        self._observations: list[_SubmitObservation] = []
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    @staticmethod
    def _is_submit_request(request) -> bool:
        try:
            if request.method.upper() not in ("POST", "PUT", "PATCH"):
                return False
            signal = f"{request.url}\n{request.post_data or ''}"
            return bool(_SUBMIT_REQUEST_PATTERN.search(signal))
        except Exception:
            return False

    def _on_response(self, response) -> None:
        request = response.request
        if not self._is_submit_request(request):
            return
        endpoint = _safe_response_url(response.url)
        try:
            body = response.text()
        except Exception:
            body = ""
        error = _response_error(body)
        if response.status >= 400 or error:
            evidence = error or body.strip() or response.status_text
            self._observations.append(
                _SubmitObservation(
                    "rejected",
                    f"ATS submit response {response.status} from {endpoint}: {evidence}"[:4000],
                )
            )
        elif _response_is_explicit_success(response.status, body):
            self._observations.append(
                _SubmitObservation(
                    "success",
                    f"ATS submit response {response.status} from {endpoint}",
                )
            )
        else:
            self._observations.append(
                _SubmitObservation(
                    "ambiguous",
                    f"Ambiguous ATS submit response {response.status} from {endpoint}",
                )
            )

    def _on_request_failed(self, request) -> None:
        if not self._is_submit_request(request):
            return
        try:
            failure = request.failure or "unknown network failure"
        except Exception:
            failure = "unknown network failure"
        self._observations.append(
            _SubmitObservation(
                "request_failed",
                f"ATS submit request failed at {_safe_response_url(request.url)}: {failure}"[:4000],
            )
        )

    def terminal(self) -> _SubmitObservation | None:
        for outcome in ("rejected", "request_failed", "success"):
            match = next((item for item in self._observations if item.outcome == outcome), None)
            if match is not None:
                return match
        return None

    def detail(self) -> str | None:
        terminal = self.terminal()
        if terminal is not None:
            return terminal.detail
        if self._observations:
            return self._observations[-1].detail
        return "No identifiable ATS submission response was captured after the click."


def _is_invisible_captcha(size_signal: str) -> bool:
    return "invisible" in (size_signal or "").strip().lower()


def _blocking_captcha_present(page) -> bool:
    """A *visible*, actually-interactive CAPTCHA widget guarding this form --
    never an invisible one, which is excluded on purpose (see the module comment
    above _CAPTCHA_HOST_PATTERN). Checked via both a real iframe's own resolved
    URL (a live reCAPTCHA/hCaptcha embed carries size=invisible in its query
    string when that's how it's configured) and a title/class fallback for a
    static container element, since a fixture page built for local testing has
    no live src to load.

    Confirmed live (GitLab's posting): the same already-rendered anchor iframe
    matches *both* checks above, and its own static `src` attribute carries the
    exact same size=invisible text its resolved frame.url does -- reading a
    *different* signal (a data-size attribute, which a real iframe never
    actually carries; only a not-yet-rendered container div does) for the
    element-based check than the frame-based one made them disagree about the
    identical widget. Both checks now read the same kind of signal per element
    type: an iframe's own src, or a container div's data-size."""
    try:
        for frame in page.frames:
            if _CAPTCHA_HOST_PATTERN.search(frame.url) and not _is_invisible_captcha(frame.url):
                return True
    except Exception:
        pass
    try:
        elements = page.locator(_CAPTCHA_ELEMENT_SELECTOR)
        count = min(elements.count(), 10)
        for i in range(count):
            el = elements.nth(i)
            try:
                is_iframe = el.evaluate("e => e.tagName.toLowerCase()") == "iframe"
            except Exception:
                is_iframe = False
            signal = el.get_attribute("src") if is_iframe else el.get_attribute("data-size")
            if not _is_invisible_captcha(signal):
                return True
        return False
    except Exception:
        return False


def _captcha_present(page) -> bool:
    """Whether a CAPTCHA is present at all, including invisible variants.

    Invisible reCAPTCHA is allowed to proceed initially because it often produces
    a token without user interaction.  If no submission request or confirmation
    ever follows a completed form, however, its presence is important diagnostic
    evidence: the browser-side CAPTCHA gate can be suppressing the click before
    the ATS receives anything.
    """
    try:
        if any(_CAPTCHA_HOST_PATTERN.search(frame.url) for frame in page.frames):
            return True
    except Exception:
        pass
    try:
        return page.locator(_CAPTCHA_ELEMENT_SELECTOR).count() > 0
    except Exception:
        return False


def _give_up(
    apply_url: str, ats_platform: str | None, reason: str, detail: str | None = None
) -> AutoApplyResult:
    log.info("Auto-apply skipped for %s (%s): %s", apply_url, ats_platform, reason)
    return AutoApplyResult(False, reason, detail=detail)


def _allow_submit_past_excusable_native_proxies(page, submit) -> bool:
    """Disable browser-native validation only for already-excused proxy inputs.

    Greenhouse can retain hidden, id-less, name-less ``required`` inputs beside a
    correctly selected react-select control.  They are internal validation
    plumbing, not answerable fields, and ``count_genuinely_invalid`` has already
    established that no real field remains incomplete.  A native ``type=submit``
    button otherwise refuses to dispatch its submit handler at all, leaving no
    network request or employer-visible application.

    This is deliberately narrow: it runs only when raw invalid elements exist but
    *every* one is excusable by the final validation model. The ATS still receives
    the complete form and remains authoritative for server-side validation.
    """
    try:
        if page.locator(":invalid").count() == 0 or count_genuinely_invalid(page) != 0:
            return False
        return bool(
            submit.evaluate(
                """control => {
                    const form = control.form || control.closest('form');
                    if (!form) return false;
                    form.noValidate = true;
                    return true;
                }"""
            )
        )
    except Exception:
        return False


def _checkbox_group_name(field: _UnhandledField) -> str | None:
    if field.descriptor.field_type != "checkbox":
        return None
    try:
        return field.locator.get_attribute("name") or None
    except Exception:
        return None


def _checkbox_option_label(field: _UnhandledField) -> str:
    """The option portion of a captured `Group question — Option` label."""
    _, separator, option = field.descriptor.label.rpartition(" — ")
    return option if separator else field.descriptor.label


def _fill_checkbox_groups_with_resolved_answers(
    resolutions: list[tuple[_UnhandledField, object | None]],
) -> set[int]:
    """Apply answered same-name checkbox groups as one logical question.

    Greenhouse marks every checkbox in a required multi-select group as required,
    even though the actual rule is "choose at least one."  The review UI records
    each visible option as Yes/No.  If every answer is an explicit No and the
    group itself offers an exact `None` option, checking `None` is the only
    faithful representation of those answers.  No other option is inferred, and
    a group without that explicit option remains unresolved in the normal path.
    """
    groups: dict[str, list[tuple[_UnhandledField, object | None]]] = {}
    for field, attempt in resolutions:
        name = _checkbox_group_name(field)
        if name:
            groups.setdefault(name, []).append((field, attempt))

    handled: set[int] = set()
    truthy = {"yes", "true", "agree", "checked"}
    falsy = {"no", "false", "unchecked"}
    for members in groups.values():
        if len(members) < 2 or any(attempt is None for _, attempt in members):
            continue
        values = [str(getattr(attempt, "value", "")).strip().casefold() for _, attempt in members]
        if any(value not in truthy | falsy for value in values):
            continue
        selected = [field for (field, _), value in zip(members, values) if value in truthy]
        if not selected:
            none_options = [
                field
                for field, _ in members
                if _checkbox_option_label(field).strip().casefold() in {"none", "none of the above"}
            ]
            if len(none_options) != 1:
                continue
            selected = none_options
        try:
            for field, _ in members:
                if field in selected:
                    field.locator.check()
                    if not field.locator.is_checked():
                        raise RuntimeError("checkbox did not remain checked")
                else:
                    field.locator.uncheck()
            handled.update(id(field) for field, _ in members)
        except Exception:
            # Leave this group to the ordinary per-field path; the final native
            # validation gate will refuse submission if it is still incomplete.
            continue
    return handled


def fill_and_submit(
    page,
    profile: dict,
    resume_bytes: bytes,
    resume_filename: str | None,
    config: PlatformConfig,
    apply_url: str = "",
    answer_lookup: AnswerLookup | None = None,
) -> AutoApplyResult:
    """Pure form-interaction step against an already-loaded page. No top-level
    navigation, no browser lifecycle — safe to call directly from tests against a
    fixture page. `apply_url` is only used for logging context.

    `answer_lookup`, if given, is tried against each required field left over after
    fill_known_fields — e.g. a profile-similarity match (see apply_adapters/
    profile_similarity.py). It must be synchronous and do no network I/O of its own
    for *that* tier; a caller chaining in a live LLM tier means this can still
    block on a bounded-timeout network call per field. This runs while the browser is
    still open, so anything slower (an LLM drafting an answer) happens afterward,
    off AutoApplyResult.unresolved_questions, back in scheduler.py — never here."""
    # Some ATS (confirmed on Ashby) gate the real form behind a landing-page button;
    # this is a no-op if the form is already present or no safe trigger is found.
    ensure_form_present(page)

    if _blocking_captcha_present(page):
        return _give_up(apply_url, config.name, CAPTCHA_PROTECTED_REASON)

    # Ashby's SetFormValue responses contain complete form snapshots.  Keep its
    # writes strictly one-at-a-time so an older response cannot restore a blank
    # value after a newer field has already been selected.
    field_save_barrier = _AshbyFieldSaveBarrier(page) if config.serialize_field_saves else None
    settle_field_save = field_save_barrier.settle if field_save_barrier is not None else None

    filled_ids = fill_known_fields(
        page,
        profile,
        extra_text_labels=config.extra_text_labels,
        after_field_fill=settle_field_save,
    )
    log.debug("Filled known fields for %s (%s): %s", apply_url, config.name, sorted(filled_ids))

    # Confirmed live on Ashby: a page can have more than one input[type='file'] —
    # e.g. an unrelated "upload your resume to see your match score" widget
    # elsewhere on the job posting, alongside the real application's own upload
    # field. Blindly taking .first risked silently attaching the résumé to the
    # wrong control while the real, required upload field stayed empty (surfacing
    # later as a misleading "custom_questions" instead of what it actually was).
    # The real one is reliably marked required; only fall back to the bare .first
    # match for a page that has just the one (unmarked) upload input.
    resume_input = page.locator(
        "input[type='file'][required], input[type='file'][aria-required='true']"
    ).first
    if resume_input.count() == 0:
        resume_input = page.locator("input[type='file']").first
    if resume_input.count() == 0:
        return _give_up(apply_url, config.name, "form_not_found")
    resume_input.set_input_files(
        files=[
            {
                "name": resume_filename or "resume.pdf",
                "mimeType": "application/pdf",
                "buffer": resume_bytes,
            }
        ]
    )
    track(filled_ids, resume_input)
    if settle_field_save is not None:
        settle_field_save()

    unhandled = find_unhandled_required_fields(page, filled_ids)
    # Resolve once up front so same-name checkboxes can be applied atomically as
    # one multi-select question instead of independent Yes/No toggles.
    resolutions = [
        (field, answer_lookup(field.descriptor) if answer_lookup else None) for field in unhandled
    ]
    grouped_checkbox_fields = (
        _fill_checkbox_groups_with_resolved_answers(resolutions)
        if not config.serialize_field_saves
        else set()
    )
    still_unhandled = []
    # Every field resolved via answer_lookup this attempt, kept around (not just
    # its descriptor) so the final validation pass below can re-apply the exact
    # same value if a later field's own interaction turns out to have reverted
    # this one — see that pass's comment for why re-applying, not just
    # re-checking, is necessary.
    resolved_fields: list[tuple[_UnhandledField, str]] = []
    for field, attempt in resolutions:
        if id(field) in grouped_checkbox_fields:
            filled_ids.add(field.identity)
            continue
        if attempt is not None and _fill_answer(page, field, attempt.value):
            filled_ids.add(field.identity)
            if settle_field_save is not None:
                settle_field_save()
            log.debug(
                "Resolved %r (%s) via %s, confidence=%.2f",
                field.descriptor.label,
                field.descriptor.field_type,
                attempt.source,
                attempt.confidence,
            )
            resolved_fields.append((field, attempt.value))
            continue
        log.debug(
            "Unresolved required field: %r (%s)%s",
            field.descriptor.label,
            field.descriptor.field_type,
            " — no answer_lookup given" if answer_lookup is None else " — no confident match",
        )
        still_unhandled.append(field.descriptor)
    if still_unhandled:
        log.info(
            "Auto-apply skipped for %s (%s): custom_questions (%d unresolved)",
            apply_url,
            config.name,
            len(still_unhandled),
        )
        return AutoApplyResult(False, "custom_questions", unresolved_questions=still_unhandled)

    # Confirmed live on Ashby: every field fill triggers its own async
    # save-to-backend call (a GraphQL mutation per field), and filling a whole form
    # in the seconds it takes Playwright to do it can genuinely outrun those
    # round-trips — the DOM, the input's own live value, and even the page's own
    # React state for that field all agreed the value was correct, yet Ashby's
    # own pre-submit validation still rejected the click with "Missing entry for
    # required field: Email", purely because its authoritative form-state hadn't
    # finished catching up yet. Not a fixed sleep (see this file's other timing
    # comments for why): wait for the page's own in-flight requests to actually
    # settle, bounded, so a page with unrelated persistent background chatter
    # can't hang this indefinitely.
    try:
        page.wait_for_load_state("networkidle", timeout=_FIELD_SYNC_SETTLE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(_FIELD_SYNC_SETTLE_MS)

    # Final gate, using the browser's own judgment instead of ours: every field
    # above reported success, but confirmed live (a Greenhouse posting with
    # several react-select comboboxes in a row) that filling field N+1 can
    # silently revert field N's already-verified, already-committed selection —
    # not just a one-off click failure, a genuine cascading side effect between
    # sequential fields on the same page. So a single validation pass isn't
    # enough either: re-apply every resolved field's own already-known value
    # again (never re-deriving a new one — no repeat bank/LLM calls) and
    # re-check, bounded, until the page's own :invalid judgment is clean or the
    # retries run out. Never clicks Submit on a form this check can prove is
    # still broken. Uses count_genuinely_invalid, not a raw `:invalid` count —
    # see its own docstring for the one confirmed case (a Greenhouse "select all
    # that apply" checkbox group marking every option individually required) where
    # the raw count is a false positive on a correctly-filled form.
    invalid_count = count_genuinely_invalid(page)
    retry_round = 0
    while invalid_count > 0 and retry_round < _MAX_FINAL_VALIDATION_ROUNDS:
        for field, value in resolved_fields:
            if _fill_answer(page, field, value) and settle_field_save is not None:
                settle_field_save()
        invalid_count = count_genuinely_invalid(page)
        retry_round += 1
    if invalid_count > 0:
        log.info(
            "Auto-apply skipped for %s (%s): %d field(s) still invalid per the page's own"
            " validation after %d re-validation round(s), despite every fill call"
            " reporting success",
            apply_url,
            config.name,
            invalid_count,
            retry_round,
        )
        return _give_up(apply_url, config.name, "fields_invalid_before_submit")

    submit = find_submit_control(page)
    if submit is None and config.submit_fallback_selector:
        fallback = page.locator(config.submit_fallback_selector)
        submit = fallback.first if fallback.count() == 1 else None
    if submit is None:
        return _give_up(apply_url, config.name, "submit_not_found")

    _allow_submit_past_excusable_native_proxies(page, submit)
    monitor = _SubmissionMonitor(page)
    submit.click()
    confirmed_by_text = confirmed_by_url = False
    submission_observation = None
    elapsed_ms = 0
    while elapsed_ms < _CONFIRMATION_TIMEOUT_MS:
        page.wait_for_timeout(_CONFIRMATION_POLL_MS)
        elapsed_ms += _CONFIRMATION_POLL_MS
        confirmed_by_text = page.get_by_text(config.confirmation_pattern).count() > 0
        confirmed_by_url = bool(SUCCESS_URL_PATTERN.search(page.url))
        submission_observation = monitor.terminal()
        if confirmed_by_text or confirmed_by_url or submission_observation is not None:
            break
    if confirmed_by_text or confirmed_by_url:
        return AutoApplyResult(True, detail=monitor.detail())
    if submission_observation is not None:
        if submission_observation.outcome == "success":
            return AutoApplyResult(True, detail=submission_observation.detail)
        reason = (
            "submission_request_failed"
            if submission_observation.outcome == "request_failed"
            else "submission_rejected"
        )
        return _give_up(apply_url, config.name, reason, submission_observation.detail)

    # No success signal within the timeout -- two more specific, more actionable
    # signals are checked before falling back to the narrow, genuinely ambiguous
    # remainder below, so "why" is diagnosable from the reason alone rather than
    # one catch-all bucket covering several different real situations.
    if page.get_by_text(LISTING_CLOSED_PATTERN).count() > 0:
        # The posting itself closed out from under this attempt -- definitely not
        # submitted, and retrying this same job later won't help either.
        return _give_up(apply_url, config.name, "listing_closed")
    if count_genuinely_invalid(page) > 0:
        # The same validation error(s) the pre-submit gate above already checked
        # are *still* present after clicking Submit -- the click did not actually
        # go through, a materially more confident "did not submit" signal than
        # silence, not just a slower confirmation than we waited for.
        return _give_up(apply_url, config.name, "submission_rejected", monitor.detail())
    if _captcha_present(page):
        # The form was complete and no submit response was observed, but an
        # invisible CAPTCHA was loaded. It may have withheld a token or escalated
        # to an interaction that a browser automation session cannot complete.
        # Report that concrete gate rather than implying the application may have
        # been submitted without any server evidence.
        return _give_up(
            apply_url,
            config.name,
            CAPTCHA_PROTECTED_REASON,
            "CAPTCHA present but no ATS submission response or confirmation was detected",
        )
    # Neither of the above: the click fired cleanly and nothing on the page says
    # it failed -- most likely it *did* go through, this is just the case where no
    # confirmation text/URL this codebase recognizes ever rendered in time.
    return _give_up(apply_url, config.name, "confirmation_not_detected", monitor.detail())


def attempt_apply(
    apply_url: str,
    ats_platform: str | None,
    profile: dict,
    resume_bytes: bytes | None,
    resume_filename: str | None,
    answer_lookup: AnswerLookup | None = None,
) -> AutoApplyResult:
    """Resolves the adapter for `ats_platform`, launches a real browser, and attempts
    one full apply. Workday is refused outright before any resolution happens — see
    platforms.WORKDAY_UNSUPPORTED_REASON for why. `answer_lookup` is forwarded to
    fill_and_submit unchanged — see its docstring."""
    if is_workday(ats_platform):
        return _give_up(apply_url, ats_platform, WORKDAY_UNSUPPORTED_REASON)
    if not resume_bytes:
        return _give_up(apply_url, ats_platform, "no_resume_file")
    config = resolve_platform(ats_platform)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=get_settings().crawler_headless,
                slow_mo=get_settings().crawler_slow_mo_ms,
            )
            try:
                page = browser.new_page()
                page.goto(apply_url, wait_until=config.wait_until, timeout=45_000)
                # domcontentloaded fires as soon as the HTML is parsed, before a
                # client-rendered form necessarily exists — give it a bounded extra
                # window rather than assuming it's ready immediately. A timeout here
                # isn't fatal: fill_and_submit's own reveal-step/form_not_found
                # handling takes it from there.
                try:
                    page.wait_for_selector(
                        "input[type='file'], [required], [aria-required='true']",
                        timeout=_FORM_READY_TIMEOUT_MS,
                    )
                except PlaywrightTimeoutError:
                    pass
                # Confirmed live (GitLab's Greenhouse posting): the required-field
                # selector above resolving only means those DOM nodes exist, not
                # that the page's own JS has finished hydrating — interacting with
                # a react-select combobox this early silently no-ops (the same
                # "first interaction" flakiness _fill_react_select's own per-field
                # retry already works around, just showing up harder here: with
                # several comboboxes on one page, the *first* one hit eats this
                # miss almost every time). A short settle wait before any fill
                # attempt starts fixed it 4/4 in repeated live testing, vs. maybe
                # 1/3 relying on per-field retries alone.
                page.wait_for_timeout(_HYDRATION_SETTLE_MS)
                return fill_and_submit(
                    page,
                    profile,
                    resume_bytes,
                    resume_filename,
                    config,
                    apply_url,
                    answer_lookup=answer_lookup,
                )
            finally:
                browser.close()
    except PlaywrightTimeoutError:
        return _give_up(apply_url, ats_platform, "navigation_timeout")
    except Exception:
        log.exception("Unexpected auto-apply failure for %s (%s)", apply_url, ats_platform)
        return AutoApplyResult(False, "unexpected_error")
