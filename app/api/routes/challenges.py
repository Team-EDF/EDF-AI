"""
챌린지 엔드포인트.

GET /api/challenges/catalog : 카탈로그 15개 전체. 백엔드가 챌린지 테이블을 채울 때(seed) 쓰는 원본.
(추천 엔드포인트 POST /api/challenges/recommend는 선택 규칙 구현 후 이 라우터에 추가한다.)
"""
from fastapi import APIRouter, HTTPException

from app.api.schemas.challenges import CatalogResponse
from app.services.challenge_catalog import load_catalog

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
