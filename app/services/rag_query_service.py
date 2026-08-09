import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.services.rag_retriever_service import (
    retrieve_eco_documents,
)


load_dotenv()


GEMINI_MODEL = "gemini-2.5-flash"

_PROJECT_ID = os.getenv(
    "GEMINI_PROJECT_ID"
)

_LOCATION = os.getenv(
    "GEMINI_LOCATION",
    "us-central1",
)


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """
    Vertex AI Gemini client 생성.
    """

    global _client

    if _client is None:
        if not _PROJECT_ID:
            raise RuntimeError(
                "GEMINI_PROJECT_ID "
                "환경변수가 설정되지 않았습니다."
            )

        _client = genai.Client(
            vertexai=True,
            project=_PROJECT_ID,
            location=_LOCATION,
        )

    return _client


def search_rag_documents(
    question: str,
    k: int = 3,
) -> dict:
    """
    RAG 문서만을 이용한 일반 Q&A.

    이 함수는 소비기록 기반 feedback이 아니라
    RAG 자체 테스트 및 문서 질문용이다.
    """

    if not question or not question.strip():
        return {
            "answer": "질문을 입력해주세요.",
            "sources": [],
        }

    # ---------------------------------------------------------
    # 1. 관련 문서 검색
    # ---------------------------------------------------------

    try:
        rag_result = retrieve_eco_documents(
            query=question,
            k=k,
        )

    except FileNotFoundError:
        return {
            "answer": (
                "RAG 인덱스가 아직 "
                "생성되지 않았습니다."
            ),
            "sources": [],
        }

    context = rag_result.get(
        "context",
        "",
    )

    sources = rag_result.get(
        "sources",
        [],
    )

    if not context:
        return {
            "answer": (
                "관련 문서를 찾지 못했습니다."
            ),
            "sources": [],
        }

    # ---------------------------------------------------------
    # 2. Gemini용 prompt
    # ---------------------------------------------------------

    prompt = f"""
너는 소비 기반 탄소 발자국 플랫폼의
친환경 정보 제공 AI다.

아래 RAG 참고 문서만을 근거로
사용자의 질문에 답변한다.


[사용자 질문]

{question}


[RAG 참고자료]

{context}


[답변 규칙]

- 반드시 한국어로 답변한다.
- 제공된 참고자료에 근거해서 설명한다.
- 문서에 없는 구체적인 수치나 사실을
  임의로 만들어내지 않는다.
- 소비 습관 개선과 탄소 절감 관점에서 설명한다.
- 근거가 부족하면 근거가 부족하다고 명시한다.
- 사용자가 이해하기 쉽게 설명한다.
- 필요하면 실천 가능한 행동을 제안한다.
- 지나치게 긴 답변은 작성하지 않는다.
""".strip()

    # ---------------------------------------------------------
    # 3. Gemini 답변 생성
    # ---------------------------------------------------------

    response = (
        _get_client()
        .models
        .generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=700,
            ),
        )
    )

    if not response.text:
        return {
            "answer": (
                "답변을 생성하지 못했습니다."
            ),
            "sources": sources,
        }

    return {
        "answer": response.text.strip(),
        "sources": sources,
    }