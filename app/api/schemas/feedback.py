from typing import Any

from pydantic import BaseModel, Field


class FeedbackChatRequest(BaseModel):
    record_id: int = Field(
        ...,
        gt=0,
        description="OCR 처리 후 생성된 소비 기록 ID",
    )

    message: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="사용자의 질문",
    )


class FeedbackChatResponse(BaseModel):
    chat_id: int | None = None
    record_id: int
    message: str
    feedback: str
    total_carbon_kg: float | None = None
    highest_carbon_category: str | None = None

    rag_category: str | None = None

    rag_sources: list[dict[str, Any]] = Field(
        default_factory=list
    )