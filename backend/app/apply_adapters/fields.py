"""Shared, ATS-agnostic form-filling engine.

Every field is matched by its accessible label text rather than a hardcoded element
id, since that's what generalizes across Greenhouse/Lever/Ashby/etc. (and holds up
under markup changes) instead of assuming any one platform's DOM structure. Nothing
in here is platform-specific — a platform's `PlatformConfig` (see platforms.py) may
extend `TEXT_LABELS` with extra label aliases for its own known phrasing, but the
matching logic itself is shared by every named adapter *and* the generic fallback
used for any ATS/career-page URL that has no dedicated config.

Every field we successfully fill is tracked by the actual DOM element's `id`/`name`,
not a fuzzy keyword guess — so the "did we leave a required field unanswered" check
at the end is a precise match against what we actually touched, not a heuristic.
"""

import logging
import re
from dataclasses import dataclass
from typing import Callable, Literal

from .types import QuestionDescriptor

log = logging.getLogger("meridian.apply_adapters")


def _by_id(page, id_value: str):
    """A locator for an element by id, safe for *any* id value — including ones
    containing CSS-special characters. Confirmed live: Greenhouse names
    checkbox-group options like `question_37744068002[]_251382496002` (an
    array-style id for a multi-select field), which a raw `f"#{id}"` CSS id
    selector can't parse at all — `[` opens an attribute-selector clause in CSS
    syntax, not a literal character in an id, so the whole selector is a syntax
    error rather than silently matching nothing. An attribute-equality selector
    with the value treated as a quoted CSS string literal handles this (and
    dots, colons, spaces, anything else) correctly instead — every place in this
    file that re-binds to an id after resolving an element must use this, not
    string-interpolate directly into a `#`-selector."""
    escaped = id_value.replace("\\", "\\\\").replace('"', '\\"')
    return page.locator(f'[id="{escaped}"]')


def _radios_by_name(page, name_value: str):
    """Returns one logical native-radio group without assuming its name is safe
    to interpolate into a CSS selector. Ashby currently uses generated names, but
    treating the value as data keeps this stable for every ATS and mirrors
    `_by_id`'s handling of bracketed Greenhouse identifiers."""
    escaped = name_value.replace("\\", "\\\\").replace('"', '\\"')
    return page.locator(f'input[type="radio"][name="{escaped}"]')


# Simple text/textarea/select fields, keyed by the profile dict key. Values are label
# substrings to try in order (Playwright's get_by_label matching is case-insensitive
# and substring-based with exact=False, which tolerates minor phrasing differences
# across companies).
TEXT_LABELS = {
    "email": ["Email"],
    "phone": ["Phone", "Mobile Number", "Mobile Phone", "Cell Phone", "Telephone"],
    "linkedin": ["LinkedIn Profile", "LinkedIn URL", "LinkedIn"],
    "portfolio_url": ["Website", "Portfolio", "Personal Website", "Portfolio URL"],
    "github_url": ["GitHub"],
    "location": ["Location", "Current Location"],
    # The address family is deliberately absent: each part needs handling this
    # generic table can't give it — a suggestion dropdown to select from
    # (address), exact rather than substring option matching (state/country,
    # since "CA" is a substring of "North Carolina"), or real keystrokes rather
    # than .fill() (address2/zip/city/county). See ADDRESS_LABELS and friends
    # below, with fill_address / fill_exact_choice / fill_typed.
    "current_company": ["Current Company", "Current Employer"],
    "current_title": ["Current Title", "Current Job Title"],
    "desired_salary": ["Desired Salary", "Salary Expectation", "Compensation Expectation"],
    "start_date": [
        "Earliest Start Date",
        "Available Start Date",
        "Start Date",
        "Available to Start",
    ],
    "citizenship": ["Citizenship", "Country of Citizenship"],
    "security_clearance": ["Security Clearance", "Clearance Level", "Active Security Clearance"],
}
# Confirmed live: a "Country" combobox's real options are full country names
# ("United States"), never an abbreviation — an exact/unique-prefix match (see
# _fill_react_select) against a workspace profile value of "USA"/"US" never
# matches anything, silently leaving an otherwise-fully-answerable field to
# fall back to the LLM (inconsistent) or human review (unnecessary) instead of
# Tier A resolving it deterministically. Scoped to a small, explicit alias table
# rather than fuzzy-matching country names generally — a wrong guess here would
# submit a factually incorrect country to a real employer.
_COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "united states of america": "United States",
    "america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
}


def canonicalize_country(value: str) -> str:
    """Maps a common country abbreviation/alias to the full name most ATS
    country pickers actually offer as a selectable option. Anything not in the
    (deliberately small, explicit) alias table is returned unchanged — never
    fuzzy-matched or guessed, since a wrong country is a factual claim, not a
    stylistic one."""
    return _COUNTRY_ALIASES.get(value.strip().lower(), value)


# Reverse of _COUNTRY_ALIASES: canonical full name -> every alias that maps to it.
# Used by _country_name_variants so an address-suggestion widget's own preferred
# style (Google Places suffixes US addresses with "USA", not "United States") is
# still recognized as a match against a profile that stores the canonical form.
_COUNTRY_ALIAS_REVERSE: dict[str, list[str]] = {}
for _alias, _full in _COUNTRY_ALIASES.items():
    _COUNTRY_ALIAS_REVERSE.setdefault(_full.lower(), []).append(_alias)


def _country_name_variants(value: str) -> list[str]:
    """Every plausible way this country could appear in a suggestion or be
    typed into a field, canonical form first — it's the most correct value to
    submit, so it must be candidates[0] rather than whichever alias an
    unordered set happened to yield. Deduplicated, order preserved."""
    if not value:
        return []
    canonical = canonicalize_country(value)
    ordered = [canonical, value, *_COUNTRY_ALIAS_REVERSE.get(canonical.lower(), [])]
    seen: set[str] = set()
    variants = []
    for variant in ordered:
        if variant and variant.lower() not in seen:
            seen.add(variant.lower())
            variants.append(variant)
    return variants


# Most-specific first, bare "Address" last: get_by_label matches substrings, so
# the generic "Address" alias also matches "Address Line 2" and can resolve to
# the wrong field on a form with both. A specific phrasing matching first never
# reaches it, since the caller returns as soon as an alias succeeds.
ADDRESS_LABELS = ["Street Address", "Address Line 1", "Home Address", "Address"]
COUNTRY_LABELS = ["Country"]
STATE_LABELS = ["State", "State/Province", "Province"]
ADDRESS2_LABELS = ["Address Line 2", "Apartment", "Apt/Suite", "Suite", "Unit", "Flat"]
ZIP_LABELS = ["Zip", "Zip Code", "Postal Code", "ZIP/Postal Code", "Pin Code", "PIN"]
CITY_LABELS = ["City"]
COUNTY_LABELS = ["County"]

# Full name <-> two-letter code, both directions, for US states/DC/Puerto Rico. An
# ATS's State field can offer either style as its real options ("California" or
# "CA") and there's no way to know which without asking the live page, so both
# forms are tried — see _state_name_variants and fill_exact_choice.
_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico",
}  # fmt: skip
_US_STATE_ABBR_TO_FULL = {abbr.lower(): full for abbr, full in _US_STATES.items()}
_US_STATE_FULL_TO_ABBR = {full.lower(): abbr for abbr, full in _US_STATES.items()}


def _state_name_variants(value: str) -> list[str]:
    """Both plausible forms for a state value, as-typed first: if the profile
    stored an abbreviation, also try its full name (and vice versa). Anything
    not recognized as either form of a US state is returned as a single
    candidate, unchanged — never guessed."""
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in _US_STATE_ABBR_TO_FULL:
        return [stripped, _US_STATE_ABBR_TO_FULL[lowered]]
    if lowered in _US_STATE_FULL_TO_ABBR:
        return [stripped, _US_STATE_FULL_TO_ABBR[lowered]]
    return [stripped]


# Yes/No eligibility questions. Label text is matched loosely against however the
# company phrased the question; only ever fills a value we actually have on file.
# Aliases below are common recurring phrasings confirmed across Greenhouse/Lever/
# Ashby-style forms — widening these directly shrinks how often a legitimate,
# generic screening question gets misclassified as "custom_questions" (issue #19
# follow-up), rather than trying to answer genuinely novel questions.
YES_NO_LABELS = {
    "work_authorized": [
        "authorized to work",
        "legally authorized to work",
        "eligible to work",
    ],
    "visa_sponsorship": [
        "require sponsorship",
        "visa sponsorship",
        "need sponsorship",
        "sponsorship now or in the future",
        "will you now or in the future require sponsorship",
    ],
    "willing_to_relocate": ["willing to relocate", "open to relocation"],
    "is_18_or_older": ["18 years", "at least 18", "18 years of age"],
    "background_check_consent": [
        "consent to a background check",
        "willing to undergo a background check",
        "background investigation",
    ],
    "drug_test_consent": [
        "consent to a drug test",
        "willing to undergo a drug test",
        "drug screening",
    ],
    "criminal_history": [
        "convicted of a crime",
        "criminal history",
        "felony conviction",
        "ever been convicted",
    ],
}
# Voluntary EEO self-identification. Left entirely alone unless the workspace profile
# has an explicit, non-blank answer — never inferred or defaulted.
EEO_LABELS = {
    "gender": ["Gender"],
    "race_ethnicity": ["Race", "Ethnicity", "Hispanic or Latino"],
    "veteran_status": ["Veteran", "Protected Veteran"],
    "disability_status": ["Disability", "Voluntary Self-Identification of Disability"],
}
COVER_LETTER_LABELS = ["Cover Letter"]
# Tried only if neither "First Name" nor "Last Name" matched anything — some ATS
# (confirmed on Lever and, per-job, Ashby) render one combined field instead of a
# split pair, sometimes labeled as plainly as "Name". "Full Name" is tried first since
# it's specific enough not to collide with an unrelated field (e.g. "Company Name");
# bare "Name" is last since it's the substring-matching label search's broadest and
# riskiest case.
FULL_NAME_LABELS = ["Full Name", "Name"]


# Some ATS render a Yes/No screening question as a plain button pair instead of
# either a native radio group or a react-select-style combobox — confirmed live on
# Ashby's "Are you authorized to work in the country this role is listed in?":
# `<button data-option="yes/no">` (no `role="radio"`), paired with a `<label for="...">`
# whose target id doesn't exist anywhere else in the DOM (the only element carrying
# that identifier is a hidden proxy `<input type="checkbox">`'s *name*, not its id),
# and neither the buttons nor that proxy input ever carry `required`/`aria-required`.
# That combination meant this shape was invisible twice over: fill_yes_no's
# get_by_label()/role="radio" search never found it, *and*
# find_unhandled_required_fields's `[required], [aria-required='true']` scan never
# flagged it either — so it silently reached Submit unanswered, and the only visible
# symptom was Ashby's own client-side validation quietly refusing to submit, showing
# up here as an opaque "confirmation_not_detected" with no indication which question
# actually caused it. Detected structurally (a container holding exactly one
# `button[data-option='yes']` and one `button[data-option='no']`) rather than via
# Ashby's own hashed CSS-module class names, which are per-build artifacts, not a
# stable contract — same reasoning as `.select-shell`'s substring tolerance below.
_YESNO_TOGGLE_YES_SELECTOR = "button[data-option='yes' i]"
_YESNO_TOGGLE_NO_SELECTOR = "button[data-option='no' i]"


def _yesno_toggle_pairs(page) -> list[tuple[object, object, object]]:
    """Every yes/no toggle-button pair on the page, paired up structurally rather
    than by any hardcoded id/class. Returns (yes_button, no_button, container) —
    container is the immediate parent shared by both buttons (and, on Ashby, the
    hidden proxy checkbox), used to scope every other lookup below to just this one
    question's own controls."""
    pairs: list[tuple[object, object, object]] = []
    yes_buttons = page.locator(_YESNO_TOGGLE_YES_SELECTOR)
    count = min(yes_buttons.count(), 20)
    for i in range(count):
        yes_button = yes_buttons.nth(i)
        try:
            container = yes_button.locator("xpath=..").first
            no_button = container.locator(_YESNO_TOGGLE_NO_SELECTOR)
            # Exactly one — anything else is a page structure this pattern wasn't
            # confirmed against; refuse rather than guess which "no" pairs with it.
            if no_button.count() != 1:
                continue
            pairs.append((yes_button, no_button.first, container))
        except Exception:
            continue
    return pairs


def _yesno_toggle_container_entry(container):
    """Walks up from the button pair's own container to the nearest ancestor (or
    itself) that has a real `<label>` somewhere inside it — never trusting the
    label's own `for` attribute to resolve, since that's exactly the confirmed
    failure mode this whole shape is built around (see module comment above)."""
    return container.locator("xpath=ancestor-or-self::*[.//label][1]").first


def _yesno_toggle_label(container) -> str:
    """The pair's own question text, read from the nearest real `<label>` rather
    than any `for`/id resolution."""
    try:
        label = _yesno_toggle_container_entry(container).locator("label").first
        if label.count():
            return (label.inner_text() or "").strip()
    except Exception:
        pass
    return ""


def _yesno_toggle_is_required(container) -> bool:
    """No native required/aria-required attribute exists anywhere in this widget
    (confirmed live) — the only DOM signal Ashby renders is a CSS-module class on
    the question's own `<label>` (observed as `_required_<hash>`, e.g.
    `_required_f7cvd_91`) driving a `::after` asterisk that isn't even real text.
    Matched by substring on "required" rather than the full hashed class name,
    which is a per-build artifact — mirrors `_combobox_selection_confirmed`'s own
    tolerance for `.select-shell`'s hashed suffix below."""
    try:
        label = _yesno_toggle_container_entry(container).locator("label").first
        if label.count():
            classes = (label.get_attribute("class") or "").split()
            return any("required" in cls.lower() for cls in classes)
    except Exception:
        pass
    return False


def _yesno_toggle_answered(yes_button, no_button) -> bool:
    for button in (yes_button, no_button):
        try:
            if (button.get_attribute("aria-pressed") or "").lower() == "true":
                return True
        except Exception:
            pass
    return False


def _yesno_toggle_identity(container) -> str | None:
    """Neither button in the pair carries an id/name of its own (confirmed live) —
    the only stable identity is the hidden proxy checkbox's `name`, the one thing
    that ties this group back to the question it answers. Falls back to None (the
    caller falls back further to the label text) for a page that doesn't have one."""
    try:
        proxy = container.locator("input[type='checkbox']").first
        if proxy.count():
            name = proxy.get_attribute("name")
            if name:
                return name
    except Exception:
        pass
    return None


def _click_yesno_toggle(yes_button, no_button, value: str) -> bool:
    """Clicks whichever button matches value ("Yes"/"No", case-insensitive) and
    verifies via aria-pressed rather than trusting a click that raised no error —
    the same non-negotiable rule every other non-native widget in this file follows
    (see _fill_react_select's own docstring for why a silent click can't be trusted
    on its own)."""
    target = value.strip().lower()
    button = yes_button if target == "yes" else no_button if target == "no" else None
    if button is None:
        return False
    try:
        button.click()
        return (button.get_attribute("aria-pressed") or "").lower() == "true"
    except Exception:
        return False


# The dangling `label for="..."` above turns out not to be specific to the Yes/No
# toggle at all — confirmed live on the same Ashby posting's "Location" field (a
# freeform autocomplete combobox, a completely different control type): a
# `<label for="_systemfield_location">` whose target id exists on no element in the
# DOM (the input itself carries no `id` at all), and — same as the toggle — no
# `required`/`aria-required` anywhere, "required" signaled only by a class on the
# label. This is Ashby's actual, systemic accessibility pattern across its whole
# custom-field library, not a one-off, so get_by_label() can fail on *any* Ashby
# field this way, regardless of what kind of control it turns out to be. This
# fallback finds the control the same way Ashby's own UI does — by DOM proximity to
# the label's own text — instead of `for`/id resolution, for use only once
# get_by_label() has already come back empty (a page with working label
# association never reaches this).
def _label_by_own_text(page, label_text: str):
    """The single `<label>` whose own text matches `label_text`, or None if there
    isn't exactly one — refusing rather than guessing which of several is the real
    question, same as everywhere else in this file that resolves by text. Matches
    `label_text` as a whole word, not a bare substring: a plain substring filter
    let a combobox's own folded-in selected value ("Country" whose label reads
    "Country\nUnited States") match a completely different field's search term
    ("State", found inside "States") — confirmed live, and this filter's own
    word-boundary requirement can only ever narrow its candidate set, never
    admit a new false positive, so it's strictly safer for every existing case
    too (e.g. "Location" no longer coincidentally matches inside "relocation")."""
    try:
        candidates = page.locator("label").filter(
            has_text=re.compile(rf"\b{re.escape(label_text)}\b", re.IGNORECASE)
        )
        # "Location" also occurs inside Harvey's longer relocation question and
        # several radio options. Substring matching therefore returns many labels
        # even though exactly one label's *own complete text* is Location. Prefer
        # that unique exact match before applying the ambiguity guard.
        exact = []
        for i in range(min(candidates.count(), 40)):
            candidate = candidates.nth(i)
            if (candidate.inner_text() or "").strip().lower() == label_text.strip().lower():
                exact.append(candidate)
        if len(exact) == 1:
            label_el = exact[0]
        elif candidates.count() == 1:
            label_el = candidates.first
        else:
            return None
        text = (label_el.inner_text() or "").strip()
        # Same guard as _label_plausibly_matches: a real field label is never a
        # full sentence, so a drastically longer match is a coincidental substring
        # inside unrelated prose, not the field we meant.
        if not text or len(text) > len(label_text) + 40:
            return None
        return label_el
    except Exception:
        return None


def _field_entry_container(label_el):
    """The shared container Ashby renders a label alongside its real control in
    (a class containing "field", e.g. "...ashby-application-form-field-entry") —
    the same ancestor walk already used elsewhere in this file for radio groups and
    the yes/no toggle, reused here since it's the same underlying container shape."""
    return label_el.locator(
        "xpath=ancestor::fieldset[1] | ancestor::*[contains(@class,'field')][1]"
    ).first


def _control_for_broken_label(page, label_text: str):
    """Best-effort control lookup once get_by_label(label_text) has already come
    back empty: find the label by its own text instead of `for`/id, then the one
    real input/select/textarea inside its shared field-entry container. Refuses
    (returns None) if that container holds anything other than exactly one such
    control — an ambiguous container is exactly the case this shouldn't guess on."""
    label_el = _label_by_own_text(page, label_text)
    if label_el is None:
        return None
    try:
        container = _field_entry_container(label_el)
        # Excludes checkbox inputs deliberately: a hidden proxy checkbox (see the
        # yes/no toggle above) can share this same container shape, and is never
        # the control we actually want to type/select into.
        control = container.locator(
            "input:not([type='checkbox']):not([type='file']), select, textarea"
        )
        return control.first if control.count() == 1 else None
    except Exception:
        return None


def _broken_required_labels(page, limit: int = 40) -> list:
    """Every `<label for="X">` on the page where `X` doesn't resolve to any real
    element *and* the label itself carries a "required" class-substring marker
    (see module comment above) — Ashby's own systemic pattern, checked
    independent of any specific known field alias so a completely novel required
    question rendered this way is still caught by find_unhandled_required_fields
    below, not just the ones fill_text/fill_yes_no already know how to answer."""
    labels = page.locator("label[for]")
    count = min(labels.count(), limit)
    broken: list = []
    for i in range(count):
        label_el = labels.nth(i)
        try:
            target_id = label_el.get_attribute("for")
            if not target_id or _by_id(page, target_id).count() > 0:
                continue  # for/id resolves fine -- not this pattern
            classes = (label_el.get_attribute("class") or "").split()
            if any("required" in cls.lower() for cls in classes):
                broken.append(label_el)
        except Exception:
            continue
    return broken


def track(filled_ids: set[str], locator) -> None:
    """Record the real id/name of a successfully-filled element, so the later
    required-fields audit can recognize it precisely instead of guessing by keyword."""
    try:
        identity = locator.get_attribute("id") or locator.get_attribute("name")
        if identity:
            filled_ids.add(identity)
    except Exception:
        pass


_COOKIE_CONSENT_ACCEPT_SELECTOR = "#onetrust-accept-btn-handler"


def _dismiss_cookie_consent_banner(page) -> None:
    """Dismisses a OneTrust cookie-consent banner, which renders over the form
    and physically blocks clicks on the fields underneath (confirmed live on
    Paylocity). Best-effort and called once before any field interaction; most
    ATS never render one, so a missing banner is a normal no-op.
    """
    try:
        accept = page.locator(_COOKIE_CONSENT_ACCEPT_SELECTOR)
        if accept.count() > 0 and accept.first.is_visible():
            accept.first.click(timeout=3000)
            page.wait_for_timeout(300)
            log.debug("Dismissed a OneTrust cookie-consent banner")
    except Exception:
        log.debug("Cookie-consent banner dismiss attempt failed", exc_info=True)


def fill_known_fields(
    page,
    profile: dict,
    extra_text_labels: dict[str, list[str]] | None = None,
    after_field_fill: Callable[[], None] | None = None,
) -> set[str]:
    """Fill Tier-A profile fields.

    ``after_field_fill`` is deliberately optional so callers outside the live
    engine retain the simple, synchronous helper API.  Ashby uses it to wait for
    the GraphQL save caused by one interaction before changing the next field;
    without that barrier, overlapping responses can restore a stale form state.
    """
    _dismiss_cookie_consent_banner(page)
    filled_ids: set[str] = set()

    def fill_then_settle(action: Callable[[], bool]) -> bool:
        filled = action()
        if filled and after_field_fill is not None:
            after_field_fill()
        return filled

    name = (profile.get("name") or "").strip()
    if name:
        first, _, last = name.partition(" ")
        filled_first = fill_then_settle(lambda: fill_text(page, filled_ids, ["First Name"], first))
        filled_last = (
            fill_then_settle(lambda: fill_text(page, filled_ids, ["Last Name"], last))
            if last
            else False
        )
        if not (filled_first or filled_last):
            fill_then_settle(lambda: fill_text(page, filled_ids, FULL_NAME_LABELS, name))
        # Confirmed live on Ashby: a "Preferred (First/Last) Name" field can appear
        # as its own required field *alongside* the legal name fields above, not
        # instead of them. Defaulting it to the same legal name (rather than
        # leaving it blank/unhandled) isn't a guess — it's the standard, safe
        # assumption when a candidate hasn't stated a different preferred name.
        if first:
            fill_then_settle(lambda: fill_text(page, filled_ids, ["Preferred First Name"], first))
        if last:
            fill_then_settle(lambda: fill_text(page, filled_ids, ["Preferred Last Name"], last))

    address = (profile.get("address") or "").strip()
    if address:
        log.debug("Address: profile has %r, attempting fill", address)
        address_labels = ADDRESS_LABELS + list((extra_text_labels or {}).get("address", []))
        fill_then_settle(
            lambda: fill_address(
                page,
                filled_ids,
                address_labels,
                address,
                expected_city=(profile.get("city") or "").strip(),
                expected_state=(profile.get("state") or "").strip(),
                expected_country=(profile.get("country") or "").strip(),
                expected_zip=(profile.get("zip") or "").strip(),
            )
        )
    else:
        # Logged rather than silently skipped: a blank value produces no
        # fill_address output at all downstream, which is otherwise
        # indistinguishable from it running and failing.
        log.debug("Address: profile field is blank, skipping")

    state = (profile.get("state") or "").strip()
    if state:
        log.debug("State: profile has %r, attempting fill", state)
        state_labels = STATE_LABELS + list((extra_text_labels or {}).get("state", []))
        fill_then_settle(
            lambda: fill_exact_choice(page, filled_ids, state_labels, _state_name_variants(state))
        )
    else:
        log.debug("State: profile field is blank, skipping")

    country = (profile.get("country") or "").strip()
    if country:
        log.debug("Country: profile has %r, attempting fill", country)
        country_labels = COUNTRY_LABELS + list((extra_text_labels or {}).get("country", []))
        fill_then_settle(
            lambda: fill_exact_choice(
                page, filled_ids, country_labels, _country_name_variants(country)
            )
        )
    else:
        log.debug("Country: profile field is blank, skipping")

    for key, default_labels in (
        ("address2", ADDRESS2_LABELS),
        ("zip", ZIP_LABELS),
        ("city", CITY_LABELS),
        ("county", COUNTY_LABELS),
    ):
        value = (profile.get(key) or "").strip()
        if value:
            log.debug("%s: profile has %r, attempting fill", key, value)
            labels = default_labels + list((extra_text_labels or {}).get(key, []))
            fill_then_settle(lambda: fill_typed(page, filled_ids, labels, value))
        else:
            log.debug("%s: profile field is blank, skipping", key)

    text_labels = {key: list(labels) for key, labels in TEXT_LABELS.items()}
    for key, labels in (extra_text_labels or {}).items():
        # Platform-specific aliases are tried in addition to (not instead of) the
        # generic defaults, appended so the generic label is still tried first.
        text_labels.setdefault(key, []).extend(labels)

    for key, labels in text_labels.items():
        value = (profile.get(key) or "").strip()
        if value:
            # A profile key says nothing about how an ATS rendered this particular
            # control.  fill_text inspects the live combobox shape itself, so the
            # same full profile location can select Greenhouse's City option or
            # fill Ashby's id-less autocomplete correctly.
            log.debug("%s: profile has %r, attempting fill", key, value)
            fill_then_settle(lambda: fill_text(page, filled_ids, labels, value))
        else:
            log.debug("%s: profile field is blank, skipping", key)

    for key, labels in YES_NO_LABELS.items():
        value = (profile.get(key) or "").strip()
        if value in ("Yes", "No"):
            fill_then_settle(lambda: fill_yes_no(page, filled_ids, labels, value))

    for key, labels in EEO_LABELS.items():
        value = (profile.get(key) or "").strip()
        if value:
            fill_then_settle(lambda: fill_text(page, filled_ids, labels, value))

    cover_letter = (profile.get("cover_letter") or "").strip()
    if cover_letter:
        fill_then_settle(lambda: fill_text(page, filled_ids, COVER_LETTER_LABELS, cover_letter))

    return filled_ids


def _label_plausibly_matches(page, el, searched_label: str) -> bool:
    """Guards against a real, confirmed failure mode of substring label matching:
    `get_by_label("Current Employer", exact=False)` matched GitLab's "Are you
    subject to any employment agreements and/or post-employment restrictions
    with your current employer or a past employer?" — a completely unrelated
    legal screening question that happens to contain the phrase "current
    employer" — and got filled with a company name instead of being left alone.
    A real field label is never a full sentence; if the field's own accessible
    label is drastically longer than the alias we searched for, this is almost
    certainly a coincidental substring match inside unrelated prose, not the
    field we meant. Fails open (returns True) if the label can't be read at all,
    since that's not this specific, confirmed failure mode.

    Also confirmed live, a shorter-range version of the same failure: a
    combobox can fold its own currently-selected value into its accessible
    name (Paylocity's Country field reads as "Country\nUnited States"), and
    that value can coincidentally contain a *different* field's search term as
    a bare substring ("United States" contains "State") -- well within the
    length budget above, so it isn't caught by that check alone. Requiring the
    searched term to appear as a whole word (not merely a substring) catches
    this without narrowing the length check's own existing tolerance."""
    try:
        real_label = _accessible_label(page, el)
    except Exception:
        return True
    if not real_label:
        return True
    if len(real_label) > len(searched_label) + 40:
        return False
    return bool(re.search(rf"\b{re.escape(searched_label)}\b", real_label, re.IGNORECASE))


def _locate_field_for_label(page, label: str):
    """Resolves one label alias to a concrete element, or None if it doesn't
    resolve to anything usable. Shared by fill_text and fill_exact_choice —
    factored out of what was fill_text's own inline lookup, behavior unchanged."""
    field = page.get_by_label(label, exact=False)
    if field.count() == 0:
        # get_by_label() relies on `for`/id (or aria-label/-labelledby)
        # resolving to a real element — confirmed broken on this exact
        # label text on Ashby (see _control_for_broken_label's module
        # comment above). Only reached when the normal route already
        # found nothing, never instead of it.
        return _control_for_broken_label(page, label) or _control_by_id_or_name(page, label)
    el = field.first
    # Confirmed live: acting through a `get_by_label(...).first` locator
    # that matched more than one element on the page (e.g. "Location"
    # substring-matching both the real location field and an unrelated
    # "...relocation..." question) behaves differently from acting through
    # a plain single-match #id locator, even once .first has resolved to
    # the right element — a .fill()/.type() through the ambiguous locator
    # silently didn't stick, through the re-bound one it did. Re-binding to
    # id/name once resolved removes that ambiguity for every action after.
    if field.count() > 1:
        identity = el.get_attribute("id")
        if identity:
            el = _by_id(page, identity)
    if not _label_plausibly_matches(page, el, label):
        # A broad accessible-label match can land on an unrelated
        # option such as "...willing to relocate" while Ashby's real
        # standalone Location label remains dangling and therefore
        # invisible to get_by_label. Fall back to the exact own-text
        # structural lookup here too, not only when count()==0.
        return _control_for_broken_label(page, label) or _control_by_id_or_name(page, label)
    return el


def _control_by_id_or_name(page, label_text: str):
    """Last-resort lookup by the control's own id/name, for a field with no
    resolvable accessible label at all — confirmed live on Paylocity, whose
    State field is a visible input with no name, no role and nothing
    get_by_label can resolve, but an id that names the field plainly.

    Matched as a whole token inside the id/name ("State" matches
    "public-site-address-us-state" but not "realestate"), and only an
    unambiguous single match is accepted."""
    token = re.sub(r"[^a-z0-9]+", "", label_text.lower())
    if len(token) < 3:
        return None  # too short to be a meaningful, non-coincidental match
    try:
        found = page.evaluate(
            """(token) => {
                const sep = '[^a-z0-9]';
                const re = new RegExp('(^|' + sep + ')' + token + '($|' + sep + ')', 'i');
                const els = Array.from(document.querySelectorAll(
                    'input:not([type=hidden]):not([type=submit]):not([type=button]),'
                    + 'select, textarea'
                )).filter((el) => (
                    (el.offsetParent || el.getClientRects().length)
                    && (re.test(el.id || '') || re.test(el.getAttribute('name') || ''))
                ));
                return els.length === 1 ? (els[0].id || null) : null;
            }""",
            token,
        )
    except Exception:
        return None
    if not found:
        return None
    log.debug("Located field for %r by id/name instead of label: %r", label_text, found)
    return _by_id(page, found)


def _type_verified(el, value: str, retries: int = 2) -> bool:
    """Types `value` via real keystrokes, verifying the input actually holds
    it afterwards and retrying (clear + retype, slower) if not. A fast
    per-keystroke type can silently drop a character on a page doing heavy
    per-keystroke work — "765 Montague Expy" landing as "765 ontague Expy" —
    and nothing downstream reliably catches that, since a slightly wrong query
    can still return suggestions.
    """
    for attempt in range(retries + 1):
        # Short timeout rather than Playwright's 30s default: a click that
        # can't land is almost always blocked by something overlapping the
        # field (a selected-value display, a consent banner), which retrying
        # never clears. Failing fast lets the caller try its next option.
        el.click(timeout=_BLOCKED_CLICK_TIMEOUT_MS)
        el.fill("")
        el.type(value, delay=20 if attempt == 0 else 60)
        if (el.input_value() or "").strip() == value.strip():
            return True
    return False


def fill_text(page, filled_ids: set[str], labels: list[str], value: str) -> bool:
    """Fills a text/textarea/select field matched by label. Selects handle the value
    as an option label (case-insensitive substring); everything else just gets
    .fill().  Comboboxes are routed from their *live* control shape: an id-backed
    react-select widget (Greenhouse's Location/City) must select an offered option,
    while an id-less autocomplete (Ashby's Location) uses its own selection flow.
    This is intentionally not based on the profile key: one employer's Location
    can be a constrained choice while another's is freeform."""
    for label in labels:
        try:
            el = _locate_field_for_label(page, label)
            if el is None:
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            is_combobox = tag == "input" and (el.get_attribute("role") or "") == "combobox"
            if tag == "select":
                el.select_option(label=re.compile(re.escape(value), re.IGNORECASE))
                track(filled_ids, el)
                log.debug(
                    "Fill-text: label %r is a <select>, selected option matching %r", label, value
                )
                return True
            if is_combobox:
                fill_combobox = (
                    _fill_react_select
                    if _control_kind(el) == "react_select"
                    else _fill_autocomplete
                )
                if not fill_combobox(page, el, value):
                    log.debug("Fill-text: label %r is a combobox, %r did not match", label, value)
                    continue
                track(filled_ids, el)
                log.debug("Fill-text: label %r is a combobox, filled %r", label, value)
                return True
            el.fill(value)
            track(filled_ids, el)
            log.debug("Fill-text: label %r is a plain field, filled %r", label, value)
            return True
        except Exception:
            log.debug("Fill-text: attempt for label %r failed", label, exc_info=True)
            continue
    log.debug("Fill-text: no field matched any of %s for %r", labels, value)
    return False


def fill_typed(page, filled_ids: set[str], labels: list[str], value: str) -> bool:
    """Fills a plain field with real keystrokes rather than fill_text's
    .fill(). Confirmed live on Paylocity: an address-block widget family
    (Address Line 2, Zip, City, County) looks like ordinary inputs but only
    registers with the page's own validation on genuine per-keystroke events —
    .fill() leaves the text visible while the page still considers the field
    unanswered. Typing into a genuinely plain field has the same end result,
    so this is safe for any of them.
    """
    for label in labels:
        try:
            el = _locate_field_for_label(page, label)
            if el is None:
                continue
            # Never clobber a value that's already right. An address widget
            # that auto-fills City/County/State/Zip on selection treats a
            # clear-and-retype of those as a hand-edit of its validated
            # address and resets the whole group (confirmed live on
            # Paylocity). The page's own value may also be the more specific
            # one — ZIP+4 over a bare ZIP — so it wins.
            current = (el.input_value() or "").strip()
            if current and value.strip().lower() in current.lower():
                track(filled_ids, el)
                log.debug(
                    "Fill-typed: label %r already holds %r (covers %r), leaving it alone",
                    label,
                    current,
                    value,
                )
                return True
            if not _type_verified(el, value):
                log.debug("Fill-typed: label %r never matched %r after retries", label, value)
                continue
            track(filled_ids, el)
            log.debug("Fill-typed: label %r typed %r", label, value)
            return True
        except Exception:
            log.debug("Fill-typed: attempt for label %r failed", label, exc_info=True)
            continue
    log.debug("Fill-typed: no field matched any of %s for %r", labels, value)
    return False


def fill_exact_choice(page, filled_ids: set[str], labels: list[str], candidates: list[str]) -> bool:
    """Fills a field by trying each of `candidates` (most-preferred first) as an
    *exact*, case-insensitive match against whatever the live control actually
    offers — never the substring match TEXT_LABELS' generic native-<select>
    handling uses (`el.select_option(label=re.compile(...))`), which is exactly
    how a profile value "CA" could silently select the option "North Carolina"
    instead of "California": both contain "ca". Used for fields where a wrong
    pick is a factual claim about the candidate, not a stylistic difference —
    state and country. A control that is neither a <select> nor an ARIA
    combobox is treated as a possible unmarked typeahead: the candidate is
    typed and a suggestion list watched for, falling back to the typed value
    if none appears.
    """
    # A field can still be mid-hydration here, so don't conclude it's absent
    # from a single early check. Kept short: this is pure waiting on a form
    # that simply has no such field, and it exits immediately once one is
    # present.
    for hydration_attempt in range(3):
        if any(_locate_field_for_label(page, label) is not None for label in labels):
            break
        if hydration_attempt < 2:
            page.wait_for_timeout(500)
    for label in labels:
        try:
            el = _locate_field_for_label(page, label)
            if el is None:
                continue
            # A country/state field may already hold the right value as the
            # page's own default. Checked before any tag/role branching: such a
            # field isn't necessarily a properly ARIA-marked combobox (
            # Paylocity's Country input carries no role at all), and
            # re-selecting an already-correct value risks a click blocked by
            # the selected-value display sitting over it.
            # Polled rather than read once: this value can land a beat after
            # another field's change triggers it (selecting an address
            # auto-fills State), and deciding "it's empty" from a single early
            # read means fighting an overlay to fill a field that's already
            # correct.
            lowered_candidates = {c.strip().lower() for c in candidates}
            current: list[str] = []
            for settle_attempt in range(6):
                current = _react_select_selected_labels(el)
                if any(v.strip().lower() in lowered_candidates for v in current):
                    track(filled_ids, el)
                    log.debug("Exact-choice: field already shows %s, skipping re-fill", current)
                    return True
                if settle_attempt < 5:
                    page.wait_for_timeout(250)
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            is_combobox = tag == "input" and (el.get_attribute("role") or "") == "combobox"
            if tag == "select":
                live_options = _select_options(el)
                log.debug(
                    "Exact-choice: field for label %r is a <select> with live options %s, "
                    "trying candidates %s",
                    label,
                    live_options,
                    candidates,
                )
                match = next(
                    (
                        option
                        for option in live_options
                        if option.strip().lower() in {c.strip().lower() for c in candidates}
                    ),
                    None,
                )
                if match is None:
                    log.debug("Exact-choice: none of %s found among live options", candidates)
                    continue
                el.select_option(label=match)
                track(filled_ids, el)
                log.debug("Exact-choice: selected %r", match)
                return True
            if is_combobox:
                log.debug(
                    "Exact-choice: field for label %r is a combobox, trying candidates %s",
                    label,
                    candidates,
                )
                fill_combobox = (
                    _fill_react_select
                    if _control_kind(el) == "react_select"
                    else _fill_autocomplete
                )
                if not any(fill_combobox(page, el, candidate) for candidate in candidates):
                    log.debug("Exact-choice: none of %s matched the combobox's options", candidates)
                    continue
                track(filled_ids, el)
                log.debug("Exact-choice: combobox filled")
                return True
            # Neither a native <select> nor role="combobox" -- but a field whose
            # accessible label is itself a placeholder-shaped phrase (confirmed
            # live: "State\nSelect a state") is a strong sign this is actually a
            # JS-driven typeahead with no standard ARIA markup, the same shape
            # fill_address already has to handle for street addresses. Blindly
            # .fill()-ing such a field sets the visible text without the page's
            # own JS/validation ever registering it as answered — confirmed live
            # as "field looked filled but the form rejected it". .type() (not
            # .fill()) so any such widget's own per-keystroke listener actually
            # fires, then check for a suggestion list the same way fill_address
            # does.
            if not _type_verified(el, candidates[0]):
                log.debug(
                    "Exact-choice: %r never matched after retries, giving up on label %r",
                    candidates[0],
                    label,
                )
                continue
            options = None
            for _ in range(8):
                page.wait_for_timeout(250)
                candidate_options = _address_suggestion_options(page)
                if candidate_options is not None and candidate_options.count() > 0:
                    options = candidate_options
                    break
            if options is None:
                # No suggestion widget ever appeared -- a genuinely plain field,
                # and the value is already typed in.
                track(filled_ids, el)
                log.debug("Exact-choice: plain text field, typed %r", candidates[0])
                return True
            count = min(options.count(), 20)
            texts = [(options.nth(i).inner_text() or "").strip() for i in range(count)]
            log.debug(
                "Exact-choice: suggestion widget offered %s for candidates %s", texts, candidates
            )
            lowered_candidates = {c.strip().lower() for c in candidates}
            match_idx = next(
                (i for i, text in enumerate(texts) if text.strip().lower() in lowered_candidates),
                None,
            )
            if match_idx is None:
                # State/country have a small, closed value set -- unlike
                # address, there's no need for combination-based scoring here.
                # If none of our candidates exactly match an offered option,
                # something is genuinely wrong; refuse rather than guess.
                log.debug(
                    "Exact-choice: none of %s exactly matched an offered suggestion", candidates
                )
                continue
            options.nth(match_idx).click()
            page.wait_for_timeout(300)
            track(filled_ids, el)
            log.debug("Exact-choice: selected suggestion %r", texts[match_idx])
            return True
        except Exception:
            log.debug("Exact-choice: attempt for label %r failed", label, exc_info=True)
            continue
    log.debug("Exact-choice: no field matched any of %s for %s", labels, candidates)
    return False


def _wait_for_suggestions(page, rounds: int, context: str):
    """Polls up to `rounds` x 250ms for a suggestion list that actually has
    rows, returning its options locator or None."""
    for attempt in range(rounds):
        page.wait_for_timeout(250)
        options = _address_suggestion_options(page)
        if options is not None and options.count() > 0:
            log.debug(
                "Address: dropdown opened %s after %dms (%d option(s))",
                context,
                (attempt + 1) * 250,
                options.count(),
            )
            return options
    return None


def _type_and_watch_for_dropdown(page, el, value: str):
    """Types `value` and watches for the widget's suggestion list throughout,
    rather than typing the whole string in one burst and only looking after.

    Confirmed live (Paylocity): typing "765" by hand opened a 6-result
    dropdown while the full "765 Montague Expy" typed at 20ms a character
    opened nothing. These widgets debounce their lookup and key it off the
    current query, so a burst-typed full string can both outrun the debounce
    and be a narrower query than the one that matches. Typing a short prefix
    first, pausing for the debounce, then typing the rest mirrors the
    interaction that works by hand.
    """
    prefix = value.strip()[:_ADDRESS_PREFIX_CHARS]
    remainder = value.strip()[_ADDRESS_PREFIX_CHARS:]
    try:
        el.click(timeout=_BLOCKED_CLICK_TIMEOUT_MS)
        el.fill("")
        el.press_sequentially(prefix, delay=_ADDRESS_TYPE_DELAY_MS)
        on_prefix = _wait_for_suggestions(page, 6, "on prefix %r" % prefix)
        if not remainder:
            return on_prefix
        # The field still has to end up holding the full address if no
        # suggestion is ever selected, and the narrower full query usually
        # produces the better match — so always type the rest, then look again.
        el.press_sequentially(remainder, delay=_ADDRESS_TYPE_DELAY_MS)
        on_full = _wait_for_suggestions(page, 8, "on full value %r" % value)
        if on_full is not None:
            return on_full
        if on_prefix is not None:
            # Open on the prefix but not the full string: re-type the prefix so
            # there's a list to select from at all.
            el.fill("")
            el.press_sequentially(prefix, delay=_ADDRESS_TYPE_DELAY_MS)
            return _wait_for_suggestions(page, 6, "on re-typed prefix %r" % prefix)
        return None
    except Exception:
        log.debug("Address: typing/dropdown watch failed", exc_info=True)
        return None


def _address_suggestion_options(page):
    """Returns the options locator for whichever address-suggestion widget is
    currently open (dropdown), or None if the field is behaving as a plain
    textbox instead. Checks, in order: the generic ARIA listbox/option pattern
    (covers a react-select-style address combobox); bare `[role='option']`
    elements with no enclosing `role='listbox']` wrapper (some widgets only tag
    the individual rows); then Google Places Autocomplete's classic
    `.pac-container`/`.pac-item` markup, which carries no ARIA role at all but
    is the single most common real address-suggestion widget embedded in ATS
    forms. Playwright's locators pierce open shadow DOM by default, so this
    also reaches a shadow-rendered listbox without special-casing it."""
    listboxes = page.locator("[role='listbox']:visible")
    if listboxes.count() > 0:
        return listboxes.first.get_by_role("option")
    bare_options = page.locator("[role='option']:visible")
    if bare_options.count() > 0:
        return bare_options
    pac = page.locator(".pac-container:visible")
    if pac.count() > 0:
        return pac.first.locator(".pac-item")
    return None


# A suggestion set can offer the identical street/city/state/zip twice, differing
# only by unit number. Used solely to break such a tie in favour of the plain
# suggestion — never to match against the profile's own address2 value.
_UNIT_INDICATOR_PATTERN = re.compile(r"\b(apt|apartment|unit|suite|ste|fl|floor)\b", re.IGNORECASE)

# Short enough to be a query the lookup service has results for, long enough to
# be worth searching on; 90ms/char is roughly human typing speed, slow enough not
# to outrun a debounced per-keystroke handler.
_ADDRESS_PREFIX_CHARS = 8
_ADDRESS_TYPE_DELAY_MS = 90
# How long to let the widget's own City/County/State/Zip auto-fill land after a
# suggestion is selected, before anything else inspects those fields.
_ADDRESS_AUTOFILL_SETTLE_MS = 1500
# A click that can't land because something overlaps the field never starts
# working after more retries — fail fast rather than stalling a whole attempt.
_BLOCKED_CLICK_TIMEOUT_MS = 5000


def _best_address_match(
    texts: list[str],
    typed: str,
    expected_city: str,
    expected_state: str,
    expected_country: str,
    expected_zip: str,
) -> int | None:
    """Scores each suggestion against every combination of what the profile
    actually knows about this address — the typed street number, city, state
    (either form), country (including aliases like "USA"), and zip — rather
    than trusting string similarity to `typed` alone: a typed "123 Main St"
    will never appear verbatim in a suggestion formatted as "123 Main St,
    Springfield, IL 62704, USA". Not every signal needs to be present for a
    given field/form (address alone, address+city, address+state+country+zip,
    and everything in between are all real, confirmed formats); this scores
    whichever of them are actually available and requires at least two to
    agree before trusting a suggestion, since a single coincidental match
    isn't enough — the zip counts double, as a coincidental zip match is very
    unlikely on its own. Never reads address2 (apartment/unit) as a signal —
    a suggestion list can offer the identical street/city/zip twice, differing
    only by unit number ("123 Main St, Springfield, IL" vs "123 Main St, Apt
    4, Springfield, IL"), and on that specific kind of tie (confirmed live),
    the plain, no-unit suggestion is preferred by default — a static
    preference for the general building entry, not a lookup against the
    profile's own unit value. Any other tie (nothing to do with a unit
    number), or nothing clearing the bar at all, still refuses rather than
    picking one — the same posture _fill_react_select already takes on a
    genuinely ambiguous option set.
    """
    street_number_match = re.match(r"\s*(\d+)", typed)
    street_number = street_number_match.group(1) if street_number_match else None
    state_variants = _state_name_variants(expected_state) if expected_state else []
    country_variants = _country_name_variants(expected_country)
    scores = []
    for text in texts:
        lowered = text.lower()
        score = 0
        if expected_zip and expected_zip.lower() in lowered:
            score += 2
        if expected_city and expected_city.lower() in lowered:
            score += 1
        if state_variants and any(v.lower() in lowered for v in state_variants if v):
            score += 1
        if country_variants and any(v.lower() in lowered for v in country_variants):
            score += 1
        if street_number and re.search(rf"\b{re.escape(street_number)}\b", text):
            score += 1
        scores.append(score)
    if not scores:
        return None
    best_score = max(scores)
    if best_score < 2:
        return None
    best_indices = [i for i, score in enumerate(scores) if score == best_score]
    if len(best_indices) == 1:
        return best_indices[0]
    # A tie: if it's specifically the "same address, differs only by unit"
    # shape, prefer whichever tied option names no unit at all. Only applies
    # when exactly one tied candidate qualifies -- a tie among several
    # unit-free options (or several that all name a unit) is still genuinely
    # ambiguous and refuses like any other unresolved tie.
    unit_free = [i for i in best_indices if not _UNIT_INDICATOR_PATTERN.search(texts[i])]
    return unit_free[0] if len(unit_free) == 1 else None


def fill_address(
    page,
    filled_ids: set[str],
    labels: list[str],
    value: str,
    expected_city: str = "",
    expected_state: str = "",
    expected_country: str = "",
    expected_zip: str = "",
) -> bool:
    """Fills the primary street-address line. Types the value (not .fill(), so
    a JS-driven widget's own per-keystroke listener actually fires), then
    checks whether the field behaves as a dropdown or a plain textbox:

    - Plain textbox (no suggestion widget ever opens): the typed value is
      already correct, nothing more to do.
    - Dropdown: the offered suggestions are scored against the profile's own
      city/state/country/zip in whatever combination is actually
      corroborated (see _best_address_match), and only a confidently-verified
      suggestion is clicked — never accepted via Enter, which would just take
      the widget's own default-highlighted option (usually the first result)
      instead of the one actually confirmed correct, and risks submitting the
      whole form early if Enter in this field happens to trigger implicit
      form submission.

    If a dropdown appears but nothing is confidently corroborated, this
    returns False rather than guessing: the field flows through the normal
    required-field pipeline (Tier B/C/D, then honest refusal) the same as any
    other field this routine can't confidently answer — never a silent guess
    at a real mailing address.
    """
    for label in labels:
        try:
            el = _locate_field_for_label(page, label)
            if el is None:
                continue
            log.debug("Address: field located via label %r, starting typing %r", label, value)
            options = _type_and_watch_for_dropdown(page, el, value)
            if options is None:
                log.debug("Address: no dropdown opened, keeping typed value %r", value)
                track(filled_ids, el)
                return True
            count = min(options.count(), 20)
            texts = [(options.nth(i).inner_text() or "").strip() for i in range(count)]
            log.debug("Address: suggestions offered: %s", texts)
            match_idx = _best_address_match(
                texts,
                value,
                expected_city,
                expected_state,
                expected_country,
                expected_zip,
            )
            if match_idx is None:
                # Deliberately not pressing Escape/clearing here: on several
                # real combobox widgets that reverts the input to empty rather
                # than just closing the list, throwing away the typed value
                # for no benefit.
                log.debug(
                    "Address: no suggestion confidently matches the profile's "
                    "city/state/country/zip/address2 -- leaving typed value, field unresolved"
                )
                return False
            log.debug("Address: selecting suggestion %r", texts[match_idx])
            options.nth(match_idx).click()
            # Selecting a suggestion makes this widget populate
            # City/County/State/Zip itself, but not instantly. Anything reading
            # those fields before the auto-fill lands sees them empty and fills
            # them itself, undoing the widget's work. Bounded so a page with
            # persistent background chatter can't hang here.
            try:
                page.wait_for_load_state("networkidle", timeout=_ADDRESS_AUTOFILL_SETTLE_MS)
            except Exception:
                pass
            page.wait_for_timeout(_ADDRESS_AUTOFILL_SETTLE_MS)
            track(filled_ids, el)
            log.debug("Address: selected from dropdown, done")
            return True
        except Exception:
            log.debug("Address: fill attempt for label %r failed", label, exc_info=True)
            continue
    log.debug("Address: no field matched any of %s", labels)
    return False


def _fill_autocomplete(page, el, value: str) -> bool:
    """Fills an autocomplete-style combobox, including Ashby's required Location
    input, which has no id/name and therefore cannot use react-select's id-derived
    listbox selector.

    A visible suggestion list is authoritative: only an exact or unique-prefix
    option is committed, and the click is verified against the input's resulting
    value. If no list ever appears, the widget is treated as freeform and typed
    text is accepted only after it remains stable. This same routine is used both
    for Tier-A profile filling and for replaying an approved blocked answer, so a
    retry does not use a weaker control path than the original attempt.
    """
    target = value.strip().lower()
    if not target:
        return False
    try:
        saw_any_option = False
        for _ in range(2):
            el.click()
            el.fill("")
            el.type(value, delay=20)
            match = None
            match_text = ""
            for _ in range(6):
                page.wait_for_timeout(250)
                listboxes = page.locator("[role='listbox']:visible")
                if listboxes.count() == 0:
                    continue
                options = listboxes.first.get_by_role("option")
                count = min(options.count(), 20)
                if count:
                    saw_any_option = True
                texts = [(options.nth(i).inner_text() or "").strip() for i in range(count)]
                exact_idx = [i for i, text in enumerate(texts) if text.lower() == target]
                prefix_idx = [i for i, text in enumerate(texts) if text.lower().startswith(target)]
                match_idx = (
                    exact_idx[0] if exact_idx else (prefix_idx[0] if len(prefix_idx) == 1 else None)
                )
                if match_idx is not None:
                    match = options.nth(match_idx)
                    match_text = texts[match_idx]
                    break
            if match is not None:
                match.click()
                page.wait_for_timeout(500)
                committed = (el.input_value() or "").strip()
                if committed and committed.lower() == match_text.lower():
                    return True
            elif not saw_any_option:
                page.wait_for_timeout(800)
                if (el.input_value() or "").strip() == value.strip():
                    return True
            el.fill("")
            page.keyboard.press("Escape")
        return False
    except Exception:
        try:
            el.fill("")
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _fill_react_select(page, el, value: str) -> bool:
    """Best-effort fill for a react-select-style combobox — an <input role="combobox">
    backed by a hidden, filterable option list, rendered by Greenhouse's newer
    job-boards UI (and any other ATS built on the same library) for both Yes/No
    screening questions and richer pick-lists, instead of a native <select> or a
    plain radio pair. `fill_text`'s .select_option() and fill_yes_no's radio search
    both miss this shape entirely since it's neither.

    Opens the menu by clicking + typing `value` to filter, then commits an option
    whose visible text matches `value` exactly (case-insensitive) — or, failing
    that, a *unique* case-insensitive prefix match. The prefix fallback exists for
    a real, confirmed case: Greenhouse's shared country/dial-code widget renders
    "United States" as "United States +1", so a plain "United States" answer would
    never exactly match anything without it. It's still refuse-rather-than-guess:
    if more than one option shares the prefix, nothing is committed — the same
    "willing to relocate" field whose real options turn out to be "No", "No, but
    open to remote", and a list of specific office cities is exactly the case this
    guards against (a bare "No" answer must not silently land on whichever of the
    two "No..." options happens to come first). If the real option set doesn't
    contain `value` at all (exactly or as a unique prefix), this deliberately
    leaves the field untouched (closing the menu back up) rather than guessing the
    nearest option and risking a wrong answer reaching a real employer -- UNLESS no
    option ever appeared at all, across every attempt (see the fallback after the
    retry loop below): some of these widgets (confirmed live: Greenhouse's own
    freeform "describe your experience" essay-style questions) are role="combobox"
    purely for consistent styling, with no real backing option list at all -- for
    those, refusing to trust typed text isn't a safety measure, since there's no
    real option set to violate in the first place, just a field that was never
    filled at all.

    The open+type step is retried within each attempt: the very first interaction
    with any react-select combobox on a freshly-loaded page (confirmed live, not
    fixture-reproducible) can silently fail to open the menu at all — apparently a
    one-time hydration/focus race in the library's open handler.

    The whole open+select cycle is retried up to 3 times. A click is trusted only
    when both the widget's own validity proxy is clear and its rendered selected
    label exactly matches the option that was clicked. That prevents both a
    silent click failure and the Greenhouse-specific false negative where its
    searchable input clears after a successful selection."""
    try:
        identity = el.get_attribute("id")
        if not identity:
            return False
        # Greenhouse generated ids can contain CSS-special characters such as
        # ``[]`` (for a multi-value citizenship control). Reuse the safe id
        # lookup instead of interpolating them into a raw #id selector.
        listbox = _by_id(page, f"react-select-{identity}-listbox")
        target = value.strip().lower()
        saw_any_option = False
        for _ in range(3):  # bounded retries for the confirmed non-deterministic click race
            match = None
            match_text = ""
            selected_before = _react_select_selected_labels(el)
            for _ in range(2):  # one retry for the first-interaction-on-page miss
                el.click()
                el.type(value, delay=20)
                for _ in range(4):
                    page.wait_for_timeout(250)
                    if listbox.count() == 0:
                        continue
                    options = listbox.get_by_role("option")
                    count = options.count()
                    if count:
                        saw_any_option = True
                    texts = [(options.nth(i).inner_text() or "").strip() for i in range(count)]
                    exact_idx = [i for i, t in enumerate(texts) if t.lower() == target]
                    if exact_idx:
                        match_idx = exact_idx[0]
                    else:
                        prefix_idx = [
                            i for i, t in enumerate(texts) if t.lower().startswith(target)
                        ]
                        match_idx = prefix_idx[0] if len(prefix_idx) == 1 else None
                    if match_idx is not None:
                        match = options.nth(match_idx)
                        match_text = texts[match_idx]
                    if match is not None:
                        break
                if match is not None:
                    break
                el.fill("")
                page.keyboard.press("Escape")
            if match is None:
                # No matching option exists at all — a real "this value isn't in
                # the option set" case, not flakiness. Retrying the cycle won't
                # produce an option that isn't there, so stop here rather than
                # burning retries pointlessly.
                break
            match.click()
            # Confirmed live: filling several of these widgets back-to-back on the
            # same page (a job with many custom questions rendered this way — the
            # common case, not the exception) measurably raises the odds the *next*
            # field's own click-to-open silently no-ops, the same first-interaction
            # race noted above but re-triggered by not giving this selection's own
            # React state update a moment to finish first. A brief settle pause
            # here is cheap insurance against costing the next field in the loop.
            page.wait_for_timeout(300)
            if _combobox_selection_confirmed(el) and _react_select_selection_confirmed(
                el, match_text, selected_before
            ):
                return True
        if not saw_any_option:
            # Never once showed a real option to select from, across every
            # attempt above -- a freeform field only styled as a combobox, not a
            # genuine closed list (see this function's own docstring). Falls back
            # to the same type-and-verify-it-stuck approach fill_text's own
            # generic text-control branch already uses -- trusting typed text
            # here doesn't carry the risk this function otherwise guards
            # against, since there's no real option set for it to violate.
            for _ in range(2):
                el.click()
                el.type(value, delay=20)
                page.wait_for_timeout(800)
                if (el.input_value() or "").strip() == value.strip():
                    return True
            return False
        el.fill("")
        page.keyboard.press("Escape")
        return False
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    return False


def _react_select_selected_labels(el) -> list[str]:
    """Visible selected labels scoped to one react-select control."""
    try:
        selected = el.evaluate(
            """(input) => {
                let node = input.parentElement;
                for (let depth = 0; depth < 8 && node; depth += 1, node = node.parentElement) {
                    const values = Array.from(node.querySelectorAll(
                        '[class*="single-value"], [class*="multi-value__label"]'
                    ));
                    if (values.length) {
                        return values.map((value) => (value.innerText || '').trim());
                    }
                }
                return [];
            }"""
        )
        return [str(text).strip() for text in (selected or []) if str(text).strip()]
    except Exception:
        return []


def _react_select_selection_confirmed(
    el, expected: str, selected_before: list[str] | None = None
) -> bool:
    """Confirms a react-select option was actually committed.

    React-select clears its editable search input after a choice is selected, so
    ``input_value()`` is not evidence of failure or success. Greenhouse usually
    renders the committed option in a nearby ``single-value`` node, but can
    intentionally shorten it (``United States +1`` displays as ``+1``). Accept
    either an exact visible label or a non-empty selection that changed from the
    pre-click state; a click that never reaches component state still fails.
    """
    target = expected.strip().casefold()
    if not target:
        return False
    try:
        selected_after = _react_select_selected_labels(el)
        if any(text.casefold() == target for text in selected_after):
            return True
        before = [text.casefold() for text in (selected_before or [])]
        after = [text.casefold() for text in selected_after]
        return bool(after) and after != before
    except Exception:
        return False


def _combobox_selection_confirmed(el) -> bool:
    """Best-effort verification that a react-select-style combobox's selection
    actually registered with the page's own native form validation, not just
    that a click on the right option didn't raise an error. Some
    implementations (confirmed live on Greenhouse's job-boards UI) back a
    required combobox with a hidden sibling <input required> used purely to
    hook into the browser's native validity API for a custom widget that isn't
    a real form control on its own — a successful-looking click doesn't
    guarantee that sibling's state updated in sync.

    Scoped to the nearest ".select-shell"-classed ancestor (the exact wrapper
    confirmed live to contain that hidden proxy) if one exists — deliberately
    starting from `e.parentElement`, not `e` itself, since the combobox input's
    own class ("select__input") also contains the substring "select" and would
    otherwise match itself before ever reaching a real ancestor. Falls back to
    a small bounded walk up plain ancestor <div>s for an ATS that doesn't use
    this exact class name, so this isn't purely Greenhouse-specific — but never
    walks all the way to the <form>, which would false-positive on some other,
    unrelated field elsewhere on the page still being invalid. If nothing
    resembling this pattern is found at all, there's nothing to check, and this
    reports success rather than penalizing an ATS that doesn't use it."""
    try:
        return el.evaluate("""e => {
            const start = e.parentElement;
            if (!start) return true;
            const shell = start.closest('.select-shell')
                || start.closest('[class*="select-shell"]')
                || (() => {
                    let node = start;
                    for (let i = 0; i < 6 && node; i++) {
                        // A form's :invalid state includes every unrelated
                        // required field (notably the résumé before engine.py
                        // uploads it). It cannot say whether this combobox
                        // committed, so never let this narrow control check
                        // climb into the form-level validation aggregate.
                        if (node.tagName === 'FORM') break;
                        if (node.querySelector(':invalid') !== null) return node;
                        node = node.parentElement;
                    }
                    return null;
                })();
            if (!shell) return true;
            return shell.querySelector(':invalid') === null;
        }""")
    except Exception:
        return True


def _yesno_question_label(page, phrase: str):
    """Finds the question heading for a yes/no control without mistaking one of
    its option labels for the question itself.

    The distinction matters for Harvey's hybrid-work question: the option "No,
    I'm not based ... but willing to relocate" contains the known profile alias
    "willing to relocate". The previous broad `get_by_text` fallback landed on
    that option label, walked up to the group, and then selected the group's
    "Yes, I'm based ..." option for a profile value of Yes. A label whose `for`
    resolves to a radio/checkbox is an option, never the question heading; only a
    unique remaining label/legend that owns a choice control is accepted.
    """
    try:
        candidates = page.locator("label, legend").filter(
            has_text=re.compile(re.escape(phrase), re.IGNORECASE)
        )
        matches = []
        for i in range(min(candidates.count(), 30)):
            candidate = candidates.nth(i)
            tag = candidate.evaluate("e => e.tagName.toLowerCase()")
            if tag == "label":
                target_id = candidate.get_attribute("for")
                if target_id:
                    target = _by_id(page, target_id)
                    if target.count():
                        target_type = (target.first.get_attribute("type") or "").lower()
                        if target_type in ("radio", "checkbox"):
                            continue
            container = candidate.locator(
                "xpath=ancestor::fieldset[1] | ancestor::*[contains(@class,'field')][1]"
            ).first
            owns_radios = container.locator("input[type='radio']").count() > 0
            owns_toggle = (
                container.locator(_YESNO_TOGGLE_YES_SELECTOR).count() == 1
                and container.locator(_YESNO_TOGGLE_NO_SELECTOR).count() == 1
            )
            if owns_radios or owns_toggle:
                matches.append(candidate)
        return matches[0] if len(matches) == 1 else None
    except Exception:
        return None


def fill_yes_no(page, filled_ids: set[str], labels: list[str], value: str) -> bool:
    """Best-effort fill for a Yes/No question rendered as a <select>, a react-select-
    style combobox, or a radio pair. Radio groups are usually labeled per-option
    ("Yes"/"No"), not as a single control tied to the question text, so this falls
    back to searching near the question for a radio whose own accessible name
    matches the value."""
    for label in labels:
        try:
            field = page.get_by_label(label, exact=False)
            if field.count():
                el = field.first
                if field.count() > 1:
                    identity = el.get_attribute("id")
                    if identity:
                        el = _by_id(page, identity)
                if _label_plausibly_matches(page, el, label):
                    tag = el.evaluate("e => e.tagName.toLowerCase()")
                    if tag == "select":
                        el.select_option(label=re.compile(f"^{value}$", re.IGNORECASE))
                        track(filled_ids, el)
                        return True
                    if (
                        tag == "input"
                        and (el.get_attribute("role") or "") == "combobox"
                        and _fill_react_select(page, el, value)
                    ):
                        track(filled_ids, el)
                        return True
        except Exception:
            pass
        try:
            question = _yesno_question_label(page, label)
            if question is None:
                continue
            container = question.locator(
                "xpath=ancestor::fieldset[1] | ancestor::*[contains(@class,'field')][1]"
            ).first
            radio = container.get_by_role("radio", name=value, exact=False)
            if radio.count():
                radio.first.check()
                track(filled_ids, radio.first)
                return True
            yes_button = container.locator(_YESNO_TOGGLE_YES_SELECTOR)
            no_button = container.locator(_YESNO_TOGGLE_NO_SELECTOR)
            if (
                yes_button.count() == 1
                and no_button.count() == 1
                and _click_yesno_toggle(yes_button.first, no_button.first, value)
            ):
                identity = _yesno_toggle_identity(container)
                filled_ids.add(identity or label)
                return True
        except Exception:
            continue
    return False


@dataclass
class _UnhandledField:
    """Internal pairing of a public, Playwright-free QuestionDescriptor with the
    live locator/identity needed to actually fill it once an answer is found. Never
    leaves apply_adapters — engine.py consumes .identity/.locator directly and only
    ever hands the .descriptor out to callers outside this package
    (blocked_questions.py, scheduler.py, the LLM drafting module)."""

    descriptor: QuestionDescriptor
    identity: str
    locator: object
    control_kind: Literal[
        "native", "react_select", "autocomplete", "button_toggle", "radio_group"
    ] = "native"


def _control_kind(field) -> Literal["native", "react_select", "autocomplete"]:
    """Classifies how an input must be controlled independently of its public
    question type. Both an Ashby autocomplete and a Greenhouse react-select are
    `QuestionDescriptor(field_type="text")`, but replaying an approved answer
    requires materially different browser interactions."""
    try:
        if (field.get_attribute("role") or "").lower() != "combobox":
            return "native"
        return "react_select" if field.get_attribute("id") else "autocomplete"
    except Exception:
        return "native"


def _accessible_label(page, field) -> str:
    """Best-effort accessible label text for one form control, prefixed with its
    parent question's own text when the control's own label is really just one
    option of a shared question (see _base_accessible_label + `description`
    handling below) — bare option text like "TikTok" is meaningless out of context
    to a human reviewer, the Q&A bank's semantic match, or an LLM draft prompt."""
    label = _base_accessible_label(page, field)
    try:
        # Confirmed live on Greenhouse's newer job-boards UI: a checkbox that's one
        # option in a multi-select group carries the *group's* question in a plain
        # (non-ARIA) `description` attribute, since its own accessible label is only
        # ever the option text itself ("TikTok"), never the actual question being
        # asked ("Do you have experience in the Creator Economy beyond Twitch?").
        description = (field.get_attribute("description") or "").strip()
        if description and description.lower() != label.lower():
            return f"{description} — {label}" if label else description
    except Exception:
        pass
    return label


def _base_accessible_label(page, field) -> str:
    """Tried in descending order of reliability: a native <label>/`for=`
    association, aria-label, aria-labelledby's referenced text, and finally
    placeholder as a last resort for a field with no real label at all."""
    try:
        label_text = field.evaluate("e => e.labels && e.labels.length ? e.labels[0].innerText : ''")
        if label_text and label_text.strip():
            return label_text.strip()
    except Exception:
        pass
    try:
        aria_label = field.get_attribute("aria-label")
        if aria_label and aria_label.strip():
            return aria_label.strip()
    except Exception:
        pass
    try:
        labelledby = field.get_attribute("aria-labelledby")
        if labelledby:
            texts = []
            for ref_id in labelledby.split():
                ref = _by_id(page, ref_id)
                if ref.count():
                    texts.append((ref.first.inner_text() or "").strip())
            joined = " ".join(t for t in texts if t)
            if joined:
                return joined
    except Exception:
        pass
    try:
        placeholder = field.get_attribute("placeholder")
        if placeholder and placeholder.strip():
            return placeholder.strip()
    except Exception:
        pass
    return ""


def _radio_group_label(page, field) -> str:
    """A single radio's own accessible name is one option's label ("Yes"), never the
    group's question — walk up to the nearest fieldset/legend or field-ish ancestor
    and use its own text instead. Mirrors the ancestor lookup fill_yes_no already
    relies on to *find* a radio group from its question text, just walking the other
    direction (from a known radio up to its question)."""
    try:
        container = field.locator(
            "xpath=ancestor::fieldset[1] | ancestor::*[contains(@class,'field')][1]"
        ).first
        legend = container.locator("legend").first
        if legend.count():
            text = (legend.inner_text() or "").strip()
            if text:
                return text
        group_name = field.get_attribute("name")
        options = (
            {opt.strip().lower() for opt in _radio_group_options(page, group_name)}
            if group_name
            else set()
        )
        text = (container.inner_text() or "").strip()
        if text:
            # The full container's text includes every option's own label too; the
            # question text is reliably the first line for the markup patterns
            # confirmed on Greenhouse/Ashby, which render the question above the
            # radio inputs, inside that same container.
            first_line = text.splitlines()[0].strip()
            if first_line.lower() not in options:
                return first_line
        # Confirmed live on Lever: the "field"-classed container wraps only the
        # <ul> of options, with the actual question text living as plain content
        # of *its own* parent instead — the first_line check above catches that
        # case (first_line is just the first option, not a real question) and
        # falls through to here rather than returning a meaningless answer.
        parent_text = (container.locator("xpath=..").first.inner_text() or "").strip()
        if parent_text:
            for line in parent_text.splitlines():
                line = line.strip()
                if line and line.lower() not in options:
                    return line
        # Every line was just an option (or the container was empty) — fall back to
        # whatever we found, rather than nothing at all.
        return text.splitlines()[0].strip() if text else ""
    except Exception:
        pass
    return ""


def _select_options(field) -> list[str]:
    try:
        texts = field.locator("option").all_inner_texts()
    except Exception:
        return []
    return [
        t.strip()
        for t in texts
        if t.strip() and not re.match(r"^select\b", t.strip(), re.IGNORECASE)
    ]


def _radio_group_options(page, group_name: str) -> list[str]:
    options: list[str] = []
    try:
        radios = _radios_by_name(page, group_name)
        count = min(radios.count(), 10)
        for i in range(count):
            radio = radios.nth(i)
            label = _accessible_label(page, radio)
            if label:
                options.append(label)
    except Exception:
        pass
    return options


def _radio_group_filled(page, group_name: str, filled_ids: set[str]) -> bool:
    """A filled radio group is usually tracked by the specific option's own id (see
    track()), not the shared `name` — so checking `group_name in filled_ids` alone
    would miss it. Check every radio sharing this name against filled_ids too."""
    if group_name in filled_ids:
        return True
    try:
        radios = _radios_by_name(page, group_name)
        count = min(radios.count(), 10)
        for i in range(count):
            radio = radios.nth(i)
            if radio.is_checked():
                return True
            identity = radio.get_attribute("id") or radio.get_attribute("name")
            if identity and identity in filled_ids:
                return True
    except Exception:
        pass
    return False


def find_unhandled_required_fields(
    page, filled_ids: set[str], limit: int = 30
) -> list["_UnhandledField"]:
    """Returns full metadata (label, field type, options) for every required field
    left over after fill_known_fields, instead of just a bool — so a genuinely
    custom question can be captured for a Q&A bank / human review rather than its
    text being thrown away. Radio inputs sharing a `name` are one logical question
    and are collapsed into a single entry, keyed by group name rather than by each
    radio's own id."""
    required = page.locator("[required], [aria-required='true']")
    count = min(required.count(), limit)
    unhandled: list[_UnhandledField] = []
    seen_group_names: set[str] = set()
    for i in range(count):
        el = required.nth(i)
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            input_type = (el.get_attribute("type") or "").lower() if tag == "input" else ""
        except Exception:
            tag, input_type = "", ""

        if input_type == "radio":
            try:
                group_name = el.get_attribute("name")
                radio_id = el.get_attribute("id")
            except Exception:
                group_name, radio_id = None, None
            if not group_name or group_name in seen_group_names:
                continue
            seen_group_names.add(group_name)
            if _radio_group_filled(page, group_name, filled_ids):
                continue
            label = _radio_group_label(page, el)
            if not label:
                continue
            # Rebind to this radio's own id rather than keeping the positional
            # nth() locator `el` already is (see the rebind below for why).
            stable_el = _by_id(page, radio_id) if radio_id else el
            unhandled.append(
                _UnhandledField(
                    descriptor=QuestionDescriptor(
                        label=label,
                        field_type="radio",
                        options=_radio_group_options(page, group_name),
                    ),
                    identity=group_name,
                    locator=stable_el,
                    control_kind="radio_group",
                )
            )
            continue

        try:
            real_id = el.get_attribute("id")
            identity = real_id or el.get_attribute("name")
        except Exception:
            real_id, identity = None, None
        if not identity or identity in filled_ids:
            continue

        label = _accessible_label(page, el)
        if not label:
            continue
        if tag == "select":
            field_type, options = "select", _select_options(el)
        elif tag == "textarea":
            field_type, options = "textarea", []
        elif input_type == "checkbox":
            field_type, options = "checkbox", []
        else:
            field_type, options = "text", []
        # Rebind to a stable id-based selector instead of the positional nth()
        # locator `el` still is at this point — confirmed live (a Greenhouse
        # multi-select checkbox group): filling an *earlier* field in this same
        # unhandled list, later in this function's caller's loop, can change
        # what's on the page (show/hide a conditional field, a group reacting to
        # another option being checked, anything), silently shifting which
        # element a later field's own trailing nth() locator resolves to by the
        # time anything actually acts on it — not necessarily the same element
        # `label`/`field_type` above were even read from, or the same kind of
        # element at all ("Not a checkbox or radio button" is the confirmed
        # live failure this caused). Mirrors fill_text's own re-binding, done
        # for exactly the same reason.
        stable_el = _by_id(page, real_id) if real_id else el
        unhandled.append(
            _UnhandledField(
                descriptor=QuestionDescriptor(label=label, field_type=field_type, options=options),
                identity=identity,
                locator=stable_el,
                control_kind=_control_kind(stable_el),
            )
        )

    # A second, independent pass: the button-pair yes/no toggle (see module comment
    # above _YESNO_TOGGLE_YES_SELECTOR) never carries required/aria-required at all,
    # so it can never show up via the `[required], [aria-required='true']` scan
    # above — this is the only way any question rendered this way, known or not, is
    # ever caught rather than silently reaching Submit unanswered.
    for yes_button, no_button, container in _yesno_toggle_pairs(page):
        if not _yesno_toggle_is_required(container):
            continue
        identity = _yesno_toggle_identity(container)
        label = _yesno_toggle_label(container)
        if not label:
            continue
        if (identity and identity in filled_ids) or label in filled_ids:
            continue
        if _yesno_toggle_answered(yes_button, no_button):
            continue
        unhandled.append(
            _UnhandledField(
                descriptor=QuestionDescriptor(
                    label=label, field_type="radio", options=["Yes", "No"]
                ),
                identity=identity or label,
                locator=container,
                control_kind="button_toggle",
            )
        )

    # A third, independent pass: the same broken `for`/id label pattern shows up on
    # other Ashby field types too (confirmed on a freeform location-autocomplete
    # combobox, not just the yes/no toggle above) — a required text/select/textarea
    # field whose control carries neither `required` nor `aria-required` is just as
    # invisible to the scan at the top of this function. This is the generic net:
    # not scoped to any specific known label/alias, so a completely novel Ashby
    # field rendered this way is still caught, not just the ones already named
    # above. fill_text's own broken-label fallback (_control_for_broken_label)
    # already resolves and fills whatever this *can* recognize by alias — this is
    # only what's left over after that, exactly like the main scan above.
    for label_el in _broken_required_labels(page):
        try:
            container = _field_entry_container(label_el)
            # Already covered by the yes/no toggle pass above — don't double-report.
            if container.locator(_YESNO_TOGGLE_YES_SELECTOR).count() > 0:
                continue
            # Confirmed live on Harvey's Frontend Platform application: Ashby
            # renders a required four-option radio question with no required or
            # aria-required attribute on any radio. The only required signal is
            # the same class marker on a dangling question label used by its
            # id-less Location autocomplete. Treat all same-named radios in this
            # field-entry as one control; refuse containers with mixed/nameless
            # groups rather than guessing their boundaries.
            radios = container.locator("input[type='radio']")
            if radios.count() > 0:
                group_names = {
                    name
                    for i in range(min(radios.count(), 20))
                    if (name := radios.nth(i).get_attribute("name"))
                }
                if len(group_names) != 1:
                    continue
                group_name = next(iter(group_names))
                if group_name in seen_group_names:
                    continue
                seen_group_names.add(group_name)
                label = (label_el.inner_text() or "").strip()
                if not label or _radio_group_filled(page, group_name, filled_ids):
                    continue
                group_radios = _radios_by_name(page, group_name)
                unhandled.append(
                    _UnhandledField(
                        descriptor=QuestionDescriptor(
                            label=label,
                            field_type="radio",
                            options=_radio_group_options(page, group_name),
                        ),
                        identity=group_name,
                        locator=group_radios.first,
                        control_kind="radio_group",
                    )
                )
                continue
            control = container.locator(
                "input:not([type='checkbox']):not([type='file']), select, textarea"
            )
            if control.count() != 1:
                continue
            el = control.first
            label = (label_el.inner_text() or "").strip()
            if not label:
                continue
            identity = el.get_attribute("id") or el.get_attribute("name") or label
            if identity in filled_ids or label in filled_ids:
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            current_value = el.evaluate("e => e.value") or ""
            if current_value.strip():
                continue  # fill_text's own fallback (or a prior pass) already handled it
            if tag == "select":
                field_type, options = "select", _select_options(el)
            elif tag == "textarea":
                field_type, options = "textarea", []
            else:
                field_type, options = "text", []
            unhandled.append(
                _UnhandledField(
                    descriptor=QuestionDescriptor(
                        label=label, field_type=field_type, options=options
                    ),
                    identity=identity,
                    locator=el,
                    control_kind=_control_kind(el),
                )
            )
        except Exception:
            continue
    return unhandled


def has_unhandled_required_fields(page, filled_ids: set[str]) -> bool:
    """Thin bool wrapper over find_unhandled_required_fields for any caller that
    only needs the yes/no signal, not the captured question metadata."""
    return bool(find_unhandled_required_fields(page, filled_ids))


# Confirmed live on Greenhouse: a "select all that apply" checkbox group (e.g.
# "Do you have experience in the Creator Economy beyond Twitch?") can mark *every*
# individual checkbox `required`, not just the group as a whole — but native HTML5
# has no way to express "at least one of these N checkboxes", only "this specific
# checkbox must be checked". So the moment any answer other than "check every single
# option" is correct (the overwhelmingly common case — most applicants only match
# some of the listed platforms, or none), every unchecked-but-required sibling stays
# natively `:invalid` forever, no matter how correctly the group was actually filled.
# engine.py's final pre-submit gate uses `:invalid` as its ground truth specifically
# to catch a form that's genuinely still broken — treating this shape as broken too
# was a false positive blocking an otherwise fully and correctly answered form.
#
# A second, unrelated false-positive confirmed live on the same Greenhouse posting:
# several already-correctly-filled custom questions (react-select-style comboboxes)
# each back their own native validity with a hidden proxy <input required
# aria-hidden="true" tabindex="-1"> and no id/name of its own — the exact pattern
# _combobox_selection_confirmed already treats with suspicion right after a fill,
# just never previously excused here at the final gate too. Confirmed live: these
# proxies can still show up `:invalid` at this later point even though the widget
# they back was correctly filled and re-verified — and since they carry no id/name,
# neither this code nor a human could ever target one directly; there's no real
# question here to leave unanswered, just inert internal plumbing.
_INVALID_COUNT_JS = """
() => {
    function isExcusable(el) {
        if (el.tagName === 'INPUT' && el.type === 'checkbox' && el.name) {
            const group = document.querySelectorAll(
                'input[type="checkbox"][name="' + CSS.escape(el.name) + '"]'
            );
            // At least one sibling in this same-named group is checked -- the
            // group's own real intent ("pick the ones that apply") is satisfied,
            // even though this specific unchecked box can never stop being
            // natively :invalid on its own.
            return Array.from(group).some((cb) => cb.checked);
        }
        if (
            el.tagName === 'INPUT' &&
            el.getAttribute('aria-hidden') === 'true' &&
            el.tabIndex === -1 &&
            !el.id &&
            !el.name
        ) {
            return true;
        }
        if (el.tagName === 'INPUT' && el.required && el.value === '') {
            // A react-select-style combobox's own underlying search input can be
            // required *and* stay empty by design even once a value is genuinely
            // selected -- the selection lives in the app's own state, rendered via
            // a separate nearby "selected value" element, never written back into
            // this input's own value. Confirmed live: Paylocity's Country field
            // shows "United States" this way and is correctly left untouched once
            // already correct (see fill_exact_choice), but still failed this
            // check for exactly that reason. Same walk-up/selector
            // _react_select_selected_labels already uses to detect this, so
            // "already correct" and "excused here" always agree with each other.
            //
            // Climbing several ancestor levels risks reaching a container wide
            // enough to also hold *other*, unrelated required fields -- at that
            // point querySelector (which searches every descendant) could find
            // a completely different field's own selected-value display and
            // wrongly excuse a genuinely-blank one. Stop at the first level
            // that has any selected-value display at all, and only trust it if
            // that same container holds exactly one required input -- this one.
            // A shared/ambiguous container refuses rather than risk a false
            // excuse, the same posture as everywhere else in this file.
            let node = el.parentElement;
            for (let depth = 0; depth < 8 && node; depth += 1, node = node.parentElement) {
                const selected = node.querySelector(
                    '[class*="single-value"], [class*="multi-value__label"]'
                );
                if (!selected || !(selected.textContent || '').trim()) continue;
                const requiredHere = node.querySelectorAll('input[required]');
                return requiredHere.length === 1 && requiredHere[0] === el;
            }
        }
        if (el.tagName === 'FIELDSET' || el.tagName === 'FORM') {
            // A container only inherits :invalid from its own descendants -- excuse
            // it too, but only if every single invalid descendant is itself
            // excusable (never if it's invalid for some other, real reason).
            const invalidDescendants = Array.from(el.querySelectorAll(':invalid'));
            return invalidDescendants.length > 0 && invalidDescendants.every(isExcusable);
        }
        return false;
    }
    return Array.from(document.querySelectorAll(':invalid')).filter(
        (el) => !isExcusable(el)
    ).length;
}
"""


def count_genuinely_invalid(page) -> int:
    """The page's own `:invalid` count, minus (a) any unchecked-but-required
    checkbox whose same-named group already has a checked sibling, (b) any hidden,
    id-less/name-less validation-proxy input for some other widget, and (c) any
    FIELDSET/FORM whose own invalidity is caused only by (a)/(b) — see the module
    comment above _INVALID_COUNT_JS for why none of those indicate a real problem.
    Falls back to the raw, uncorrected `:invalid` count on any evaluation error,
    since refusing to submit is always the safe failure mode here."""
    try:
        return page.evaluate(_INVALID_COUNT_JS)
    except Exception:
        return page.locator(":invalid").count()


_INVALID_LABELS_JS = _INVALID_COUNT_JS.replace(
    "    ).length;",
    "    ).filter((el) => el.tagName !== 'FIELDSET' && el.tagName !== 'FORM')"
    "     .map((el) => el.id || el.name || (el.tagName + '[type=' + el.type + ']'));",
)


def genuinely_invalid_field_labels(page) -> list[str]:
    """Same excusability logic as count_genuinely_invalid, but naming each
    surviving invalid element (id, or name, or a short tag/type fallback)
    instead of just counting them — purely diagnostic, for logging which
    field(s) the final gate actually refused on instead of just how many."""
    try:
        return page.evaluate(_INVALID_LABELS_JS)
    except Exception:
        return []


def _fill_answer(page, field: "_UnhandledField", value: str) -> bool:
    """Applies an accepted AnswerAttempt's value to a previously-captured unhandled
    field, using the same per-type fill strategy fill_text/fill_yes_no already use —
    kept separate from those since this operates on an already-resolved locator
    instead of searching the page by label."""
    descriptor = field.descriptor
    try:
        if descriptor.field_type in ("text", "textarea"):
            # find_unhandled_required_fields buckets a react-select-style combobox
            # (role="combobox") into "text" too, since its underlying tag is a plain
            # <input> — but a raw .fill() on one of these doesn't register at all:
            # confirmed live, it leaves the widget's real value empty and the form
            # still invalid, while returning True here and letting the caller
            # believe the field was actually answered. Route it through the same
            # open-and-select mechanism fill_yes_no already uses instead.
            if field.control_kind == "autocomplete":
                return _fill_autocomplete(page, field.locator, value)
            if field.control_kind == "react_select" or (
                field.control_kind == "native"
                and (field.locator.get_attribute("role") or "") == "combobox"
            ):
                return _fill_react_select(page, field.locator, value)
            # Real typed keystrokes, not .fill(): the same "looks like a plain
            # <input> but secretly needs genuine per-keystroke events to
            # register with the page's own validation" shape fill_typed was
            # built for (confirmed live on this exact Paylocity address block)
            # applies here too -- an approved answer replayed via .fill() can
            # fail the same way the original Tier A attempt did. Verified, not
            # a bare .type(): a fast per-keystroke sequence can drop a
            # character on a page whose own JS does heavy per-keystroke work
            # (see _type_verified) -- confirmed live as "765 Montague Expy"
            # landing as "765 ontague Expy" with no error anywhere.
            return _type_verified(field.locator, value)
        if descriptor.field_type == "select":
            field.locator.select_option(label=re.compile(re.escape(value), re.IGNORECASE))
            return True
        if descriptor.field_type == "radio":
            # find_unhandled_required_fields hands back the toggle-pair's own
            # container (not a radio input) as field.locator for this shape (see
            # _yesno_toggle_pairs) — check that directly before assuming a native
            # radio group. A real radio <input> has no descendants of its own, so
            # this is a no-op fall-through for the existing radio-group case below.
            yes_button = field.locator.locator(_YESNO_TOGGLE_YES_SELECTOR)
            no_button = field.locator.locator(_YESNO_TOGGLE_NO_SELECTOR)
            if field.control_kind == "button_toggle" or (
                yes_button.count() == 1 and no_button.count() == 1
            ):
                return _click_yesno_toggle(yes_button.first, no_button.first, value)
            container = field.locator.locator(
                "xpath=ancestor::fieldset[1] | ancestor::*[contains(@class,'field')][1]"
            ).first
            radio = container.get_by_role("radio", name=value.strip(), exact=True)
            if radio.count() == 1:
                radio.first.check()
                return radio.first.is_checked()
            return False
        if descriptor.field_type == "checkbox":
            truthy = value.strip().lower() in ("yes", "true", "agree", "checked")
            if truthy:
                field.locator.check()
            else:
                field.locator.uncheck()
            return True
    except Exception:
        return False
    return False
