"""Auto-apply adapter layer.

A shared, ATS-agnostic form-filling/submission engine (fields.py, submit.py) plus
lightweight per-platform tuning (platforms.py), replacing the old Greenhouse-only
gate that used to live in greenhouse_adapter.py. See issues #19/#20 for the design
discussion behind this split.
"""

from .engine import attempt_apply, fill_and_submit
from .fields import canonicalize_country
from .platforms import WORKDAY_UNSUPPORTED_REASON, PlatformConfig, is_workday, resolve_platform
from .types import AnswerAttempt, AnswerLookup, AutoApplyResult, QuestionDescriptor

__all__ = [
    "WORKDAY_UNSUPPORTED_REASON",
    "AnswerAttempt",
    "AnswerLookup",
    "AutoApplyResult",
    "PlatformConfig",
    "QuestionDescriptor",
    "attempt_apply",
    "canonicalize_country",
    "fill_and_submit",
    "is_workday",
    "resolve_platform",
]
