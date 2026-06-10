from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from typing import Optional
from app.api.schemas.merchant import (
    ClassifyResponse,
    CategoryResult,
    ItemResult,
    ReceiptItem,
    ClassifyRequest,
)
from app.services.ocr_fallback_service import extract_receipt_with_fallback
from app.services.clova_ocr_service import extract_receipt_from_clova
from app.services.classifier import MerchantClassifier
from app.services.carbon import calculate_carbon, CARBON_ROUND_DIGITS
from app.services.record_service import RecordService
from app.database.connection import get_db

router = APIRouter()
classifier = MerchantClassifier()
record_service = RecordService()


@router.post("/ocr/classify", response_model=ClassifyResponse)
async def ocr_classify(
    image: UploadFile = File(..., description="영수증 이미지 파일"),
    # 아래 3개는 OCR 추출값이 없을 때 쓰는 override 필드 (선택)
    merchant_name_override: Optional[str] = Form(default=None, description="가맹점명 직접 입력 (OCR 추출 실패 시 사용)"),
    payment_location_override: Optional[str] = Form(default=None, description="결제 위치 직접 입력"),
    payment_date_override: Optional[str] = Form(default=None, description="결제 일자 직접 입력 (YYYY-MM-DD)"),
    conn=Depends(get_db),
):
    """
    영수증 이미지 → OCR 다단계 fallback → 파싱 → 품목별 분류 + 탄소배출량 계산.

    OCR 엔진 우선순위: Google Vision → Gemini Vision → GPT-4o Vision → Naver Clova
    가맹점명/위치/날짜는 영수증에서 자동 추출.
    추출 실패 시 override 파라미터 사용, 둘 다 없으면 '미확인 가맹점' fallback.
    """
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="이미지 파일이 비어있습니다")

    try:
        parsed = extract_receipt_with_fallback(image_bytes)
    except Exception as e:
        if "IMAGE_QUALITY_LOW" in str(e):
            raise HTTPException(
                status_code=422,
                detail="영수증 이미지 품질이 낮습니다. 밝은 곳에서 평평하게 펴고 다시 촬영해주세요."
            )
        raise HTTPException(status_code=502, detail=f"OCR 처리 실패: {str(e)}")

    raw_text = parsed.get("raw_text") or ""

    # 최종 품질 검증 (모든 OCR 엔진 실패한 경우)
    if parsed["merchant_name"] is None and parsed["total_amount"] is None:
        raise HTTPException(
            status_code=422,
            detail="영수증 정보를 읽을 수 없습니다. 밝은 곳에서 영수증을 평평하게 펴고 다시 촬영해주세요."
        )

    # OCR 추출값 우선, 없으면 override, 없으면 기본값
    merchant_name = parsed["merchant_name"] or merchant_name_override or "미확인 가맹점"
    payment_location = parsed["payment_location"] or payment_location_override
    payment_date = parsed["payment_date"] or payment_date_override
    total_amount = parsed["total_amount"]
    parsed_items = parsed["items"]

    merchant_category = None
    merchant_carbon_kg = None
    item_results = None
    total_carbon_kg = None

    if parsed_items:
        item_results = []
        for item in parsed_items:
            result = classifier.classify_from_item(item["name"], conn=conn)
            category = CategoryResult(**result)
            carbon_kg = calculate_carbon(category.co2eq_KRW, item["price"])
            item_results.append(ItemResult(
                item_name=item["name"],
                amount_krw=float(item["price"]),
                category=category,
                carbon_kg=carbon_kg,
            ))

        carbon_values = [r.carbon_kg for r in item_results if r.carbon_kg is not None]
        total_carbon_kg = round(sum(carbon_values), CARBON_ROUND_DIGITS) if carbon_values else None

    else:
        # 품목 추출 실패 → 가맹점명 기반 분류 fallback
        result = classifier.classify_from_merchant(merchant_name, payment_location, conn=conn)
        merchant_category = CategoryResult(**result)
        merchant_carbon_kg = calculate_carbon(
            merchant_category.co2eq_KRW,
            total_amount or 0,
        )

    request = ClassifyRequest(
        merchant_name=merchant_name,
        payment_location=payment_location,
        payment_date=payment_date,
        total_amount_krw=float(total_amount) if total_amount else None,
        items=[ReceiptItem(item_name=i["name"], amount_krw=float(i["price"])) for i in parsed_items] if parsed_items else None,
    )

    response = ClassifyResponse(
        merchant_name=merchant_name,
        payment_location=payment_location,
        payment_date=payment_date,
        total_amount_krw=float(total_amount) if total_amount else None,
        merchant_category=merchant_category,
        merchant_carbon_kg=merchant_carbon_kg,
        item_results=item_results,
        total_carbon_kg=total_carbon_kg,
        ocr_raw_text=raw_text or None,
        ocr_engine=parsed.get("ocr_engine"),
    )

    try:
        record_id = record_service.save_receipt(request, response, conn=conn)
        response.record_id = record_id
    except Exception:
        pass

    return response

@router.post("/ocr/clova/classify", response_model=ClassifyResponse)
async def ocr_clova_classify(
    image: UploadFile = File(..., description="영수증 이미지 파일"),
    merchant_name_override: Optional[str] = Form(default=None, description="가맹점명 직접 입력 (OCR 추출 실패 시 사용)"),
    payment_location_override: Optional[str] = Form(default=None, description="결제 위치 직접 입력"),
    payment_date_override: Optional[str] = Form(default=None, description="결제 일자 직접 입력 (YYYY-MM-DD)"),
    conn=Depends(get_db),
):
    """
    Naver Clova OCR로 영수증 분석.
    Google Vision + 커스텀 파서 대신 Clova 영수증 도메인 모델 사용.
    """
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="이미지 파일이 비어있습니다")

    try:
        parsed = extract_receipt_from_clova(image_bytes)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Clova OCR 처리 실패: {str(e)}")

    if parsed["merchant_name"] is None and parsed["total_amount"] is None:
        raise HTTPException(status_code=422, detail="영수증 정보를 읽을 수 없습니다.")

    if parsed["total_amount"] is None:
        raise HTTPException(status_code=422, detail="결제 금액을 인식할 수 없습니다.")

    merchant_name = parsed["merchant_name"] or merchant_name_override or "미확인 가맹점"
    payment_location = parsed["payment_location"] or payment_location_override
    payment_date = parsed["payment_date"] or payment_date_override
    total_amount = parsed["total_amount"]
    parsed_items = parsed["items"]

    merchant_category = None
    merchant_carbon_kg = None
    item_results = None
    total_carbon_kg = None

    if parsed_items:
        item_results = []
        for item in parsed_items:
            result = classifier.classify_from_item(item["name"], conn=conn)
            category = CategoryResult(**result)
            carbon_kg = calculate_carbon(category.co2eq_KRW, item["price"])
            item_results.append(ItemResult(
                item_name=item["name"],
                amount_krw=float(item["price"]),
                category=category,
                carbon_kg=carbon_kg,
            ))
        carbon_values = [r.carbon_kg for r in item_results if r.carbon_kg is not None]
        total_carbon_kg = round(sum(carbon_values), CARBON_ROUND_DIGITS) if carbon_values else None
    else:
        result = classifier.classify_from_merchant(merchant_name, payment_location, conn=conn)
        merchant_category = CategoryResult(**result)
        merchant_carbon_kg = calculate_carbon(
            merchant_category.co2eq_KRW,
            total_amount or 0,
        )

    request = ClassifyRequest(
        merchant_name=merchant_name,
        payment_location=payment_location,
        payment_date=payment_date,
        total_amount_krw=float(total_amount) if total_amount else None,
        items=[ReceiptItem(item_name=i["name"], amount_krw=float(i["price"])) for i in parsed_items] if parsed_items else None,
    )

    response = ClassifyResponse(
        merchant_name=merchant_name,
        payment_location=payment_location,
        payment_date=payment_date,
        total_amount_krw=float(total_amount) if total_amount else None,
        merchant_category=merchant_category,
        merchant_carbon_kg=merchant_carbon_kg,
        item_results=item_results,
        total_carbon_kg=total_carbon_kg,
        ocr_raw_text=None,
    )

    try:
        record_id = record_service.save_receipt(request, response, conn=conn)
        response.record_id = record_id
    except Exception:
        pass

    return response
