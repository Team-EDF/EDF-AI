"""
챌린지 엔드포인트.

GET  /api/challenges/catalog   : 카탈로그 15개 전체. 백엔드가 챌린지 테이블을 채울 때(seed) 쓰는 원본.
POST /api/challenges/recommend : 프로필로 맞춤 챌린지 3개 + 추천 이유.
POST /api/challenges/verify    : 사진(텀블러+영수증 / 저탄소 마크+영수증)으로 챌린지 인증 판정.
"""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.schemas.challenges import CatalogResponse, RecommendRequest, RecommendResponse, VerifyResponse
from app.services.challenge_catalog import load_catalog
from app.services.challenge_service import recommend_challenges
from app.services.challenge_verifier import BadImages, VerifierUnavailable, verify_challenge

router = APIRouter()


@router.get(
    "/challenges/catalog",
    response_model=CatalogResponse,
)
def get_catalog():
    try:
        return load_catalog()
    except (ValueError, OSError) as e:
        # 카탈로그 JSON이 깨졌거나 파일이 없는 경우 — 원인을 응답에 남긴다.
        raise HTTPException(status_code=500, detail=f"카탈로그 로드 실패: {type(e).__name__}: {str(e)[:150]}")


@router.post(
    "/challenges/recommend",
    response_model=RecommendResponse,
)
def recommend(request: RecommendRequest):
    if request.profile is None:
        # user_id만으로 서버가 프로필을 다시 계산하는 기능은 실데이터 전환(2단계) 이후에 지원한다
        raise HTTPException(
            status_code=400,
            detail="profile이 필요합니다. /api/profile 응답의 areas, persona를 그대로 넘겨 주세요. (user_id만으로 추천하는 기능은 아직 지원하지 않습니다)",
        )
    try:
        return recommend_challenges(
            request.profile.model_dump(),
            exclude_ids=request.exclude_challenge_ids,
            user_name=request.user_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"챌린지 추천 실패: {type(e).__name__}: {str(e)[:150]}")


@router.post(
    "/challenges/verify",
    response_model=VerifyResponse,
)
def verify(
    kind: str = Form(..., description="인증 종류: TUMBLER | LOW_CARBON"),
    images: list[UploadFile] = File(..., description="사진 1~3장 (상품/텀블러 사진 + 영수증 사진)"),
):
    """
    사진으로 인증 여부를 판정한다. 통과/실패는 모두 200으로 돌려주고(passed, code, message),
    사진 문제는 400, AI 일시 불가는 503이다. 사진은 저장하지 않는다.
    같은 영수증의 중복 사용은 receipt.fingerprint를 BE가 DB에서 걸러낸다.
    """
    try:
        data = [upload.file.read() for upload in images]
        return verify_challenge(kind, data)
    except BadImages as e:
        raise HTTPException(status_code=400, detail=str(e))
    except VerifierUnavailable as e:
        raise HTTPException(status_code=503, detail=f"AI 인증을 잠시 쓸 수 없어요 ({e})")
