import os
import re
import shutil

from pypdf import PdfReader
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.rag_service import (
    CHROMA_DIR,
    EMBEDDING_MODEL,
    get_embeddings,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS_DIR = os.path.join(BASE_DIR, "data", "rag_documents")

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 250
MIN_CHUNK_LENGTH = 120


def get_document_category(file_path: str) -> str:
    """data/rag_documents 하위 첫 번째 폴더명을 category로 사용한다."""
    try:
        relative_path = os.path.relpath(file_path, DOCS_DIR)
        parts = relative_path.split(os.sep)
        if len(parts) >= 2:
            return parts[0]
    except Exception:
        pass
    return "unknown"


def normalize_text(text: str) -> str:
    """문단 경계는 유지하면서 PDF/Markdown 추출 텍스트를 정리한다."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_noise_text(text: str) -> bool:
    """목차/참고문헌/부록/지나치게 짧은 텍스트 등을 제거한다."""
    text = text.strip()

    if len(text) < 100:
        return True

    normalized = text.lower()
    noise_keywords = [
        "목차",
        "표 목차",
        "그림 목차",
        "참고문헌",
        "참고 문헌",
        "부록",
        "references",
        "bibliography",
        "table of contents",
        "······",
        "-----",
    ]

    if text.count("··") >= 5:
        return True

    keyword_count = sum(
        1 for keyword in noise_keywords if keyword in normalized
    )
    return keyword_count >= 2


def detect_section_title(text: str) -> str:
    """
    문서 앞부분에서 제한적으로 섹션 제목을 탐지한다.
    오탐을 줄이기 위해 보고서에서 자주 쓰는 제목 패턴만 사용한다.
    """
    if not text:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    patterns = [
        r"^제\s*\d+\s*장\b.*",
        r"^제\s*\d+\s*절\b.*",
        r"^\d+\.\s+\S+.*",
        r"^\d+\.\d+\s+\S+.*",
        r"^[가-하]\.\s+\S+.*",
    ]

    for line in lines[:20]:
        if len(line) > 100:
            continue
        for pattern in patterns:
            if re.match(pattern, line):
                return line[:100]

    return ""


def load_markdown_documents() -> list[Document]:
    """data/rag_documents 이하의 Markdown 문서를 읽는다."""
    documents = []

    if not os.path.exists(DOCS_DIR):
        return documents

    for root, _, files in os.walk(DOCS_DIR):
        for filename in files:
            if not filename.lower().endswith(".md"):
                continue

            file_path = os.path.join(root, filename)

            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    content = normalize_text(file.read())
            except Exception as error:
                print(
                    "[RAG Index] Markdown 읽기 실패: "
                    f"{file_path} / {error}"
                )
                continue

            if not content or is_noise_text(content):
                continue

            category = get_document_category(file_path)

            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "source": file_path,
                        "filename": filename,
                        "type": "markdown",
                        "document_type": "markdown",
                        "category": category,
                        "section": detect_section_title(content),
                    },
                )
            )

    return documents


def load_pdf_documents() -> list[Document]:
    """
    data/rag_documents 이하의 PDF를 페이지 단위로 읽는다.
    페이지 번호는 chunk metadata에 그대로 전달된다.
    """
    documents = []

    if not os.path.exists(DOCS_DIR):
        return documents

    for root, _, files in os.walk(DOCS_DIR):
        for filename in files:
            if not filename.lower().endswith(".pdf"):
                continue

            file_path = os.path.join(root, filename)
            category = get_document_category(file_path)

            try:
                reader = PdfReader(file_path)
            except Exception as error:
                print(
                    "[RAG Index] PDF 읽기 실패: "
                    f"{file_path} / {error}"
                )
                continue

            for page_number, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text()
                except Exception as error:
                    print(
                        "[RAG Index] PDF 페이지 추출 실패: "
                        f"{file_path} page={page_number} / {error}"
                    )
                    continue

                text = normalize_text(text or "")

                if not text or is_noise_text(text):
                    continue

                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source": file_path,
                            "filename": filename,
                            "type": "pdf",
                            "document_type": "research_document",
                            "page": page_number,
                            "category": category,
                            "section": detect_section_title(text),
                        },
                    )
                )

    return documents


def create_text_splitter() -> RecursiveCharacterTextSplitter:
    """
    문단 -> 줄 -> 한국어 문장 -> 일반 문장 -> 공백 순서로
    의미 경계를 최대한 유지하며 분할한다.
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=[
            "\n\n",
            "\n",
            "다. ",
            "다.",
            "요. ",
            "요.",
            ". ",
            "? ",
            "! ",
            "; ",
            " ",
            "",
        ],
    )


def split_documents(documents: list[Document]) -> list[Document]:
    """
    문서를 chunk로 분리하고 metadata를 강화한다.

    - chunk_size: 1200
    - chunk_overlap: 250
    - 문단/문장 경계를 우선하여 분리
    - filename/section/chunk_index/chunk_size 저장
    - 지나치게 짧거나 noise인 최종 chunk 제거
    """
    splitter = create_text_splitter()
    split_chunks = splitter.split_documents(documents)

    chunks = []
    source_chunk_counts = {}

    for chunk in split_chunks:
        content = normalize_text(chunk.page_content)

        if not content:
            continue
        if len(content) < MIN_CHUNK_LENGTH:
            continue
        if is_noise_text(content):
            continue

        chunk.page_content = content

        source = chunk.metadata.get("source", "unknown")
        page = chunk.metadata.get("page", 0)
        counter_key = (source, page)

        chunk_index = source_chunk_counts.get(counter_key, 0)
        source_chunk_counts[counter_key] = chunk_index + 1

        chunk.metadata["chunk_index"] = chunk_index
        chunk.metadata["chunk_size"] = len(content)

        if not chunk.metadata.get("filename"):
            chunk.metadata["filename"] = os.path.basename(source)

        if not chunk.metadata.get("section"):
            chunk.metadata["section"] = detect_section_title(content)

        chunks.append(chunk)

    return chunks


def build_rag_index() -> dict:
    """
    RAG 문서를 읽고 새로운 Chroma index를 구축한다.

    주의:
    기존 chroma_db가 존재하면 삭제 후 새로 생성한다.
    """
    markdown_documents = load_markdown_documents()
    pdf_documents = load_pdf_documents()
    documents = markdown_documents + pdf_documents

    if not documents:
        return {
            "message": "RAG 인덱스 생성 실패: 문서가 없습니다.",
            "document_count": 0,
            "chunk_count": 0,
        }

    chunks = split_documents(documents)

    if not chunks:
        return {
            "message": "RAG 인덱스 생성 실패: chunk가 없습니다.",
            "document_count": len(documents),
            "chunk_count": 0,
        }

    if os.path.exists(CHROMA_DIR):
        shutil.rmtree(CHROMA_DIR)

    embeddings = get_embeddings()

    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR,
    )

    category_counts = {}

    for chunk in chunks:
        category = chunk.metadata.get("category", "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1

    print(
        "[RAG Index] 생성 완료 "
        f"documents={len(documents)}, "
        f"chunks={len(chunks)}, "
        f"categories={category_counts}, "
        f"chunk_size={CHUNK_SIZE}, "
        f"chunk_overlap={CHUNK_OVERLAP}"
    )

    return {
        "message": "RAG 인덱스 생성 완료",
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "embedding_model": EMBEDDING_MODEL,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "category_chunk_count": category_counts,
    }
