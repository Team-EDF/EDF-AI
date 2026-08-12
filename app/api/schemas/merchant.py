from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class ReceiptItem(BaseModel):
    """OCR로 인식한 세부 품목 1건."""

    item_name: str
    amount_krw: int


# 기존 merchant 라우터에서 사용하는 이름과 호환
ItemRequest = ReceiptItem


class ClassifyRequest(BaseModel):
    """가맹점 또는 OCR 품목 분류 요청."""

    merchant_name: str
    payment_location: Optional[str] = None
    payment_date: Optional[date] = None
    total_amount_krw: Optional[int] = None
    items: Optional[List[ReceiptItem]] = None


class CategoryResult(BaseModel):
    """분류된 카테고리와 탄소계수 정보."""

    main_category_id: Optional[int] = None
    main_name: Optional[str] = None
    middle_category_id: Optional[int] = None
    middle_name: Optional[str] = None
    co2eq_KRW: Optional[float] = None
    classify_stage: Optional[int] = None


class ItemResult(BaseModel):
    """품목별 분류 및 탄소 계산 결과."""

    item_name: str
    amount_krw: int
    category: Optional[CategoryResult] = None
    carbon_kg: Optional[float] = None


class ClassifyResponse(BaseModel):
    """가맹점 및 OCR 품목 분류 응답."""

    record_id: Optional[int] = None

    merchant_name: Optional[str] = None
    payment_location: Optional[str] = None
    payment_date: Optional[date] = None
    total_amount_krw: Optional[int] = None

    merchant_category: Optional[CategoryResult] = None
    merchant_carbon_kg: Optional[float] = None

    item_results: Optional[List[ItemResult]] = None
    total_carbon_kg: Optional[float] = None

    ocr_raw_text: Optional[str] = None
    ocr_engine: Optional[str] = None
    image_url: Optional[str] = None  # 영수증 원본 이미지 저장 경로/URL (저장 실패 시 None)
