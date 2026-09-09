from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


CHROMA_DIR = "chroma_db"

EMBEDDING_MODEL = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)

VALID_CATEGORIES = {
    "cafe",
    "food",
    "echo_guide",
}


# ============================================================
# Embedding
# ============================================================

_embeddings: HuggingFaceEmbeddings | None = None


def get_embeddings():
    """
    RAG 검색용 임베딩 모델을 최초 한 번만 로드하고 재사용한다.

    매 요청마다 새로 생성하면 HuggingFace Hub 접근이
    요청마다 반복되어 지연/실패(타임아웃)의 원인이 된다.
    """

    global _embeddings

    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL
        )

    return _embeddings


# ============================================================
# Vector Store
# ============================================================

_vector_store: Chroma | None = None


def get_vector_store():
    """
    기존 Chroma 벡터스토어를 최초 한 번만 불러오고 재사용한다.
    """

    global _vector_store

    if _vector_store is None:
        _vector_store = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=get_embeddings(),
        )

    return _vector_store


# ============================================================
# Noise Chunk 판별
# ============================================================

def is_noise_chunk(
    text: str,
) -> bool:
    """
    검색된 chunk가 목차, 참고문헌 등
    실제 친환경 피드백 근거로 사용하기 어려운
    noise인지 판별한다.
    """

    if not text:
        return True

    normalized = text.strip().lower()

    if len(normalized) < 80:
        return True

    noise_keywords = [
        "references",
        "bibliography",
        "reference list",
        "참고문헌",
        "참고 문헌",
        "목차",
        "table of contents",
        "그림 목차",
        "표 목차",
        "부록",
    ]

    reference_signals = [
        "doi.org",
        "http://",
        "https://",
        "et al.",
    ]

    noise_keyword_count = sum(
        1
        for keyword in noise_keywords
        if keyword in normalized
    )

    reference_signal_count = sum(
        normalized.count(signal)
        for signal in reference_signals
    )

    if noise_keyword_count >= 1:
        return True

    if reference_signal_count >= 5:
        return True

    return False


# ============================================================
# Query Category 판별
# ============================================================

def detect_query_category(
    query: str,
) -> str | None:
    """
    query의 키워드를 분석해
    우선 검색할 RAG category를 결정한다.
    """

    normalized = query.lower()

    cafe_keywords = [
        "카페",
        "커피",
        "텀블러",
        "다회용컵",
        "다회용 컵",
        "일회용컵",
        "일회용 컵",
        "개인컵",
        "개인 컵",
        "테이크아웃",
        "커피전문점",
        "커피 전문점",
        "에스프레소",
        "라떼",
        "카푸치노",
        "coffee",
        "cafe",
        "tumbler",
        "reusable cup",
        "single-use cup",
        "single use cup",
        "takeaway",
        "espresso",
        "latte",
        "cappuccino",
    ]

    food_keywords = [
        "식품",
        "음식",
        "식단",
        "식생활",
        "식재료",
        "육류",
        "고기",
        "소고기",
        "쇠고기",
        "돼지고기",
        "닭고기",
        "햄",
        "소시지",
        "가공육",
        "축산물",
        "농축산물",
        "채식",
        "비건",
        "콩",
        "콩류",
        "두부",
        "단백질",
        "식물성",
        "대체육",
        "우유",
        "유제품",
        "식물성 우유",
        "음식물 쓰레기",
        "음식물쓰레기",
        "식품 폐기",
        "식품폐기",
        "배달음식",
        "배달 음식",
        "food",
        "diet",
        "meat",
        "beef",
        "pork",
        "chicken",
        "vegetarian",
        "vegan",
        "plant-based",
        "plant based",
        "protein",
        "dairy",
        "milk",
        "food waste",
    ]

    eco_keywords = [
        "일상생활",
        "일상 생활",
        "생활습관",
        "생활 습관",
        "친환경 행동",
        "친환경 생활",
        "탄소중립",
        "탄소 절감 행동",
        "탄소감축 행동",
        "탄소 감축 행동",
        "소비 습관",
        "소비습관",
        "에너지",
        "전기",
        "난방",
        "냉방",
        "자동차",
        "대중교통",
        "교통",
        "자전거",
        "도보",
        "재활용",
        "재사용",
        "중고",
        "절약",
        "lifestyle",
        "household",
        "transport",
        "energy",
        "recycling",
        "reuse",
        "carbon reduction",
    ]

    scores = {
        "cafe": 0,
        "food": 0,
        "echo_guide": 0,
    }

    for keyword in cafe_keywords:
        if keyword in normalized:
            scores["cafe"] += 1

    for keyword in food_keywords:
        if keyword in normalized:
            scores["food"] += 1

    for keyword in eco_keywords:
        if keyword in normalized:
            scores["echo_guide"] += 1

    highest_category = max(
        scores,
        key=scores.get,
    )

    if scores[highest_category] == 0:
        return None

    return highest_category


# ============================================================
# 검색 결과 필터링
# ============================================================

def filter_documents(
    docs,
    k: int,
):
    """
    검색된 문서 후보에서

    1. noise 제거
    2. 동일 source + page 제거
    3. 동일 PDF 반복 노출 방지

    후 최종 문서를 반환한다.
    """

    filtered_docs = []

    used_sources = set()
    used_source_pages = set()

    for doc in docs:

        if is_noise_chunk(
            doc.page_content
        ):
            continue

        source = doc.metadata.get(
            "source"
        )

        page = doc.metadata.get(
            "page"
        )

        source_page_key = (
            source,
            page,
        )

        if source_page_key in used_source_pages:
            continue

        if source in used_sources:
            continue

        used_source_pages.add(
            source_page_key
        )

        used_sources.add(
            source
        )

        filtered_docs.append(
            doc
        )

        if len(filtered_docs) >= k:
            break

    return filtered_docs


# ============================================================
# RAG Search
# ============================================================

def search_rag(
    query: str,
    k: int = 5,
    preferred_category: str | None = None,
):
    """
    사용자 질문과 유사한 RAG 문서를 검색한다.

    preferred_category가 전달되면:
        해당 category를 최우선으로 사용한다.

    preferred_category가 없으면:
        기존 키워드 기반 category 판별을 사용한다.

    category가 결정되면:
        해당 category 내부에서만 검색한다.
    """

    vectorstore = get_vector_store()

    # ========================================================
    # Category 결정
    # ========================================================

    if preferred_category not in VALID_CATEGORIES:
        preferred_category = None

    if preferred_category is None:
        preferred_category = detect_query_category(
            query
        )

    print(
        "[RAG] "
        f"preferred_category={preferred_category}"
    )

    # ========================================================
    # 후보 검색 개수
    # ========================================================

    candidate_k = max(
        k * 5,
        20,
    )

    # ========================================================
    # Category 검색
    # ========================================================

    if preferred_category:
        docs = (
            vectorstore
            .similarity_search(
                query,
                k=candidate_k,
                filter={
                    "category":
                        preferred_category
                },
            )
        )

    else:
        docs = (
            vectorstore
            .similarity_search(
                query,
                k=candidate_k,
            )
        )

    # ========================================================
    # Noise + Duplicate 제거
    # ========================================================

    final_docs = filter_documents(
        docs=docs,
        k=k,
    )

    # ========================================================
    # Context 생성
    # ========================================================

    context_parts = []
    sources = []

    for idx, doc in enumerate(
        final_docs,
        start=1,
    ):
        source = doc.metadata.get(
            "source"
        )

        page = doc.metadata.get(
            "page"
        )

        doc_type = doc.metadata.get(
            "type"
        )

        category = doc.metadata.get(
            "category"
        )

        context_parts.append(
            f"[문서 {idx}]\n"
            f"카테고리: {category}\n"
            f"출처: {source}\n"
            f"페이지: {page}\n"
            f"내용:\n"
            f"{doc.page_content}"
        )

        sources.append(
            {
                "source": source,
                "page": page,
                "type": doc_type,
                "category": category,
                "preview":
                    doc.page_content[:200],
            }
        )

    return {
        "query_category":
            preferred_category,
        "context":
            "\n\n".join(context_parts),
        "sources":
            sources,
    }


# ============================================================
# Context Only
# ============================================================

def get_rag_context(
    query: str,
    k: int = 5,
    preferred_category: str | None = None,
) -> str:
    """
    다른 서비스에서 사용할 수 있도록
    RAG 검색 결과 중 context만 반환한다.
    """

    result = search_rag(
        query=query,
        k=k,
        preferred_category=preferred_category,
    )

    return result["context"]