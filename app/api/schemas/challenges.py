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
    verification_next: Optional[str] = None   # (사용 안 함) 예전 2차 계획 필드
    daily_check_limit: Optional[int] = None   # 자율 체크의 하루 체크 가능 횟수, 자동 인증은 None
    verification_methods: list[str] = []      # 화면에 보여 줄 인증 방식: ["AUTO_TRANSIT"] | ["SELF"] | ["SELF", "PHOTO"]
    photo_verification: Optional[str] = None  # 사진 인증 종류: "TUMBLER" | "LOW_CARBON" | None
    self_check_limit: Optional[int] = None    # 사진 인증이 있는 챌린지의 자율 체크 주간 인정 횟수 (없으면 제한 없음)
    cross_check: Optional[str] = None         # 서버 교차 검증: "NO_SHOPPING_RECEIPT" (무구매 챌린지) | None
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
    user_name: Optional[str] = None                     # 추천 이유 문구에서 "{이름}님"으로 부를 이름 (없으면 이름 없이 작성)


class RecommendedChallenge(CatalogChallenge):
    slot: int                    # 추천 순서 1~3 (1번이 가장 줄일 여지가 큰 영역)
    reason: str                  # 추천 이유 문구 (LLM 또는 고정 문구)
    reason_source: str           # "llm" | "fallback"


class RecommendResponse(BaseModel):
    source: str                  # "survey"
    intro: str                   # 예: "AI가 생활패턴을 분석했어요."
    challenges: list[RecommendedChallenge]


# ---------------------------------------------------------------- 사진 인증 (POST /challenges/verify)

class VerifyReceipt(BaseModel):
    merchant_name: Optional[str] = None
    payment_date: Optional[str] = None      # YYYY-MM-DD
    payment_time: Optional[str] = None      # HH:MM
    total_amount: Optional[int] = None
    fingerprint: Optional[str] = None       # 영수증 중복 방지용 해시 (날짜·시각·금액). 읽지 못하면 None


class VerifyResponse(BaseModel):
    passed: bool
    code: str                    # "OK" | RECEIPT_UNREADABLE | RECEIPT_DATE | RECEIPT_TOO_OLD | RECEIPT_EDITED | NOT_CAFE | NO_TUMBLER | NO_MARK
    message: str                 # 사용자에게 그대로 보여 줄 한국어 안내
    kind: str                    # "TUMBLER" | "LOW_CARBON"
    receipt: Optional[VerifyReceipt] = None
    evidence: Optional[str] = None            # 통과 근거: PHOTO_AND_RECEIPT | PHOTO | RECEIPT_DISCOUNT | MARK_AND_RECEIPT
    marks: list[str] = []                     # 인정된 저탄소 마크 종류
