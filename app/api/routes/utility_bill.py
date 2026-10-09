"""
관리비 고지서 분석 API.

관리비 고지서 이미지를 업로드하면
Google Vision OCR → 관리비 파싱 → 탄소배출량 계산을 수행한다.
"""

import logging

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    UploadFile,
    status,
)

from app.api.schemas.utility_bill import (
    UtilityBillAnalyzeResponse,
)
from app.services.utility_bill_ocr_service import (
    utility_bill_ocr_service,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/utility-bill",
    tags=["Utility Bill"],
)


# 허용할 이미지 MIME 타입
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}


# 최대 업로드 크기: 10 MB
MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post(
    "/analyze",
    response_model=UtilityBillAnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="관리비 고지서 분석",
    description=(
        "관리비 고지서 이미지를 업로드하여 "
        "전기·도시가스·수도 등의 사용량을 추출하고 "
        "탄소배출량을 계산합니다."
    ),
)
async def analyze_utility_bill(
    file: UploadFile = File(...),
) -> UtilityBillAnalyzeResponse:
    """
    관리비 고지서 이미지 분석.

    처리 순서:
        1. 이미지 검증
        2. Google Vision OCR
        3. UtilityBillParser
        4. UtilityCarbonService
        5. 분석 결과 반환
    """

    # ── 파일 형식 검증 ──────────────────────────────

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "지원하지 않는 파일 형식입니다. "
                "JPEG, PNG, WEBP 이미지만 업로드할 수 있습니다."
            ),
        )

    # ── 이미지 읽기 ─────────────────────────────────

    try:
        image_bytes = await file.read()

    except Exception as error:
        logger.exception(
            "[관리비 API] 이미지 읽기 실패"
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="업로드한 이미지를 읽을 수 없습니다.",
        ) from error

    # ── 빈 파일 검증 ────────────────────────────────

    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="업로드한 이미지가 비어 있습니다.",
        )

    # ── 파일 크기 검증 ──────────────────────────────

    if len(image_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="이미지 크기는 최대 10MB까지 허용됩니다.",
        )

    # ── OCR + Parser + Carbon 계산 ──────────────────

    try:
        result = utility_bill_ocr_service.extract(
            image_bytes
        )

    except Exception as error:
        logger.exception(
            "[관리비 API] 관리비 분석 중 예외 발생"
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="관리비 고지서 분석 중 오류가 발생했습니다.",
        ) from error

    # ── OCR 실패 ────────────────────────────────────

    if result.get("status") != "success":
        logger.warning(
            "[관리비 API] OCR 분석 실패: %s",
            result.get("warnings"),
        )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "관리비 고지서를 분석하지 못했습니다.",
                "ocr_engine": result.get(
                    "ocr_engine"
                ),
                "warnings": result.get(
                    "warnings",
                    [],
                ),
            },
        )

    # ── 정상 응답 ───────────────────────────────────

    return UtilityBillAnalyzeResponse(
        **result
    )