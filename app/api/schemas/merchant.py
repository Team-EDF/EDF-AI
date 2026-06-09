from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class ItemRequest(BaseModel):
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
    record_id: Optional[int] = None

    merchant_name: Optional[str] = None
    payment_location: Optional[str] = None
    payment_date: Optional[date] = None

    merchant_category: Optional[CategoryResult] = None
    merchant_carbon_kg: Optional[float] = None

    item_results: Optional[List[ItemResult]] = None
    total_carbon_kg: Optional[float] = None