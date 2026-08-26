"""Parses an uploaded .xlsx/.csv of jobs for an upload-mode batch.

The expected column layout is exactly what `export.py`'s "Download Excel" produces
(Score, Title, Company, ATS Platform, Status, Apply URL, Missing Skills, Weak
Requirements, Date Fetched, Date Applied) — the intended workflow is export, review
or edit offline, then re-upload to auto-apply unattended to whatever still clears the
threshold. Three extra columns (Requirement Coverage, Skill Coverage, Global
Similarity) are accepted if present but aren't part of the export format, so they
default to 0 when absent.
"""

import csv
import io
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from openpyxl import load_workbook

from .extractor import ATS_DOMAINS
from .models import Job, JobStatus

REQUIRED_HEADERS = {"title", "company", "apply url"}
HEADER_FIELDS = {
    "score": "score",
    "title": "title",
    "company": "company",
    "ats platform": "ats_platform",
    "status": "status",
    "apply url": "apply_url",
    "missing skills": "missing_skills",
    "weak requirements": "weak_requirements",
    "date fetched": "date_fetched",
    "date applied": "date_applied",
    "requirement coverage": "requirement_coverage",
    "skill coverage": "skill_coverage",
    "global similarity": "global_similarity",
}


class SheetImportError(RuntimeError):
    pass


def _normalize_header(value) -> str:
    return str(value or "").strip().lower()


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _split_list(value, separator: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(separator) if item.strip()]


def _parse_status(value) -> JobStatus:
    text = re.sub(r"\s+", "_", str(value or "").strip().lower())
    try:
        return JobStatus(text)
    except ValueError:
        return JobStatus.discovered


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _derive_ats(apply_url: str) -> str:
    domain = urlparse(apply_url).netloc.lower()
    return next((name for marker, name in ATS_DOMAINS.items() if marker in domain), "career-page")


def _rows_from_csv(contents: bytes) -> list[list]:
    text = contents.decode("utf-8-sig", errors="replace")
    return [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]


def _rows_from_xlsx(contents: bytes) -> list[list]:
    workbook = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    return [
        list(row)
        for row in sheet.iter_rows(values_only=True)
        if any(cell is not None for cell in row)
    ]


def parse_sheet(filename: str, contents: bytes) -> list[dict]:
    """Returns a list of field-name -> raw-cell-value dicts, one per row with an apply
    link. Raises SheetImportError for a bad file or a missing required column."""
    lower = (filename or "").lower()
    if lower.endswith(".csv"):
        rows = _rows_from_csv(contents)
    elif lower.endswith(".xlsx"):
        rows = _rows_from_xlsx(contents)
    else:
        raise SheetImportError("Please upload a .xlsx or .csv file.")
    if not rows:
        raise SheetImportError("The file is empty.")

    headers, *data_rows = rows
    normalized = [_normalize_header(h) for h in headers]
    missing = REQUIRED_HEADERS - set(normalized)
    if missing:
        raise SheetImportError(f"Missing required column(s): {', '.join(sorted(missing))}")
    fields = [HEADER_FIELDS.get(h) for h in normalized]

    parsed = []
    for raw_row in data_rows:
        record = {field: value for field, value in zip(fields, raw_row) if field}
        if str(record.get("apply_url", "")).strip():
            parsed.append(record)
    return parsed


def build_jobs_from_rows(rows: list[dict], workspace_id: int, batch_id: int) -> list[Job]:
    jobs = []
    for index, row in enumerate(rows):
        apply_url = str(row.get("apply_url", "")).strip()
        jobs.append(
            Job(
                workspace_id=workspace_id,
                external_id=f"import:{batch_id}:{index}",
                title=str(row.get("title") or "").strip() or "Untitled role",
                company=str(row.get("company") or "").strip() or "Unknown company",
                ats_platform=str(row.get("ats_platform") or "").strip().lower()
                or _derive_ats(apply_url),
                apply_url=apply_url,
                score=_to_float(row.get("score")),
                requirement_coverage=_to_float(row.get("requirement_coverage")),
                skill_coverage=_to_float(row.get("skill_coverage")),
                global_similarity=_to_float(row.get("global_similarity")),
                missing_skills=_split_list(row.get("missing_skills"), ","),
                weak_requirements=_split_list(row.get("weak_requirements"), ";"),
                status=_parse_status(row.get("status")),
                date_fetched=_parse_datetime(row.get("date_fetched")) or datetime.now(timezone.utc),
                date_applied=_parse_datetime(row.get("date_applied")),
                source_batch_id=batch_id,
            )
        )
    return jobs
