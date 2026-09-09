from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from app.api.schemas.feedback import (
    FeedbackChatRequest,
    FeedbackChatResponse,
)
from app.database.connection import get_db
from app.services.feedback_service import FeedbackService


router = APIRouter()

feedback_service = FeedbackService()


@router.post(
    "/feedback/chat",
    response_model=FeedbackChatResponse,
)
async def feedback_chat(
    request: FeedbackChatRequest,
    conn=Depends(get_db),
):
    """
    저장된 소비기록(record_id)을 기준으로
    RAG + Gemini 친환경 피드백을 생성한다.
    """

    try:
        result = feedback_service.chat(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            record_id=request.record_id,
            user_message=request.message,
            conn=conn,
        )

        return FeedbackChatResponse(
            **result
        )

    except ValueError as error:
        # 존재하지 않는 record_id
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except Exception as error:
        print(
            "[Feedback API] "
            f"피드백 생성 실패: "
            f"{type(error).__name__}: {error}"
        )

        raise HTTPException(
            status_code=500,
            detail=f"피드백 생성 실패: {error}",
        ) from error