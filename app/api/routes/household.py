"""
가정 에너지(관리비) 엔드포인트.

POST /api/household/read-bill : 고지서 사진(1~3장)을 읽어 사용월과 전기·수도·가스·난방 값을 돌려준다 (사진은 저장하지 않음).
POST /api/household/carbon    : 한 달 값(사용량/금액)으로 탄소(kgCO2eq)와 항목별 내역을 계산한다.
"""
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.schemas.household import BillReadResponse, CarbonRequest, CarbonResponse
from app.services.challenge_verifier import BadImages, VerifierUnavailable
from app.services.household_bill_reader import read_bill
from app.services.household_carbon import calculate_household_carbon

router = APIRouter()


@router.post("/household/read-bill", response_model=BillReadResponse)
def read_household_bill(images: list[UploadFile] = File(..., description="고지서 사진 1~3장")):
    """
    읽기 결과는 통과/실패 모두 200(readable, code, message)이고, 사진 형식 문제는 400, AI 일시 불가는 503이다.
    읽은 값은 사용자가 확인·수정해서 저장하는 "미리 채우기" 용도다. fingerprint는 같은 고지서의 중복 사용을 막는 해시.
    """
    try:
        return read_bill([upload.file.read() for upload in images])
    except BadImages as e:
        raise HTTPException(status_code=400, detail=str(e))
    except VerifierUnavailable as e:
        raise HTTPException(status_code=503, detail=f"AI 고지서 판독을 잠시 쓸 수 없어요 ({e})")


@router.post("/household/carbon", response_model=CarbonResponse)
def household_carbon(request: CarbonRequest):
    return calculate_household_carbon(request.model_dump())
