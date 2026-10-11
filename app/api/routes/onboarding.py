"""
온보딩 설문 -> 추천 탄소 절감 목표 데모용 엔드포인트.

주의: 이건 백엔드/프론트가 호출할 공식 계약이 아니라, 로컬에서 계산 로직을
눈으로 확인해보기 위한 내부 테스트 전용 엔드포인트다 (ocr.py의 /clova/classify와
같은 성격). 실제 서비스에 붙일 때는 백엔드 담당자가 확정한 문항/필드명에 맞춰
app/services/onboarding_service.py를 다시 조정한 뒤 그대로 연결하면 된다.
"""
from fastapi import APIRouter

from app.api.schemas.onboarding import OnboardingAnswers, OnboardingTargetResponse
from app.services.onboarding_service import calculate_onboarding_target

router = APIRouter()


@router.post(
    "/onboarding/target",
    response_model=OnboardingTargetResponse,
)
def onboarding_target(answers: OnboardingAnswers):
    result = calculate_onboarding_target(answers.model_dump())
    return result
