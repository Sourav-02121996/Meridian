from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pypdf import PdfReader
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Setting
from ..schemas import ResumeRequest, ThresholdRequest

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_value(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def set_value(db: Session, key: str, value: str):
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


@router.get("")
def read_settings(db: Session = Depends(get_db)):
    configured = get_settings()
    return {
        "resume": get_value(db, "resume"),
        "threshold": float(get_value(db, "threshold", str(configured.score_threshold))),
    }


@router.post("/resume")
def save_resume(payload: ResumeRequest, db: Session = Depends(get_db)):
    set_value(db, "resume", payload.text)
    return {"saved": True}


@router.post("/resume/pdf")
async def upload_resume_pdf(file: UploadFile = File(...), db: Session = Depends(get_db)):
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
    set_value(db, "resume", text)
    return {"saved": True, "text": text, "filename": filename, "pages": len(reader.pages)}


@router.put("/threshold")
def save_threshold(payload: ThresholdRequest, db: Session = Depends(get_db)):
    set_value(db, "threshold", str(payload.value))
    return {"value": payload.value}
