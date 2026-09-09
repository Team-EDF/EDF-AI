from typing import Any

from pydantic import BaseModel, Field


class FeedbackChatRequest(BaseModel):
    user_id: int = Field(
        ...,
        gt=0,
        description="사용자 ID",
    )

    conversation_id: int | None = Field(
        default=None,
        gt=0,
        description=(
            "대화방 ID. "
            "없으면 새로운 대화방을 생성합니다."
        ),
    )

    record_id: int | None = Field(
        default=None,
        gt=0,
        description="특정 소비 기록 ID (선택)",
    )

    message: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="사용자의 질문",
    )


class FeedbackChatResponse(BaseModel):
    chat_id: int | None = None

    conversation_id: int

    user_id: int

    record_id: int | None = None

    message: str

    feedback: str

    total_carbon_kg: float | None = None

    highest_carbon_category: str | None = None

    rag_category: str | None = None

    rag_sources: list[dict[str, Any]] = Field(
        default_factory=list
    )