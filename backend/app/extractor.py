import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

ATS_DOMAINS = {
    "greenhouse": "greenhouse",
    "lever.co": "lever",
    "myworkdayjobs": "workday",
    "workday.com": "workday",
    "ashbyhq": "ashby",
    "workable": "workable",
    "bamboohr": "bamboohr",
    "icims": "icims",
    "jobvite": "jobvite",
    "smartrecruiters": "smartrecruiters",
    "recruitee": "recruitee",
}


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first_key(data: dict, keys: tuple[str, ...], default: str = "") -> str:
    wanted = {key.lower() for key in keys}
    for key, value in _walk(data):
        if key.lower() in wanted and isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return default


def _display_value(value: Any, keys: tuple[str, ...]) -> str:
    """Extract a useful label from fields that drift between strings and objects."""
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip()
    if isinstance(value, dict):
        return _first_key(value, keys)
    return ""


def _first_display(
    data: dict, field_keys: tuple[str, ...], value_keys: tuple[str, ...], default: str
) -> str:
    wanted = {key.lower() for key in field_keys}
    for key, value in _walk(data):
        if key.lower() in wanted:
            label = _display_value(value, value_keys)
            if label:
                return label
    return default


def _urls(data: dict) -> list[str]:
    found = []
    for _, value in _walk(data):
        if isinstance(value, str):
            found.extend(re.findall(r"https?://[^\s\"'<>]+", value))
    return found


def _rendered_description(raw: dict) -> str:
    """Build scoreable text from fields present in HiringCafe's public HTML payload."""
    processed = raw.get("v5_processed_job_data")
    if not isinstance(processed, dict):
        return ""
    sections: list[str] = []
    summary = processed.get("requirements_summary")
    if isinstance(summary, str) and summary.strip():
        sections.extend(("Requirements", summary.strip()))
    activities = processed.get("role_activities")
    if isinstance(activities, list) and activities:
        sections.append("Responsibilities")
        sections.extend(str(item).strip() for item in activities if str(item).strip())
    skills = processed.get("technical_tools")
    if isinstance(skills, list) and skills:
        sections.extend(
            ("Skills", ", ".join(str(item).strip() for item in skills if str(item).strip()))
        )
    certifications = processed.get("licenses_or_certifications")
    if isinstance(certifications, list) and certifications:
        sections.extend(("Licenses and certifications", ", ".join(map(str, certifications))))
    return "\n".join(sections)


def extract_job(raw: dict) -> dict:
    title = _first_display(
        raw,
        ("title", "job_title", "core_job_title"),
        ("name", "title", "label", "job_title", "core_job_title"),
        "Untitled role",
    )
    company = _first_display(
        raw,
        ("company_name", "companyName", "company"),
        ("name", "company_name", "companyName", "label"),
        "Unknown company",
    )
    description_html = _first_key(raw, ("description", "job_description", "jobDescription", "text"))
    description = BeautifulSoup(description_html, "html.parser").get_text("\n", strip=True)
    if not description:
        description = _rendered_description(raw)

    apply_url = _first_key(raw, ("apply_url", "applyUrl", "url", "job_url", "source_url", "link"))
    candidates = _urls(raw)
    preferred = [u for u in candidates if any(domain in u.lower() for domain in ATS_DOMAINS)]
    apply_is_internal = "hiring.cafe" in apply_url.lower()
    if preferred and (
        not apply_url
        or apply_is_internal
        or not any(domain in apply_url.lower() for domain in ATS_DOMAINS)
    ):
        apply_url = preferred[0]
    elif not apply_url and candidates:
        apply_url = next((u for u in candidates if "hiring.cafe" not in u.lower()), candidates[0])

    domain = urlparse(apply_url).netloc.lower()
    ats = next((name for marker, name in ATS_DOMAINS.items() if marker in domain), "career-page")
    external_id = _first_key(raw, ("external_id", "externalId", "job_id", "jobId", "id", "_id"))
    if not external_id:
        external_id = hashlib.sha256(f"{title}|{company}|{apply_url}".encode()).hexdigest()
    return {
        "external_id": external_id,
        "title": title,
        "company": company,
        "description": description,
        "apply_url": apply_url,
        "ats_platform": ats,
    }
