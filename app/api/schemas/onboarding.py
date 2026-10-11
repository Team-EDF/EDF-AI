from typing import Optional

from pydantic import BaseModel


class OnboardingAnswers(BaseModel):
    """
    온보딩 설문 답변 (프로토타입/데모 전용).

    필드명·옵션값은 백엔드가 실제 설문 문항을 확정하기 전까지의 잠정 이름이다.
    """

    transport: Optional[str] = None          # "car" | "public_transit" | "walk_bike"
    transport_spend: Optional[str] = None    # transport="car" -> "under_100k"|"100k_to_300k"|"over_300k"
                                              # transport="public_transit" -> "under_30k"|"30k_to_70k"|"over_70k"
                                              # transport="walk_bike" -> 없음 (후속 질문 자체가 안 뜸)
    cafe_drink: Optional[str] = None         # "none" | "under_30k" | "over_30k"
    food: Optional[str] = None               # "under_100k" | "100k_to_300k" | "over_300k"
    shopping: Optional[str] = None           # "under_50k" | "50k_to_150k" | "over_150k"
    eco_interest: Optional[str] = None       # "not_interested" | "interested_not_tried" | "tried"
    goal_intent: Optional[str] = None        # "light_start" | "serious_reduction" | "just_looking"


class OnboardingTargetResponse(BaseModel):
    estimated_spend_krw: dict
    carbon_breakdown_kg: dict
    baseline_carbon_kg: float
    reduction_rate: Optional[float]
    target_carbon_kg: Optional[float]
    message: str
