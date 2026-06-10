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


@router.post("/classify", response_model=ClassifyResponse)
def classify_receipt(
    request: ClassifyRequest,
    # DI: 요청 진입점에서 커넥션을 한 번만 열고 classifier/record_service에 전달해
    # 하나의 요청 안에서 반복되는 DB 연결 오버헤드를 제거한다
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
        # 세부 품목 있음 → 품목별 분류 + 탄소 계산 (conn 공유로 DB 연결 1회)
        item_results = []
        for item in request.items:
            result = classifier.classify_from_item(item.item_name, conn=conn)
            category = CategoryResult(**result)
            carbon_kg = calculate_carbon(category.co2eq_KRW, item.amount_krw)
            item_results.append(ItemResult(
                item_name=item.item_name,
                amount_krw=item.amount_krw,
                category=category,
                carbon_kg=carbon_kg,
            ))

        carbon_values = [r.carbon_kg for r in item_results if r.carbon_kg is not None]
        total_carbon_kg = round(sum(carbon_values), CARBON_ROUND_DIGITS) if carbon_values else None

    else:
        # 세부 품목 없음 → 가맹점명 기반 분류 + 총액 기반 탄소 계산
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
        pass  # DB 저장 실패해도 분류 결과는 반환

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
    stats = record_service.get_category_stats(user_id, period_type, period_start, conn=conn)
    return {"period_type": period_type, "period_start": str(period_start or date.today().replace(day=1)), "categories": stats}
