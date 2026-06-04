from pydantic import BaseModel
from typing import Optional, List


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
    payment_location: Optional[str] = None
    payment_date: Optional[str] = None
    total_amount_krw: Optional[float] = None
    items: Optional[List[ReceiptItem]] = None


class CategoryResult(BaseModel):
    """카테고리 분류 결과 단위"""
    category_type: str
    category_id: Optional[int]
    category_name: str
    co2eq_KRW: Optional[float]
    similarity: float
    classify_stage: int


class ItemResult(BaseModel):
    """세부 품목 1건의 분류 결과"""
    item_name: str
    amount_krw: float
    category: Optional[CategoryResult]
    carbon_kg: Optional[float]


class ClassifyResponse(BaseModel):
    """분류 응답"""
    merchant_name: str
    payment_location: Optional[str]
    payment_date: Optional[str]
    merchant_category: Optional[CategoryResult]
    merchant_carbon_kg: Optional[float]
    item_results: Optional[List[ItemResult]]
    total_carbon_kg: Optional[float]
    record_id: Optional[int] = None