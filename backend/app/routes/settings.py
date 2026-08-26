from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pypdf import PdfReader
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Workspace
from ..schemas import (
    AutoApplyThresholdRequest,
    ProfileRequest,
    ResumeRequest,
    ThresholdRequest,
    WorkspaceSettingsOut,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}/settings", tags=["settings"])


def _get_workspace(workspace_id: int, db: Session) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return workspace


@router.get("", response_model=WorkspaceSettingsOut)
def read_settings(workspace_id: int, db: Session = Depends(get_db)):
    workspace = _get_workspace(workspace_id, db)
    return WorkspaceSettingsOut(
        resume=workspace.resume_text,
        resume_filename=workspace.resume_filename,
        has_resume_file=bool(workspace.resume_file),
        threshold=workspace.threshold,
        auto_apply_threshold=workspace.auto_apply_threshold,
        profile_name=workspace.profile_name,
        profile_email=workspace.profile_email,
        profile_phone=workspace.profile_phone,
        profile_linkedin=workspace.profile_linkedin,
        profile_portfolio_url=workspace.profile_portfolio_url,
        profile_github_url=workspace.profile_github_url,
        profile_location=workspace.profile_location,
        profile_current_company=workspace.profile_current_company,
        profile_current_title=workspace.profile_current_title,
        profile_desired_salary=workspace.profile_desired_salary,
        profile_start_date=workspace.profile_start_date,
        profile_work_authorized=workspace.profile_work_authorized,
        profile_visa_sponsorship=workspace.profile_visa_sponsorship,
        profile_willing_to_relocate=workspace.profile_willing_to_relocate,
        profile_18_or_older=workspace.profile_18_or_older,
        profile_gender=workspace.profile_gender,
        profile_race_ethnicity=workspace.profile_race_ethnicity,
        profile_veteran_status=workspace.profile_veteran_status,
        profile_disability_status=workspace.profile_disability_status,
        cover_letter=workspace.cover_letter,
    )


@router.post("/resume")
def save_resume(workspace_id: int, payload: ResumeRequest, db: Session = Depends(get_db)):
    workspace = _get_workspace(workspace_id, db)
    workspace.resume_text = payload.text
    db.commit()
    return {"saved": True}


@router.post("/resume/pdf")
async def upload_resume_pdf(
    workspace_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    workspace = _get_workspace(workspace_id, db)
    filename = file.filename or "resume.pdf"
    if file.content_type not in {
        "application/pdf",
        "application/x-pdf",
    } and not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(413, "The PDF is larger than the 10 MB upload limit.")
    try:
        reader = PdfReader(BytesIO(contents))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:
        raise HTTPException(
            400, "The PDF could not be read. It may be damaged or password-protected."
        ) from exc
    if not text:
        raise HTTPException(
            422,
            "No selectable text was found. This may be a scanned PDF; export it with OCR and try again.",
        )
    # Keep the original file too (not just the extracted text) so batches can re-upload
    # it as-is when auto-applying through a supported ATS.
    workspace.resume_text = text
    workspace.resume_filename = filename
    workspace.resume_file = contents
    db.commit()
    return {"saved": True, "text": text, "filename": filename, "pages": len(reader.pages)}


@router.put("/threshold")
def save_threshold(workspace_id: int, payload: ThresholdRequest, db: Session = Depends(get_db)):
    workspace = _get_workspace(workspace_id, db)
    workspace.threshold = payload.value
    db.commit()
    return {"value": payload.value}


@router.put("/auto-apply-threshold")
def save_auto_apply_threshold(
    workspace_id: int, payload: AutoApplyThresholdRequest, db: Session = Depends(get_db)
):
    workspace = _get_workspace(workspace_id, db)
    workspace.auto_apply_threshold = payload.value
    db.commit()
    return {"value": payload.value}


@router.put("/profile")
def save_profile(workspace_id: int, payload: ProfileRequest, db: Session = Depends(get_db)):
    workspace = _get_workspace(workspace_id, db)
    workspace.profile_name = payload.name
    workspace.profile_email = payload.email
    workspace.profile_phone = payload.phone
    workspace.profile_linkedin = payload.linkedin
    workspace.profile_portfolio_url = payload.portfolio_url
    workspace.profile_github_url = payload.github_url
    workspace.profile_location = payload.location
    workspace.profile_current_company = payload.current_company
    workspace.profile_current_title = payload.current_title
    workspace.profile_desired_salary = payload.desired_salary
    workspace.profile_start_date = payload.start_date
    workspace.profile_work_authorized = payload.work_authorized
    workspace.profile_visa_sponsorship = payload.visa_sponsorship
    workspace.profile_willing_to_relocate = payload.willing_to_relocate
    workspace.profile_18_or_older = payload.is_18_or_older
    workspace.profile_gender = payload.gender
    workspace.profile_race_ethnicity = payload.race_ethnicity
    workspace.profile_veteran_status = payload.veteran_status
    workspace.profile_disability_status = payload.disability_status
    workspace.cover_letter = payload.cover_letter
    db.commit()
    return {"saved": True}
