
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
    채팅 유형에 따라 친환경 피드백을 생성한다.

    RECEIPT:
        Spring Boot에서 전달한 영수증 소비내역 기반 상담

    HOUSEHOLD:
        Spring Boot에서 전달한 관리비 데이터 기반 상담
    """

    try:
        result = feedback_service.chat(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            record_id=request.record_id,
            user_message=request.message,
            chat_type=request.chat_type,
            household_summary=request.household_summary,
            receipt_summary=request.receipt_summary,
            conn=conn,
        )

        return (
            FeedbackChatResponse(
                **result,
                chat_type=request.chat_type,
            )
            if "chat_type" not in result
            else FeedbackChatResponse(**result)
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        import traceback
        traceback.print_exc()
        print(
            "[Feedback API] "
            f"피드백 생성 실패: "
            f"{type(error).__name__}: {error}"
        )

        raise HTTPException(
            status_code=500,
            detail="피드백 생성 중 서버 오류가 발생했습니다.",
        ) from error
