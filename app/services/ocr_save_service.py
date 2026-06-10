from sqlalchemy.orm import Session
from app.models.ocr_result import OcrResult


def save_ocr_result(db: Session, filename: str, raw_text: str) -> OcrResult:
    result = OcrResult(filename=filename, raw_text=raw_text)

    db.add(result)
    db.commit()
    db.refresh(result)

    return result