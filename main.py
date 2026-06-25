from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import Base, engine, SessionLocal
from app.api.routes import merchant, ocr

from app.services.eco_feedback_service import generate_eco_feedback
from app.services.rag_index_service import build_rag_index
from app.services.feedback_service import generate_feedback
from app.services.chat_history_service import save_chat_history
from app.services.consumption_summary_service import get_user_consumption_summary

Base.metadata.create_all(bind=engine)

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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def serve_ui():
    return FileResponse("static/index.html")


@app.get("/health")
def health_check():
    return {"status": "ok"}


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