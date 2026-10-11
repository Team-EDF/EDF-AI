
from typing import Any, Literal

from pydantic import BaseModel, Field

ChatType = Literal["RECEIPT", "HOUSEHOLD"]


class FeedbackChatRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    chat_type: ChatType = Field(default="RECEIPT")
    record_id: int | None = Field(default=None, gt=0)
    message: str = Field(..., min_length=1, max_length=1000)

    # Spring Boot가 조회한 관리비 기록
    household_summary: list[dict[str, Any]] | None = None

    # Spring Boot가 조회한 영수증 및 소비내역 요약
    # 특정 영수증 또는 여러 영수증의 집계 데이터를 전달한다.
    receipt_summary: dict[str, Any] | None = None


class FeedbackChatResponse(BaseModel):
    chat_id: int | None = None
    conversation_id: int
    user_id: int
    chat_type: ChatType = "RECEIPT"
    record_id: int | None = None
    message: str
    feedback: str
    total_carbon_kg: float | None = None
    highest_carbon_category: str | None = None
    rag_category: str | None = None
    rag_sources: list[dict[str, Any]] = Field(default_factory=list)
