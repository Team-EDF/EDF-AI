from fastapi import APIRouter, HTTPException
from app.api.schemas.merchant import (
    ClassifyRequest,
    ClassifyResponse,
    CategoryResult,
    ItemResult,
)
from app.services.classifier import MerchantClassifier

router = APIRouter()
classifier = MerchantClassifier()


@router.post("/classify", response_model=ClassifyResponse)
def classify_receipt(request: ClassifyRequest):
    """
    OCR 영수증 분류 엔드포인트.

    - items 있을 때: 품목별 classify_from_item() 실행 → item_results 반환
    - items 없을 때: 가맹점명 기반 classify_from_merchant() 실행 → merchant_category 반환
    """
    if not request.merchant_name.strip():
        raise HTTPException(status_code=400, detail="merchant_name은 비어있을 수 없습니다")

    merchant_category = None
    item_results = None

    if request.items:
        # 세부 품목 있음 → 품목별 분류
        item_results = []
        for item in request.items:
            result = classifier.classify_from_item(item.item_name)
            item_results.append(ItemResult(
                item_name=item.item_name,
                amount_krw=item.amount_krw,
                category=CategoryResult(**result) if result else None,
            ))
    else:
        # 세부 품목 없음 → 가맹점명 기반 분류
        result = classifier.classify_from_merchant(
            request.merchant_name,
            request.payment_location,
        )
        merchant_category = CategoryResult(**result) if result else None

    return ClassifyResponse(
        merchant_name=request.merchant_name,
        payment_location=request.payment_location,
        payment_date=request.payment_date,
        merchant_category=merchant_category,
        item_results=item_results,
    )
