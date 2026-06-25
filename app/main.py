from fastapi import FastAPI, UploadFile, File, Depends, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.routes import merchant
from app.database import Base, engine, SessionLocal

from app.models.consumption_record import ConsumptionRecord
from app.models.chat_history import ChatHistory

from app.services.ocr_service import extract_text_from_image
from app.services.ocr_save_service import save_ocr_result
from app.services.receipt_parser_service import parse_receipt_text
from app.services.eco_feedback_service import generate_eco_feedback
from app.services.rag_index_service import build_rag_index
from app.services.feedback_service import generate_feedback
from app.services.chat_history_service import save_chat_history
from app.services.consumption_record_service import save_consumption_records
from app.services.consumption_summary_service import get_user_consumption_summary

Base.metadata.create_all(bind=engine)

app = FastAPI(title="GreenStep AI API", version="1.0.0")

app.include_router(merchant.router, prefix="/api", tags=["classify"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "AI Server 실행 성공"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/ocr")
async def ocr(
    user_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    print("[OCR] 1. 요청 수신")

    image_bytes = await file.read()
    print("[OCR] 2. 이미지 읽기 완료")

    text = extract_text_from_image(image_bytes)
    print("[OCR] 3. Google Vision OCR 완료")

    saved_ocr = save_ocr_result(
        db=db,
        filename=file.filename,
        raw_text=text
    )
    print("[OCR] 4. OCR 원문 DB 저장 완료")

    parsed_result = parse_receipt_text(text)
    print("[OCR] 5. 영수증 파싱 완료")
    print("[OCR] parsed_result:", parsed_result)

    saved_consumptions = save_consumption_records(
        db=db,
        user_id=user_id,
        parsed_result=parsed_result,
        ocr_id=saved_ocr.id
    )
    print("[OCR] 6. 소비기록 DB 저장 완료")
    print("[OCR] saved_consumption_count:", len(saved_consumptions))

    return {
        "ocr_id": saved_ocr.id,
        "user_id": user_id,
        "filename": saved_ocr.filename,
        "text": saved_ocr.raw_text,
        "parsed_result": parsed_result,
        "saved_consumption_count": len(saved_consumptions),
        "created_at": saved_ocr.created_at
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


class ChatFeedbackRequest(BaseModel):
    user_id: int
    message: str


@app.post("/feedback/chat")
def chat_feedback(request: ChatFeedbackRequest, db: Session = Depends(get_db)):
    consumption_summary = get_user_consumption_summary(
        db=db,
        user_id=request.user_id
    )

    result = generate_feedback(
        user_message=request.message,
        consumption_summary=consumption_summary
    )

    feedback_text = result["answer"]
    rag_sources = result["rag_sources"]

    saved_chat = save_chat_history(
        db=db,
        user_id=request.user_id,
        user_message=request.message,
        consumption_summary=consumption_summary,
        ai_response=feedback_text
    )

    return {
        "chat_id": saved_chat.id,
        "user_id": request.user_id,
        "message": request.message,
        "consumption_summary": consumption_summary,
        "feedback": feedback_text,
        "rag_sources": rag_sources
    }