from datetime import date
from typing import List, Optional

from pydantic import BaseModel
<<<<<<< HEAD


class ItemRequest(BaseModel):
=======
from typing import Optional, List


# ── 요청 모델 ────────────────────────────────────────────────────────────────

class ReceiptItem(BaseModel):
    """OCR로 인식한 세부 품목 1건"""
>>>>>>> origin/dev
    item_name: str
    amount_krw: int


class ClassifyRequest(BaseModel):
    merchant_name: str
    payment_location: Optional[str] = None
    payment_date: Optional[date] = None
    total_amount_krw: Optional[int] = None
    items: Optional[List[ItemRequest]] = None


class CategoryResult(BaseModel):
    main_category_id: Optional[int] = None
    main_name: Optional[str] = None
    middle_category_id: Optional[int] = None
    middle_name: Optional[str] = None
    co2eq_KRW: Optional[float] = None
    classify_stage: Optional[str] = None


class ItemResult(BaseModel):
    item_name: str
    amount_krw: int
    category: Optional[CategoryResult] = None
    carbon_kg: Optional[float] = None


class ClassifyResponse(BaseModel):
<<<<<<< HEAD
    record_id: Optional[int] = None

    merchant_name: Optional[str] = None
    payment_location: Optional[str] = None
    payment_date: Optional[date] = None

    merchant_category: Optional[CategoryResult] = None
    merchant_carbon_kg: Optional[float] = None

    item_results: Optional[List[ItemResult]] = None
    total_carbon_kg: Optional[float] = None
=======
    """분류 응답"""
    merchant_name: str
    payment_location: Optional[str]
    payment_date: Optional[str]
    total_amount_krw: Optional[float] = None     # 총 결제금액 (KRW)
    merchant_category: Optional[CategoryResult]  # items 없을 때 가맹점 기반 분류 결과
    merchant_carbon_kg: Optional[float]          # 가맹점 기반 탄소배출량 (kgCO2eq)
    item_results: Optional[List[ItemResult]]     # items 있을 때 품목별 분류 결과
    total_carbon_kg: Optional[float]             # 품목 탄소배출량 합계 (kgCO2eq)
    record_id: Optional[int] = None              # DB 저장 후 부여되는 consumption_records PK
    ocr_raw_text: Optional[str] = None           # OCR 엔드포인트 전용: Vision API 원본 텍스트
    ocr_engine: Optional[str] = None             # OCR 엔드포인트 전용: 실제 사용된 엔진명 (google_vision/gemini_vision/gpt_vision/clova)
>>>>>>> origin/dev
