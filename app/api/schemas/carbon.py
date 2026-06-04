from pydantic import BaseModel


# 2단계(SBERT) 카테고리 분류 완성 후 확정 예정
class CarbonCalculateRequest(BaseModel):
    merchant_name: str
    amount_krw: float


class CarbonCalculateResponse(BaseModel):
    merchant_name: str
    amount_krw: float
    co2eq_kg: float | None
