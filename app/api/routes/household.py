"""가정 에너지(관리비) API: 고지서 판독, 탄소 계산, 통합 분석."""

import logging
import os

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.schemas.household import (
    BillReadResponse,
    CarbonRequest,
    CarbonResponse,
    HouseholdAnalyzeResponse,
)
from app.services.challenge_verifier import BadImages, VerifierUnavailable
from app.services.household_bill_reader import read_bill
from app.services.household_carbon import calculate_household_carbon

logger = logging.getLogger(__name__)
router = APIRouter()


def _read_uploaded_bill(
    images: list[UploadFile],
    enforce_month_range: bool = True,
) -> dict:
    """업로드된 이미지를 Gemini 판독 서비스로 전달한다."""
    try:
        return read_bill(
            [upload.file.read() for upload in images],
            enforce_month_range=enforce_month_range,
        )
    except BadImages as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VerifierUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"AI 고지서 판독을 잠시 쓸 수 없어요 ({exc})",
        ) from exc


@router.post("/household/read-bill", response_model=BillReadResponse)
def read_household_bill(
    images: list[UploadFile] = File(..., description="고지서 사진 1~3장"),
):
    """운영 환경에서는 최근 13개월 제한, 로컬 테스트에서는 제한 해제."""
    allow_old_bills = os.getenv("HOUSEHOLD_ALLOW_OLD_BILLS", "false").lower() == "true"

    return _read_uploaded_bill(
        images,
        enforce_month_range=not allow_old_bills,
    )


@router.post("/household/carbon", response_model=CarbonResponse)
def household_carbon(request: CarbonRequest):
    """입력된 사용량 또는 금액으로 가정 에너지 탄소배출량을 계산한다."""
    return calculate_household_carbon(request.model_dump())


@router.post("/household/analyze", response_model=HouseholdAnalyzeResponse)
def analyze_household_bill(
    file: UploadFile = File(..., description="관리비 고지서 이미지 1장"),
):
    """과거 고지서도 Gemini로 판독하고 탄소배출량을 계산한다."""
    reading = BillReadResponse.model_validate(
        _read_uploaded_bill([file], enforce_month_range=False)
    )

    if not reading.readable or reading.code != "OK" or reading.values is None:
        return HouseholdAnalyzeResponse(bill=reading, carbon=None)

    carbon = calculate_household_carbon(reading.values.model_dump())
    return HouseholdAnalyzeResponse(
        bill=reading,
        carbon=CarbonResponse.model_validate(carbon),
    )
