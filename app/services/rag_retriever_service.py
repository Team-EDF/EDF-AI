from app.services.rag_service import (
    search_rag,
)


def retrieve_eco_documents(
    query: str,
    k: int = 3,
) -> dict:
    """
    친환경 피드백에 사용할 RAG 문서를 검색한다.

    실제 Chroma 접근 및 embedding 처리는
    rag_service.py가 담당한다.

    반환:
    {
        "query_category": str | None,
        "context": str,
        "sources": list
    }
    """

    if not query or not query.strip():
        return {
            "query_category": None,
            "context": "",
            "sources": [],
        }

    return search_rag(
        query=query,
        k=k,
    )