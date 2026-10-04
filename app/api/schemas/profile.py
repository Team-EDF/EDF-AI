from typing import Optional

from pydantic import BaseModel

from app.api.schemas.onboarding import OnboardingAnswers

class ProfileRequest(OnboardingAnswers):
    """
    설문 답변(필드명·옵션 값은 BE 문항 확정 후 맞출 예정) + 선택 항목 user_id.
    user_id를 주면 최근 30일 확정 영수증이 충분할 때 실데이터로 프로필을 계산한다.
    """
    user_id: Optional[int] = None


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
    percent: int             # 선택된 쪽 비율 50~95 (예: 뚜벅이 78)
    opposite_letter: str     # 반대쪽 글자, 예: "D"
    opposite_label: str      # 예: "드라이버"
    opposite_percent: int    # 반대쪽 비율 = 100 - percent


class ProfilePersona(BaseModel):
    """그린 유형 (4축 16유형, 식물·자연 테마)."""
    type_code: str           # 축 글자 4개, 예: "WLMA"
    type_name: str           # 예: "맑은 이슬 새싹"
    emoji: str
    tagline: str             # 한 줄 소개
    description: str
    axes: list[ProfileAxis]
    preferred_difficulty: int  # 챌린지 선호 난이도 1~3 (추천 시 사용)


class ProfileDataInfo(BaseModel):
    """user_id를 보냈을 때만 채워지는, 실데이터 사용 여부와 근거."""
    used: bool                       # 실데이터로 계산했는지
    receipts_in_window: int          # 최근 window_days일 확정 영수증 수
    window_days: int
    min_receipts: int                # 실데이터로 전환하는 최소 영수증 수
    reason: Optional[str] = None     # used가 False일 때 이유: "insufficient_receipts"


class ProfileResponse(BaseModel):
    source: str              # "survey" (설문 기반) | "data" (최근 30일 영수증 기반)
    data_info: Optional[ProfileDataInfo] = None
    areas: list[ProfileArea]
    persona: ProfilePersona
    focus_area: Optional[str]            # 먼저 시작할 영역 키, 전부 1레벨이면 None
    baseline_carbon_kg: float
    reduction_rate: Optional[float]      # "일단 구경만"이면 None
    target_carbon_kg: Optional[float]    # "일단 구경만"이면 None
    message: str
