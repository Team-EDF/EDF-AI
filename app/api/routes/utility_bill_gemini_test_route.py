"""Independent FastAPI route for Gemini utility bill comparison."""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.utility_bill_gemini_service import GeminiUtilityBillService

router = APIRouter(prefix="/api/utility-bill", tags=["utility-bill-gemini-test"])
service = GeminiUtilityBillService()


@router.post("/gemini-test")
async def analyze_gemini_test(file: UploadFile = File(...)) -> dict:
    mime_type = file.content_type or ""
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="JPEG, PNG, WEBP만 지원합니다.")

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="이미지는 10MB 이하만 지원합니다.")

    try:
        return service.analyze(image_bytes, mime_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
