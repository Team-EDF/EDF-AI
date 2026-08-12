import logging

logger = logging.getLogger(__name__)

from typing import Optional
from datetime import date, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from app.api.schemas.merchant import (
    CategoryResult,
    ClassifyRequest,
    ClassifyResponse,
    ItemResult,
    ReceiptItem,
)
from app.database.connection import get_db
from app.services.carbon import (
    CARBON_ROUND_DIGITS,
    calculate_carbon,
)
from app.services.classifier import MerchantClassifier
from app.services.clova_ocr_service import extract_receipt_from_clova
from app.services.image_storage_service import save_receipt_image
from app.services.ocr_fallback_service import extract_receipt_with_fallback
from app.services.record_service import RecordService


router = APIRouter()

classifier = MerchantClassifier()
record_service = RecordService()


# ============================================================
# 카테고리 응답 변환
# ============================================================

def _build_category_result(
    classify_result: dict,
    conn,
) -> CategoryResult:
    """
    classifier.py의 범용 분류 결과를
    API 응답용 CategoryResult 형식으로 변환한다.

    classifier.py 반환 예시:
    {
        "category_type": "main" | "middle",
        "category_id": int | None,
        "category_name": str,
        "co2eq_KRW": float | None,
        "similarity": float,
        "classify_stage": int,
    }

    CategoryResult 형식:
    {
        "main_category_id": int | None,
        "main_name": str | None,
        "middle_category_id": int | None,
        "middle_name": str | None,
        "co2eq_KRW": float | None,
        "classify_stage": int | None,
    }
    """

    category_type = classify_result.get(
        "category_type"
    )

    category_id = classify_result.get(
        "category_id"
    )

    category_name = classify_result.get(
        "category_name"
    )

    main_category_id = None
    main_name = None

    middle_category_id = None
    middle_name = None

    # ========================================================
    # 대분류
    # ========================================================

    if category_type == "main":

        main_category_id = category_id
        main_name = category_name

    # ========================================================
    # 중분류
    # ========================================================

    elif category_type == "middle":

        middle_category_id = category_id
        middle_name = category_name

        # 중분류에 연결된 상위 대분류 정보 조회
        if middle_category_id is not None:

            cursor = conn.cursor()

            try:
                cursor.execute(
                    """
                    SELECT
                        middle.main_category_id,
                        main.main_name

                    FROM middle_category middle

                    LEFT JOIN main_category main
                      ON middle.main_category_id =
                         main.main_category_id

                    WHERE middle.middle_category_id = %s

                    LIMIT 1;
                    """,
                    (
                        middle_category_id,
                    ),
                )

                row = cursor.fetchone()

                if row:
                    main_category_id = row[0]
                    main_name = row[1]

            finally:
                cursor.close()

    # ========================================================
    # 미분류 fallback
    # ========================================================

    if (
        category_id is None
        and category_name == "미분류"
    ):
        main_name = "미분류"

    return CategoryResult(
        main_category_id=main_category_id,
        main_name=main_name,
        middle_category_id=middle_category_id,
        middle_name=middle_name,
        co2eq_KRW=classify_result.get(
            "co2eq_KRW"
        ),
        classify_stage=classify_result.get(
            "classify_stage"
        ),
    )


# ============================================================
# 결제일 정규화
# ============================================================

def _normalize_payment_date(
    value,
) -> date | None:
    """
    OCR 또는 사용자 입력으로 들어온 결제일을
    안전하게 Python date 타입으로 변환한다.

    정상 예:
        "2017-06-02"
            → date(2017, 6, 2)

    잘못된 값:
        "string"
        "unknown"
        "null"
            → None

    OCR에서 날짜를 못 읽더라도
    전체 OCR API가 500으로 실패하지 않도록 한다.
    """

    if value is None:
        return None

    # datetime은 date로 변환
    if isinstance(
        value,
        datetime,
    ):
        return value.date()

    # 이미 date이면 그대로 사용
    if isinstance(
        value,
        date,
    ):
        return value

    # 문자열 처리
    if isinstance(
        value,
        str,
    ):

        value = value.strip()

        if not value:
            return None

        # OCR / Swagger / LLM에서 들어올 수 있는
        # 잘못된 placeholder 값
        invalid_values = {
            "string",
            "none",
            "null",
            "unknown",
            "n/a",
            "na",
            "-",
        }

        if value.lower() in invalid_values:
            return None

        # 기본 YYYY-MM-DD 형식
        try:
            return date.fromisoformat(
                value[:10]
            )

        except ValueError:
            return None

    return None


# ============================================================
# OCR 파싱 결과 → ClassifyRequest
# ============================================================

def _build_classify_request(
    merchant_name: str,
    payment_location: Optional[str],
    payment_date,
    total_amount,
    parsed_items: list,
) -> ClassifyRequest:
    """
    OCR 파싱 결과를
    DB 저장용 ClassifyRequest로 변환한다.
    """

    normalized_payment_date = (
        _normalize_payment_date(
            payment_date
        )
    )

    return ClassifyRequest(
        merchant_name=merchant_name,
        payment_location=payment_location,
        payment_date=normalized_payment_date,
        total_amount_krw=(
            int(total_amount)
            if total_amount is not None
            else None
        ),
        items=(
            [
                ReceiptItem(
                    item_name=item["name"],
                    amount_krw=int(
                        item["price"]
                    ),
                )
                for item in parsed_items
            ]
            if parsed_items
            else None
        ),
    )


# ============================================================
# DB 저장
# ============================================================

def _save_classification_result(
    request: ClassifyRequest,
    response: ClassifyResponse,
    conn,
    user_id: Optional[int] = None,
) -> None:
    """
    분류 결과를 DB에 저장하고
    response.record_id를 설정한다.

    DB 저장 실패가 OCR·분류 결과 반환까지
    막지는 않도록 예외를 잡는다.
    """

    try:
        record_id = (
            record_service.save_receipt(
                request=request,
                response=response,
                user_id=user_id,
                image_url=response.image_url,
                conn=conn,
            )
        )

        response.record_id = record_id

    except Exception:
        logger.exception(
            "DB 저장 실패: merchant=%s, user_id=%s",
            request.merchant_name,
            user_id,
        )


# ============================================================
# 기본 OCR
# ============================================================

@router.post(
    "/ocr/classify",
    response_model=ClassifyResponse,
)
async def ocr_classify(
    image: UploadFile = File(
        ...,
        description="영수증 이미지 파일",
    ),
    user_id: Optional[int] = Form(
        default=None,
        description="백엔드가 JWT 인증 후 전달하는 사용자 ID",
    ),
    merchant_name_override: Optional[str] = Form(
        default=None,
        description=(
            "가맹점명 직접 입력 "
            "(OCR 추출 실패 시 사용)"
        ),
    ),
    payment_location_override: Optional[str] = Form(
        default=None,
        description="결제 위치 직접 입력",
    ),
    payment_date_override: Optional[str] = Form(
        default=None,
        description=(
            "결제 일자 직접 입력 "
            "(YYYY-MM-DD)"
        ),
    ),
    conn=Depends(
        get_db
    ),
):
    """
    영수증 이미지 처리 과정:

    1. OCR fallback
    2. 영수증 정보 파싱
    3. 품목별 카테고리 분류
    4. 품목별 탄소배출량 계산
    5. 분류 결과 DB 저장

    OCR 우선순위:
    Google Vision → Gemini Vision
    """

    # ========================================================
    # 이미지 읽기
    # ========================================================

    image_bytes = await image.read()

    if not image_bytes:

        raise HTTPException(
            status_code=400,
            detail=(
                "이미지 파일이 "
                "비어있습니다."
            ),
        )

    # ========================================================
    # 영수증 원본 이미지 저장 (실패해도 분류는 계속 진행)
    # ========================================================

    image_url = None
    try:
        image_url = save_receipt_image(image_bytes, image.filename or "receipt.jpg")
    except Exception:
        logger.exception("영수증 이미지 저장 실패 (분류는 계속 진행)")

    # ========================================================
    # OCR fallback
    # ========================================================

    try:
        parsed = (
            extract_receipt_with_fallback(
                image_bytes
            )
        )

    except Exception as error:

        if (
            "IMAGE_QUALITY_LOW"
            in str(error)
        ):

            raise HTTPException(
                status_code=422,
                detail=(
                    "영수증 이미지 품질이 낮습니다. "
                    "밝은 곳에서 영수증을 "
                    "평평하게 펴고 "
                    "다시 촬영해주세요."
                ),
            ) from error

        raise HTTPException(
            status_code=502,
            detail=(
                "OCR 처리 실패: "
                f"{error}"
            ),
        ) from error

    # ========================================================
    # OCR raw text
    # ========================================================

    raw_text = (
        parsed.get(
            "raw_text"
        )
        or ""
    )

    # ========================================================
    # OCR 결과 자체가 없는 경우
    # ========================================================

    if (
        parsed.get(
            "merchant_name"
        ) is None
        and parsed.get(
            "total_amount"
        ) is None
    ):

        raise HTTPException(
            status_code=422,
            detail=(
                "영수증 정보를 읽을 수 없습니다. "
                "밝은 곳에서 영수증을 "
                "평평하게 펴고 "
                "다시 촬영해주세요."
            ),
        )

    # ========================================================
    # 가맹점
    # ========================================================

    merchant_name = (
        parsed.get(
            "merchant_name"
        )
        or merchant_name_override
        or "미확인 가맹점"
    )

    # ========================================================
    # 결제 위치
    # ========================================================

    payment_location = (
        parsed.get(
            "payment_location"
        )
        or payment_location_override
    )

    # ========================================================
    # 결제 날짜
    # ========================================================

    raw_payment_date = (
        parsed.get(
            "payment_date"
        )
        or payment_date_override
    )

    payment_date = (
        _normalize_payment_date(
            raw_payment_date
        )
    )

    # ========================================================
    # 금액 / 품목
    # ========================================================

    total_amount = (
        parsed.get(
            "total_amount"
        )
    )

    parsed_items = (
        parsed.get(
            "items"
        )
        or []
    )

    # ========================================================
    # 초기값
    # ========================================================

    merchant_category = None
    merchant_carbon_kg = None

    item_results = None
    total_carbon_kg = None

    # ========================================================
    # 품목 존재 → 품목별 분류
    # ========================================================

    if parsed_items:

        item_results = []

        for item in parsed_items:

            item_name = (
                item.get(
                    "name"
                )
            )

            item_price = (
                item.get(
                    "price"
                )
            )

            if not item_name:
                continue

            if item_price is None:
                continue

            # ------------------------------------------------
            # 카테고리 분류
            # ------------------------------------------------

            classify_result = (
                classifier.classify_from_item(
                    item_name,
                    conn=conn,
                )
            )

            category = (
                _build_category_result(
                    classify_result=
                        classify_result,
                    conn=conn,
                )
            )

            # ------------------------------------------------
            # 탄소 계산
            # ------------------------------------------------

            carbon_kg = (
                calculate_carbon(
                    category.co2eq_KRW,
                    int(item_price),
                )
            )

            # ------------------------------------------------
            # 품목 결과
            # ------------------------------------------------

            item_results.append(
                ItemResult(
                    item_name=item_name,
                    amount_krw=int(
                        item_price
                    ),
                    category=category,
                    carbon_kg=carbon_kg,
                )
            )

        # ====================================================
        # 총 탄소량
        # ====================================================

        carbon_values = [
            result.carbon_kg
            for result in item_results
            if result.carbon_kg
            is not None
        ]

        if carbon_values:

            total_carbon_kg = round(
                sum(
                    carbon_values
                ),
                CARBON_ROUND_DIGITS,
            )

    # ========================================================
    # 품목 없음 → 가맹점 기준 분류
    # ========================================================

    else:

        classify_result = (
            classifier.classify_from_merchant(
                merchant_name=
                    merchant_name,
                payment_location=
                    payment_location,
                conn=conn,
            )
        )

        merchant_category = (
            _build_category_result(
                classify_result=
                    classify_result,
                conn=conn,
            )
        )

        merchant_carbon_kg = (
            calculate_carbon(
                merchant_category.co2eq_KRW,
                int(
                    total_amount
                    or 0
                ),
            )
        )

    # ========================================================
    # DB 저장용 Request
    # ========================================================

    request = (
        _build_classify_request(
            merchant_name=
                merchant_name,
            payment_location=
                payment_location,
            payment_date=
                payment_date,
            total_amount=
                total_amount,
            parsed_items=
                parsed_items,
        )
    )

    # ========================================================
    # API Response
    # ========================================================

    response = ClassifyResponse(
        merchant_name=
            merchant_name,

        payment_location=
            payment_location,

        payment_date=
            payment_date,

        total_amount_krw=(
            int(total_amount)
            if total_amount
            is not None
            else None
        ),

        merchant_category=
            merchant_category,

        merchant_carbon_kg=
            merchant_carbon_kg,

        item_results=
            item_results,

        total_carbon_kg=
            total_carbon_kg,

        ocr_raw_text=(
            raw_text
            or None
        ),

        ocr_engine=
            parsed.get(
                "ocr_engine"
            ),

        image_url=
            image_url,
    )

    # ========================================================
    # DB 저장
    # ========================================================

    _save_classification_result(
        request=request,
        response=response,
        conn=conn,
        user_id=user_id,
    )

    return response


# ============================================================
# Clova OCR 테스트
# ============================================================
# 테스트 전용 — api/ocr/classify(사진 업로드) + /feedback/chat(채팅) 2개로 정함.
# 이 엔드포인트는 Clova OCR 동작이 잘 되는지 개발 중 확인하기 위한 용도로만 사용.

@router.post(
    "/ocr/clova/classify",
    response_model=ClassifyResponse,
)
async def ocr_clova_classify(
    image: UploadFile = File(
        ...,
        description="영수증 이미지 파일",
    ),
    user_id: Optional[int] = Form(
        default=None,
        description="백엔드가 JWT 인증 후 전달하는 사용자 ID",
    ),
    merchant_name_override: Optional[str] = Form(
        default=None,
        description=(
            "가맹점명 직접 입력 "
            "(OCR 추출 실패 시 사용)"
        ),
    ),
    payment_location_override: Optional[str] = Form(
        default=None,
        description="결제 위치 직접 입력",
    ),
    payment_date_override: Optional[str] = Form(
        default=None,
        description=(
            "결제 일자 직접 입력 "
            "(YYYY-MM-DD)"
        ),
    ),
    conn=Depends(
        get_db
    ),
):
    """
    Naver Clova OCR 전용 테스트 라우트.

    기본 OCR 라우트와 별개로
    Clova 테스트를 위해 유지한다.
    """

    # ========================================================
    # 이미지 읽기
    # ========================================================

    image_bytes = await image.read()

    if not image_bytes:

        raise HTTPException(
            status_code=400,
            detail=(
                "이미지 파일이 "
                "비어있습니다."
            ),
        )

    # ========================================================
    # Clova OCR
    # ========================================================

    try:
        parsed = (
            extract_receipt_from_clova(
                image_bytes
            )
        )

    except Exception as error:

        raise HTTPException(
            status_code=502,
            detail=(
                "Clova OCR 처리 실패: "
                f"{error}"
            ),
        ) from error

    # ========================================================
    # OCR 결과 검사
    # ========================================================

    if (
        parsed.get(
            "merchant_name"
        ) is None
        and parsed.get(
            "total_amount"
        ) is None
    ):

        raise HTTPException(
            status_code=422,
            detail=(
                "영수증 정보를 "
                "읽을 수 없습니다."
            ),
        )

    if (
        parsed.get(
            "total_amount"
        ) is None
    ):

        raise HTTPException(
            status_code=422,
            detail=(
                "결제 금액을 "
                "인식할 수 없습니다."
            ),
        )

    # ========================================================
    # 가맹점
    # ========================================================

    merchant_name = (
        parsed.get(
            "merchant_name"
        )
        or merchant_name_override
        or "미확인 가맹점"
    )

    # ========================================================
    # 위치
    # ========================================================

    payment_location = (
        parsed.get(
            "payment_location"
        )
        or payment_location_override
    )

    # ========================================================
    # 날짜
    # ========================================================

    raw_payment_date = (
        parsed.get(
            "payment_date"
        )
        or payment_date_override
    )

    payment_date = (
        _normalize_payment_date(
            raw_payment_date
        )
    )

    # ========================================================
    # 금액 / 품목
    # ========================================================

    total_amount = (
        parsed.get(
            "total_amount"
        )
    )

    parsed_items = (
        parsed.get(
            "items"
        )
        or []
    )

    merchant_category = None
    merchant_carbon_kg = None

    item_results = None
    total_carbon_kg = None

    # ========================================================
    # 품목별 분류
    # ========================================================

    if parsed_items:

        item_results = []

        for item in parsed_items:

            item_name = (
                item.get(
                    "name"
                )
            )

            item_price = (
                item.get(
                    "price"
                )
            )

            if not item_name:
                continue

            if item_price is None:
                continue

            classify_result = (
                classifier.classify_from_item(
                    item_name,
                    conn=conn,
                )
            )

            category = (
                _build_category_result(
                    classify_result=
                        classify_result,
                    conn=conn,
                )
            )

            carbon_kg = (
                calculate_carbon(
                    category.co2eq_KRW,
                    int(item_price),
                )
            )

            item_results.append(
                ItemResult(
                    item_name=
                        item_name,

                    amount_krw=
                        int(item_price),

                    category=
                        category,

                    carbon_kg=
                        carbon_kg,
                )
            )

        carbon_values = [
            result.carbon_kg
            for result in item_results
            if result.carbon_kg
            is not None
        ]

        if carbon_values:

            total_carbon_kg = round(
                sum(
                    carbon_values
                ),
                CARBON_ROUND_DIGITS,
            )

    # ========================================================
    # 품목 없을 경우 가맹점 기준
    # ========================================================

    else:

        classify_result = (
            classifier.classify_from_merchant(
                merchant_name=
                    merchant_name,
                payment_location=
                    payment_location,
                conn=conn,
            )
        )

        merchant_category = (
            _build_category_result(
                classify_result=
                    classify_result,
                conn=conn,
            )
        )

        merchant_carbon_kg = (
            calculate_carbon(
                merchant_category.co2eq_KRW,
                int(total_amount),
            )
        )

    # ========================================================
    # DB 저장 Request
    # ========================================================

    request = (
        _build_classify_request(
            merchant_name=
                merchant_name,
            payment_location=
                payment_location,
            payment_date=
                payment_date,
            total_amount=
                total_amount,
            parsed_items=
                parsed_items,
        )
    )

    # ========================================================
    # Response
    # ========================================================

    response = ClassifyResponse(
        merchant_name=
            merchant_name,

        payment_location=
            payment_location,

        payment_date=
            payment_date,

        total_amount_krw=
            int(total_amount),

        merchant_category=
            merchant_category,

        merchant_carbon_kg=
            merchant_carbon_kg,

        item_results=
            item_results,

        total_carbon_kg=
            total_carbon_kg,

        ocr_raw_text=
            None,

        ocr_engine=
            "clova",
    )

    # ========================================================
    # DB 저장
    # ========================================================

    _save_classification_result(
        request=request,
        response=response,
        conn=conn,
        user_id=user_id,
    )

    return response