from pydantic import BaseModel
from typing import Optional, List


# ── 요청 모델 ────────────────────────────────────────────────────────────────

class ReceiptItem(BaseModel):
    """OCR로 인식한 세부 품목 1건"""
    item_name: str
    amount_krw: float


class ClassifyRequest(BaseModel):
    """
    OCR 영수증 분류 요청.
    - items 있을 때: 품목별 classify_from_item() 호출
    - items 없을 때: 가맹점명 기반 classify_from_merchant() 호출
    """
    merchant_name: str
    payment_location: Optional[str] = None   # 결제 위치 (도로명주소)
    payment_date: Optional[str] = None        # 결제 일자 (YYYY-MM-DD)
    items: Optional[List[ReceiptItem]] = None  # 세부 품목 및 금액 목록


# ── 응답 모델 ────────────────────────────────────────────────────────────────

class CategoryResult(BaseModel):
    """카테고리 분류 결과 단위"""
    category_type: str            # "main" | "middle"
    category_id: int
    category_name: str
    co2eq_KRW: Optional[float]    # kgCO2eq / KRW (탄소계수)
    similarity: float             # 유사도 (강제매핑: 1.0 / SBERT: 0.5~1.0)
    classify_stage: int           # 분류 단계 번호


class ItemResult(BaseModel):
    """세부 품목 1건의 분류 결과"""
    item_name: str
    amount_krw: float
    category: Optional[CategoryResult]  # None이면 gemini-2.5-flash-lite fallback 필요


class ClassifyResponse(BaseModel):
    """분류 응답"""
    merchant_name: str
    payment_location: Optional[str]
    payment_date: Optional[str]
    merchant_category: Optional[CategoryResult]  # items 없을 때 가맹점 기반 분류 결과
    item_results: Optional[List[ItemResult]]      # items 있을 때 품목별 분류 결과
