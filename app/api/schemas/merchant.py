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
    payment_location: Optional[str] = None    # 결제 위치 (도로명주소)
    payment_date: Optional[str] = None         # 결제 일자 (YYYY-MM-DD)
    total_amount_krw: Optional[float] = None   # 총 결제금액 (items 없을 때 탄소계산용)
    items: Optional[List[ReceiptItem]] = None  # 세부 품목 및 금액 목록


# ── 응답 모델 ────────────────────────────────────────────────────────────────

class CategoryResult(BaseModel):
    """카테고리 분류 결과 단위"""
    category_type: str              # "main" | "middle"
    # Optional로 변경: 모든 분류 단계 실패 시 Safe-Default '미분류' 반환을 지원하기 위함
    category_id: Optional[int]
    category_name: str
    co2eq_KRW: Optional[float]      # kgCO2eq / KRW (탄소계수)
    similarity: float               # 유사도 (강제매핑: 1.0 / SBERT: 0.5~1.0 / 미분류: 0.0)
    classify_stage: int             # 분류 단계 번호 (4 = Safe-Default 미분류)


class ItemResult(BaseModel):
    """세부 품목 1건의 분류 결과"""
    item_name: str
    amount_krw: float
    category: Optional[CategoryResult]
    carbon_kg: Optional[float]           # kgCO2eq (분류 실패 시 None)


class ClassifyResponse(BaseModel):
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
    image_url: Optional[str] = None              # 영수증 원본 이미지 저장 경로/URL (저장 실패 시 None)