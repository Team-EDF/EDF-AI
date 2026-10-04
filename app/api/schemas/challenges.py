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
