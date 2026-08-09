from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


CHROMA_DIR = "chroma_db"

EMBEDDING_MODEL = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)


# ============================================================
# Embedding
# ============================================================

def get_embeddings():
    """
    RAG 검색용 임베딩 모델을 생성한다.
    """

    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL
    )


# ============================================================
# Vector Store
# ============================================================

def get_vector_store():
    """
    기존 Chroma 벡터스토어를 불러온다.
    """

    embeddings = get_embeddings()

    return Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings,
    )


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

    normalized = (
        text.strip()
        .lower()
    )

    # --------------------------------------------------------
    # 너무 짧은 chunk 제외
    # --------------------------------------------------------

    if len(normalized) < 80:
        return True

    # --------------------------------------------------------
    # 목차 / 참고문헌 관련 키워드
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 참고문헌 페이지에서 자주 등장하는 신호
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 명확한 목차 / 참고문헌 chunk
    # --------------------------------------------------------

    if noise_keyword_count >= 1:
        return True

    # --------------------------------------------------------
    # URL / DOI 등이 과도하게 반복되면
    # 참고문헌일 가능성이 높음
    # --------------------------------------------------------

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
    사용자 질문을 분석해
    우선 검색할 RAG category를 결정한다.

    반환:
        cafe
        food
        echo_guide
        None
    """

    normalized = query.lower()

    # ========================================================
    # Cafe Keywords
    # ========================================================

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

    # ========================================================
    # Food Keywords
    # ========================================================

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

    # ========================================================
    # Eco Guide Keywords
    # ========================================================

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

    # ========================================================
    # Category Score
    # ========================================================

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

    # ========================================================
    # 가장 높은 점수 category
    # ========================================================

    highest_category = max(
        scores,
        key=scores.get,
    )

    if scores[
        highest_category
    ] == 0:

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

        # ====================================================
        # Noise 제거
        # ====================================================

        if is_noise_chunk(
            doc.page_content
        ):
            continue

        source = (
            doc.metadata.get(
                "source"
            )
        )

        page = (
            doc.metadata.get(
                "page"
            )
        )

        # ====================================================
        # 동일 source + page 제거
        # ====================================================

        source_page_key = (
            source,
            page,
        )

        if (
            source_page_key
            in used_source_pages
        ):
            continue

        # ====================================================
        # 동일 PDF 반복 제거
        # ====================================================

        if source in used_sources:
            continue

        # ====================================================
        # 정상 문서 등록
        # ====================================================

        used_source_pages.add(
            source_page_key
        )

        used_sources.add(
            source
        )

        filtered_docs.append(
            doc
        )

        if len(
            filtered_docs
        ) >= k:

            break

    return filtered_docs


# ============================================================
# RAG Search
# ============================================================

def search_rag(
    query: str,
    k: int = 5,
):
    """
    사용자 질문과 유사한
    RAG 문서를 검색한다.

    처리 과정:

    1. 질문 category 판별
    2. 해당 category 내부에서 검색
    3. noise 제거
    4. 동일 PDF 반복 제거
    5. category 판별 실패 시에만 전체 검색
    6. 최종 결과 반환

    중요:
    category 검색 결과가 k개보다 적더라도
    다른 category의 관련 없는 문서로
    억지로 채우지 않는다.
    """

    vectorstore = (
        get_vector_store()
    )

    # ========================================================
    # Query Category
    # ========================================================

    preferred_category = (
        detect_query_category(
            query
        )
    )

    # ========================================================
    # 후보 검색 개수
    # ========================================================

    candidate_k = max(
        k * 5,
        20,
    )

    # ========================================================
    # Category가 판단된 경우
    # 해당 category에서만 검색
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

    # ========================================================
    # Category를 판단할 수 없는 질문
    # 전체 RAG 검색
    # ========================================================

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

    final_docs = (
        filter_documents(
            docs=docs,
            k=k,
        )
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

        source = (
            doc.metadata.get(
                "source"
            )
        )

        page = (
            doc.metadata.get(
                "page"
            )
        )

        doc_type = (
            doc.metadata.get(
                "type"
            )
        )

        category = (
            doc.metadata.get(
                "category"
            )
        )

        # ----------------------------------------------------
        # LLM에게 전달할 Context
        # ----------------------------------------------------

        context_parts.append(
            f"[문서 {idx}]\n"
            f"카테고리: {category}\n"
            f"출처: {source}\n"
            f"페이지: {page}\n"
            f"내용:\n"
            f"{doc.page_content}"
        )

        # ----------------------------------------------------
        # API에서 반환할 Source
        # ----------------------------------------------------

        sources.append(
            {
                "source":
                    source,

                "page":
                    page,

                "type":
                    doc_type,

                "category":
                    category,

                "preview":
                    doc.page_content[:200],
            }
        )

    # ========================================================
    # 최종 결과
    # ========================================================

    return {
        "query_category":
            preferred_category,

        "context":
            "\n\n".join(
                context_parts
            ),

        "sources":
            sources,
    }


# ============================================================
# Context Only
# ============================================================

def get_rag_context(
    query: str,
    k: int = 5,
) -> str:
    """
    다른 서비스에서 사용할 수 있도록
    RAG 검색 결과 중 context만 반환한다.
    """

    result = search_rag(
        query=query,
        k=k,
    )

    return result[
        "context"
    ]