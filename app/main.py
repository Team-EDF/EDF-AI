from fastapi import FastAPI, UploadFile, File, Depends
from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app.models.ocr_result import OcrResult
from app.services.ocr_service import extract_text_from_image
from app.services.ocr_save_service import save_ocr_result
from app.services.receipt_parser_service import parse_receipt_text
from app.services.eco_feedback_service import generate_eco_feedback
from app.services.rag_index_service import build_rag_index

Base.metadata.create_all(bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "AI Server 실행 성공"}


@app.post("/ocr")
async def ocr(file: UploadFile = File(...), db: Session = Depends(get_db)):
    image_bytes = await file.read()

    text = extract_text_from_image(image_bytes)

    saved = save_ocr_result(
        db=db,
        filename=file.filename,
        raw_text=text
    )

    parsed_result = parse_receipt_text(text)

    return {
        "ocr_id": saved.id,
        "filename": saved.filename,
        "text": saved.raw_text,
        "parsed_result": parsed_result,
        "created_at": saved.created_at
    }


@app.post("/parse-test")
def parse_test(raw_text: str):
    return parse_receipt_text(raw_text)

@app.post("/feedback")
def feedback(parsed_receipt: dict):
    return generate_eco_feedback(parsed_receipt)

@app.post("/rag/index")
def rag_index():
    return build_rag_index()