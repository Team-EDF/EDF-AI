import os
from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI

load_dotenv()

CHROMA_DIR = "chroma_db"


def search_rag_documents(question: str, k: int = 3):
    if not os.path.exists(CHROMA_DIR):
        return {
            "answer": "RAG 인덱스가 아직 생성되지 않았습니다. 먼저 /rag/index를 실행하세요.",
            "sources": []
        }

    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001"
    )

    db = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings
    )

    docs = db.similarity_search(question, k=k)

    if not docs:
        return {
            "answer": "관련 문서를 찾지 못했습니다.",
            "sources": []
        }

    context = "\n\n".join([
        f"[문서 {i+1}]\n{doc.page_content}"
        for i, doc in enumerate(docs)
    ])

    source_info = [
        {
            "source": doc.metadata.get("source"),
            "type": doc.metadata.get("type"),
            "page": doc.metadata.get("page")
        }
        for doc in docs
    ]

    llm = ChatGoogleGenerativeAI(
        model="gemini-1.5-flash",
        temperature=0.2
    )

    prompt = f"""
너는 탄소 발자국 소비 개선 플랫폼의 AI 피드백 assistant야.

아래 RAG 참고 문서를 바탕으로 사용자의 질문에 답변해.
반드시 문서 내용에 근거해서 답변하고, 문서에 없는 내용은 추측하지 마.

[사용자 질문]
{question}

[참고 문서]
{context}

[답변 조건]
- 한국어로 답변
- 사용자가 이해하기 쉽게 설명
- 탄소 절감, 소비 습관 개선 관점으로 정리
- 문서 근거가 부족하면 부족하다고 말하기
"""

    response = llm.invoke(prompt)

    return {
        "answer": response.content,
        "sources": source_info
    }