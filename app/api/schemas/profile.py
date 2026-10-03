from typing import Optional

from pydantic import BaseModel

from app.api.schemas.onboarding import OnboardingAnswers

# 요청 본문은 설문 답변 스키마를 그대로 쓴다 (필드명·옵션 값은 BE 문항 확정 후 맞출 예정).
ProfileRequest = OnboardingAnswers


class ProfileArea(BaseModel):
    key: str                 # "move" | "food" | "cafe" | "shop"
    label: str               # "이동" | "식품" | "카페" | "쇼핑"
    level: int               # 1(매우 낮음) ~ 5(매우 높음)
    level_label: str
    carbon_kg: float         # 월 예상 탄소배출량(kg CO2e)
    spend_krw: int           # 월 예상 지출(원)


class ProfileAxis(BaseModel):
    axis: str                # "이동" | "식탁" | "소비" | "태도"
    letter: str              # 예: "W"
    label: str               # 예: "뚜벅이"


class ProfilePersona(BaseModel):
    """그린 유형 (4축 16유형, 식물·자연 테마)."""
    type_code: str           # 축 글자 4개, 예: "WLMA"
    type_name: str           # 예: "맑은 이슬 새싹"
    emoji: str
    tagline: str             # 한 줄 소개
    description: str
    axes: list[ProfileAxis]
    preferred_difficulty: int  # 챌린지 선호 난이도 1~3 (추천 시 사용)


class ProfileResponse(BaseModel):
    source: str              # "survey" (설문 기반) | "data" (실데이터 기반, 2단계)
    areas: list[ProfileArea]
    persona: ProfilePersona
    focus_area: Optional[str]            # 먼저 시작할 영역 키, 전부 1레벨이면 None
    baseline_carbon_kg: float
    reduction_rate: Optional[float]      # "일단 구경만"이면 None
    target_carbon_kg: Optional[float]    # "일단 구경만"이면 None
    message: str
