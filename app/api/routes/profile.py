"""
Green Profile 엔드포인트: 설문 답변 -> 영역별 레벨 + 실천 성향 + 추천 목표.

백엔드가 설문 저장 후 호출한다. 규칙 기반이라 같은 답변이면 항상 같은 결과를 돌려준다.
"""
from fastapi import APIRouter, HTTPException

from app.api.schemas.profile import ProfileRequest, ProfileResponse
from app.services.profile_service import build_profile

router = APIRouter()


@router.post(
    "/profile",
    response_model=ProfileResponse,
)
def create_profile(request: ProfileRequest):
    try:
        answers = request.model_dump(exclude={"user_id"})
        return build_profile(answers, user_id=request.user_id)
    except Exception as e:
        # 보통 DB 연결/탄소계수 조회 실패 — 원인을 응답에 남겨 BE가 바로 확인할 수 있게 한다.
        raise HTTPException(status_code=500, detail=f"프로필 생성 실패: {type(e).__name__}: {str(e)[:150]}")
