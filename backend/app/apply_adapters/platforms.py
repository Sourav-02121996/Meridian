"""Per-ATS configuration layered on top of the shared generic engine (fields.py,
submit.py). An entry here is optional tuning, not a separate implementation — a
platform with no entry still gets full support through `_GENERIC`, which is what
makes "any ATS/career-page URL Meridian hasn't seen before" a safe, working case
rather than an unsupported one (issue #19).

Keyed by `Job.ats_platform`, which extractor.py and sheet_import.py both already
compute from the apply URL's domain — no new detection logic needed here.
"""

import re
from dataclasses import dataclass, field

DEFAULT_CONFIRMATION_PATTERN = re.compile(
    r"thank you|application (was|has been) submitted|application received|"
    r"successfully submitted|we('ve| have) received your application",
    re.IGNORECASE,
)
# Secondary, weaker confirmation signal: the resulting URL itself looks like a
# success/confirmation page. Checked *in addition to* on-page text, never instead of
# it — a URL pattern alone is too easy to false-positive on.
SUCCESS_URL_PATTERN = re.compile(r"(thank[-_]?you|confirmation|success)", re.IGNORECASE)
# The posting itself closed out from under the attempt (confirmed real copy from
# Greenhouse's own "no longer accepting applications" message) — checked after a
# submit click gets neither a success nor a still-invalid-field signal, so this
# gives a genuinely different, more specific reason than a bare "we're not sure"
# for the one case where the page itself is telling us exactly what happened.
LISTING_CLOSED_PATTERN = re.compile(
    r"no longer accepting applications|position has been filled|posting (has|is) closed|"
    r"job (is|has) no longer available",
    re.IGNORECASE,
)

WORKDAY_PLATFORM = "workday"
# Workday postings typically require creating a candidate account and stepping
# through a multi-page wizard before any of the fields this engine fills are even
# reachable, and an aborted attempt partway through can leave a half-created external
# account behind — a different, harder-to-detect failure mode than "the form didn't
# submit". That gap from the single-page-form model everything else here assumes is
# large enough to warrant its own scoped effort rather than being forced through this
# abstraction, so it's excluded outright rather than routed through the generic
# fallback. See issue #19 discussion.
WORKDAY_UNSUPPORTED_REASON = "unsupported_multi_step"


@dataclass
class PlatformConfig:
    name: str
    confirmation_pattern: re.Pattern = DEFAULT_CONFIRMATION_PATTERN
    # CSS fallback tried only if the ranked submit-control search in submit.py comes
    # up empty or ambiguous — e.g. Greenhouse's historically stable `#submit_app` id.
    submit_fallback_selector: str | None = None
    # Extra label aliases tried *in addition to* fields.TEXT_LABELS' defaults for a
    # profile key, for platforms known to phrase a question differently.
    extra_text_labels: dict[str, list[str]] = field(default_factory=dict)
    # Ashby persists every individual field through a separate GraphQL mutation.
    # Those mutations return a full form snapshot, so letting two overlap can let a
    # stale response replace a more recent selection in the page's canonical form
    # state.  The engine therefore waits for one save to settle before making the
    # next field change when this is enabled.
    serialize_field_saves: bool = False
    # "networkidle" was tried first and dropped: pages with persistent background
    # connections (analytics beacons, chat widgets — confirmed on Lever) never settle,
    # timing the whole attempt out even though the form rendered almost immediately.
    # "domcontentloaded" plus engine.py's bounded post-navigation readiness wait is a
    # far more reliable signal for "is the form actually here yet".
    wait_until: str = "domcontentloaded"


_GENERIC = PlatformConfig(name="generic")

# Named tuning for the platforms extractor.ATS_DOMAINS already recognizes. Not being
# listed here is not "unsupported" — resolve_platform() falls back to _GENERIC, which
# is a fully working, conservative adapter on its own.
_PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    "greenhouse": PlatformConfig(name="greenhouse", submit_fallback_selector="#submit_app"),
    "lever": PlatformConfig(name="lever"),
    "ashby": PlatformConfig(
        name="ashby",
        # Confirmed live: Ashby renders this question as "Current or Most Recent
        # Employer", which the generic "Current Employer"/"Current Company" aliases in
        # fields.TEXT_LABELS don't substring-match.
        extra_text_labels={"current_company": ["Current or Most Recent Employer"]},
        serialize_field_saves=True,
    ),
    # The aliases below are seeded from each platform's publicly documented/observed
    # standard application-form copy, not a guess — same bar as the Ashby entry above.
    # Being unlisted was never "unsupported" (falls back to _GENERIC either way); this
    # just trims a few more common false-positive "custom_questions" cases per platform.
    "workable": PlatformConfig(
        name="workable",
        extra_text_labels={"current_company": ["Current Employer"]},
    ),
    "bamboohr": PlatformConfig(
        name="bamboohr",
        extra_text_labels={"linkedin": ["LinkedIn Profile URL"]},
    ),
    "icims": PlatformConfig(name="icims"),
    "jobvite": PlatformConfig(name="jobvite"),
    "smartrecruiters": PlatformConfig(
        name="smartrecruiters",
        extra_text_labels={"current_title": ["Current Job Title"]},
    ),
    "recruitee": PlatformConfig(name="recruitee"),
}


def is_workday(ats_platform: str | None) -> bool:
    return (ats_platform or "").strip().lower() == WORKDAY_PLATFORM


def resolve_platform(ats_platform: str | None) -> PlatformConfig:
    """Never returns None — every non-Workday platform gets at least the generic
    engine. Workday must be checked separately via is_workday() *before* calling
    this; it's excluded outright rather than routed through here at all."""
    return _PLATFORM_CONFIGS.get((ats_platform or "").strip().lower(), _GENERIC)
