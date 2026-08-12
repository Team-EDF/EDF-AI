import logging
logger = logging.getLogger(__name__)
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Depends

from app.api.schemas.merchant import (
    ClassifyRequest,
    ClassifyResponse,
    CategoryResult,
    ItemResult,
)
from app.services.classifier import MerchantClassifier
from app.services.carbon import calculate_carbon, CARBON_ROUND_DIGITS
from app.services.record_service import RecordService
from app.database.connection import get_db


router = APIRouter()
classifier = MerchantClassifier()
record_service = RecordService()

# api/ocr/classify(사진 업로드) + /feedback/chat(채팅) 2개
# 이 엔드포인트는 사진 없이 텍스트(가맹점명/품목)만으로 분류 로직을 검증하기 위한 용도로 사용함
@router.post("/classify", response_model=ClassifyResponse)
def classify_receipt(
    request: ClassifyRequest,
    conn=Depends(get_db),
):
    """
    OCR 영수증 분류 + 탄소배출량 계산 엔드포인트.

    - items 있을 때: 품목별 분류 → 품목별 carbon_kg + total_carbon_kg 반환
    - items 없을 때: 가맹점명 기반 분류 → total_amount_krw 기반 merchant_carbon_kg 반환
    """
    if not request.merchant_name.strip():
        raise HTTPException(status_code=400, detail="merchant_name은 비어있을 수 없습니다")

    merchant_category = None
    merchant_carbon_kg = None
    item_results = None
    total_carbon_kg = None

    if request.items:
        item_results = []

        for item in request.items:
            result = classifier.classify_from_item(item.item_name, conn=conn)
            category = CategoryResult(**result)
            carbon_kg = calculate_carbon(category.co2eq_KRW, item.amount_krw)

            item_results.append(
                ItemResult(
                    item_name=item.item_name,
                    amount_krw=item.amount_krw,
                    category=category,
                    carbon_kg=carbon_kg,
                )
            )

        carbon_values = [
            result.carbon_kg
            for result in item_results
            if result.carbon_kg is not None
        ]

        total_carbon_kg = (
            round(sum(carbon_values), CARBON_ROUND_DIGITS)
            if carbon_values
            else None
        )

    else:
        result = classifier.classify_from_merchant(
            request.merchant_name,
            request.payment_location,
            conn=conn,
        )

        merchant_category = CategoryResult(**result)
        merchant_carbon_kg = calculate_carbon(
            merchant_category.co2eq_KRW,
            request.total_amount_krw or 0,
        )

    response = ClassifyResponse(
        merchant_name=request.merchant_name,
        payment_location=request.payment_location,
        payment_date=request.payment_date,
        merchant_category=merchant_category,
        merchant_carbon_kg=merchant_carbon_kg,
        item_results=item_results,
        total_carbon_kg=total_carbon_kg,
    )

    try:
        record_id = record_service.save_receipt(request, response, conn=conn)
        response.record_id = record_id
    except Exception:
        logger.exception("DB 저장 실패: merchant=%s", request.merchant_name)

    return response


@router.get("/stats/categories")
def get_category_stats(
    user_id: int | None = Query(default=None, description="사용자 ID (없으면 전체)"),
    period_type: str = Query(default="MONTHLY", description="DAILY | WEEKLY | MONTHLY"),
    period_start: date | None = Query(default=None, description="집계 시작일 (YYYY-MM-DD), 기본: 이번 달 1일"),
    conn=Depends(get_db),
):
    """
    카테고리별 탄소배출량 + 소비금액 집계.
    차트 시각화용 데이터 반환.
    """
    stats = record_service.get_category_stats(
        user_id,
        period_type,
        period_start,
        conn=conn,
    )

    return {
        "period_type": period_type,
        "period_start": str(period_start or date.today().replace(day=1)),
        "categories": stats,
    }
