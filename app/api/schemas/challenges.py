from typing import Optional

from pydantic import BaseModel


class CatalogChallenge(BaseModel):
    challenge_id: str            # 예: "MOVE_2"
    area: str                    # "move" | "cafe" | "food" | "shop" | "life"
    area_label: str              # "이동" | "카페" | "식품" | "쇼핑" | "생활"
    difficulty: int              # 1(쉬움) ~ 3(어려움)
    title: str
    description: str
    target_count: int            # 달성 기준 횟수/일수
    unit: str                    # "회" | "일"
    period: str                  # "week"
    verification: str            # "AUTO_TRANSIT"(대중교통 GPS 자동 인증) | "SELF"(자율 체크)
    verification_next: Optional[str] = None   # 2차 계획 인증방식 (예: "OCR")
    daily_check_limit: Optional[int] = None   # 자율 체크의 하루 체크 가능 횟수, 자동 인증은 None
    points: int                  # 완료 시 지급 포인트 (BE가 지급)
    est_saving_kg: Optional[float] = None     # 예상 절감량(가정치), 근거가 없으면 None
    saving_basis: Optional[str] = None        # 예상 절감량 계산 근거


class CatalogResponse(BaseModel):
    catalog_version: str
    challenges: list[CatalogChallenge]


# ---------------------------------------------------------------- 추천 (POST /challenges/recommend)

class RecommendArea(BaseModel):
    key: str                     # "move" | "food" | "cafe" | "shop"
    level: int                   # 1~5


class RecommendPersona(BaseModel):
    type_code: Optional[str] = None
    type_name: Optional[str] = None
    tagline: Optional[str] = None
    preferred_difficulty: Optional[int] = None   # 없으면 가장 쉬운 1로 처리


class RecommendProfile(BaseModel):
    """/api/profile 응답을 그대로 넘겨도 된다 (모르는 필드는 무시)."""
    source: Optional[str] = None
    areas: list[RecommendArea]
    persona: Optional[RecommendPersona] = None


class RecommendRequest(BaseModel):
    profile: Optional[RecommendProfile] = None
    user_id: Optional[int] = None                       # 실데이터 전환(2단계) 이후 사용
    exclude_challenge_ids: list[str] = []               # 이미 부여/완료한 챌린지


class RecommendedChallenge(CatalogChallenge):
    slot: int                    # 추천 순서 1~3 (1번이 가장 줄일 여지가 큰 영역)
    reason: str                  # 추천 이유 문구 (LLM 또는 고정 문구)
    reason_source: str           # "llm" | "fallback"


class RecommendResponse(BaseModel):
    source: str                  # "survey"
    intro: str                   # 예: "AI가 생활패턴을 분석했어요."
    challenges: list[RecommendedChallenge]
