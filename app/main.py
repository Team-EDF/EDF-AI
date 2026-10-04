import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app.db_migrations import run_db_migrations
from app.db_seed import seed_reference_data

from app.api.routes import (
    challenges,
    feedback,
    merchant,
    ocr,
    onboarding,
    profile,
)

from app.services.rag_index_service import build_rag_index
from app.services.classifier import MerchantClassifier
from app.services.challenge_explainer import warm_up as warm_up_llm
from app.services.challenge_verifier import warm_up as warm_up_verifier

# ChatHistory는 앱 어디서도 import되지 않으면 Base.metadata에
# 등록되지 않으므로 create_all() 실행 전에 import해야 한다.
from app.models.chat_history import ChatHistory  # noqa: F401


# ============================================================
# DB 테이블 생성 및 마이그레이션
# ============================================================

# SQLAlchemy 모델에 정의된 테이블 중
# 아직 DB에 없는 테이블을 생성한다.
Base.metadata.create_all(bind=engine)

# 기존 테이블의 컬럼/FK/인덱스 등
# create_all()이 처리하지 못하는 스키마 변경을 자동 적용한다.
run_db_migrations(engine)


# ============================================================
# FastAPI 앱 생성
# ============================================================

app = FastAPI(
    title="GreenStep AI API",
    version="1.0.0",
)


# ============================================================
# CORS 설정
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# API Router 등록
# ============================================================

# 외부 공식 API :
# POST /api/ocr/classify(사진 업로드)
# POST /feedback/chat(채팅)
#
# merchant.router / ocr.router 안의 나머지 엔드포인트
# (/api/classify, /api/ocr/clova/classify 등)는
# 내부 테스트/디버깅 전용이며
# 백엔드/프론트가 호출할 공식 계약 대상이 아님.


# 가맹점 / 품목 분류 API
app.include_router(
    merchant.router,
    prefix="/api",
    tags=["classify"],
)


# 영수증 OCR + 분류 + 탄소 계산 + DB 저장
app.include_router(
    ocr.router,
    prefix="/api",
    tags=["ocr"],
)


# 온보딩 설문 -> 추천 목표 계산 (데모/내부 테스트 전용, 공식 계약 아님)
app.include_router(
    onboarding.router,
    prefix="/api",
    tags=["onboarding-demo"],
)


# Green Profile (설문 -> 영역별 레벨 + 실천 성향 + 추천 목표)
app.include_router(
    profile.router,
    prefix="/api",
    tags=["profile"],
)


# 챌린지 카탈로그 (5영역 x 3난이도 = 15개, BE가 챌린지 테이블 seed에 사용)
app.include_router(
    challenges.router,
    prefix="/api",
    tags=["challenges"],
)


# 소비기록 기반 RAG + Gemini 피드백 채팅
#
# feedback.py 내부 route:
#   /feedback/chat
#
# 따라서 여기서는 prefix를 추가하지 않는다.
app.include_router(
    feedback.router,
    tags=["feedback"],
)


# ============================================================
# Static UI
# ============================================================

app.mount(
    "/static",
    StaticFiles(
        directory="static",
    ),
    name="static",
)


# ============================================================
# 기본 페이지
# ============================================================

@app.get("/")
def serve_ui():
    """
    개발용 웹 UI 반환.
    """
    return FileResponse(
        "static/index.html"
    )


# ============================================================
# SBERT 모델 Warm-up
# ============================================================

@app.on_event("startup")
def warm_up_models():
    """
    1차 배포에서는 분류용 SBERT만 기동 시 검증/로딩한다.

    RAG용 HuggingFace embedding 모델까지 동시에 warm-up 하면
    CPU 전용 EKS Pod에서 두 모델이 한 번에 메모리에 올라가
    startup 시점의 메모리 peak가 커진다.

    RAG vector store는 실제 RAG 요청이 들어올 때 lazy-load 한다.

    로드한 SBERT로 DB 초기화 시 비어 있는 카테고리/임베딩 테이블도 채운다.
    """
    model = MerchantClassifier._get_model()
    seed_reference_data(model)


@app.on_event("startup")
def warm_up_challenge_llm():
    """
    챌린지 추천 설명 문구용 Gemini 클라이언트를 백그라운드에서 미리 준비한다.
    (준비에 약 9초 걸려서, 미리 해 두지 않으면 첫 추천 요청이 느리다.
     별도 스레드라 서버 기동을 늦추지 않고, 실패해도 기동에는 영향이 없다.)
    """
    threading.Thread(target=warm_up_llm, daemon=True).start()
    # 사진 인증용 Gemini 비전 클라이언트도 미리 준비한다 (첫 호출이 약 18초 걸렸다)
    threading.Thread(target=warm_up_verifier, daemon=True).start()


# ============================================================
# Health Check
# ============================================================

@app.get("/health")
def health_check():
    """
    서버 동작 여부 확인.
    """
    return {
        "status": "ok"
    }


# ============================================================
# RAG 인덱스 재생성 API
# ============================================================

@app.post(
    "/rag/index",
    tags=["rag"],
)
def rag_index():
    """
    data/rag_documents 아래의 PDF / Markdown 문서를 읽어
    Chroma RAG 인덱스를 새로 생성한다.

    주의:
    build_rag_index()는 기존 chroma_db를 삭제한 뒤
    새 인덱스를 생성한다.
    """
    return build_rag_index()