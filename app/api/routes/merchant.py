from fastapi import APIRouter, HTTPException
from app.api.schemas.merchant import MerchantClassifyRequest, MerchantClassifyResponse
from app.services.classifier import MerchantClassifier

router = APIRouter()
classifier = MerchantClassifier()


@router.post("/classify", response_model=MerchantClassifyResponse)
def classify_merchant(request: MerchantClassifyRequest):
    if not request.merchant_name.strip():
        raise HTTPException(status_code=400, detail="merchant_name은 비어있을 수 없습니다")

    industry_name = classifier.get_industry_name(request.merchant_name)

    return MerchantClassifyResponse(
        merchant_name=request.merchant_name,
        industry_name=industry_name,
    )
