<<<<<<< HEAD
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import merchant, ocr

app = FastAPI(title="GreenStep AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(merchant.router, prefix="/api", tags=["classify"])
app.include_router(ocr.router, prefix="/api", tags=["ocr"])

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_ui():
    return FileResponse("static/index.html")
=======
from fastapi import FastAPI, UploadFile, File, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import Base, engine, SessionLocal
from app.services.ocr_service import extract_text_from_image
from app.services.ocr_save_service import save_ocr_result
from app.services.receipt_parser_service import parse_receipt_text
from app.services.eco_feedback_service import generate_eco_feedback
from app.services.rag_index_service import build_rag_index
from app.services.feedback_service import generate_feedback
from app.models.chat_history import ChatHistory
from app.services.chat_history_service import save_chat_history

from app.api.routes import merchant

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
>>>>>>> origin/feat/feedback-chat


@app.get("/health")
def health_check():
    return {"status": "ok"}


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


class ChatFeedbackRequest(BaseModel):
    user_id: int | None = None
    message: str
    consumption_summary: dict


@app.post("/feedback/chat")
def chat_feedback(request: ChatFeedbackRequest, db: Session = Depends(get_db)):
    feedback = generate_feedback(
        user_message=request.message,
        consumption_summary=request.consumption_summary
    )

    saved_chat = save_chat_history(
        db=db,
        user_id=request.user_id,
        user_message=request.message,
        consumption_summary=request.consumption_summary,
        ai_response=feedback
    )

    return {
        "chat_id": saved_chat.id,
        "feedback": feedback
    }