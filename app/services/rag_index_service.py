import os
import shutil

from pypdf import PdfReader

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
)

from app.services.rag_service import (
    CHROMA_DIR,
    EMBEDDING_MODEL,
    get_embeddings,
)


BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

DOCS_DIR = os.path.join(
    BASE_DIR,
    "data",
    "rag_documents",
)


def get_document_category(
    file_path: str,
) -> str:
    """
    data/rag_documents 하위의
    첫 번째 폴더명을 category로 사용한다.

    예:
    data/rag_documents/cafe/...
        -> cafe

    data/rag_documents/food/...
        -> food

    data/rag_documents/echo_guide/...
        -> echo_guide
    """

    try:
        relative_path = os.path.relpath(
            file_path,
            DOCS_DIR,
        )

        parts = relative_path.split(
            os.sep
        )

        if len(parts) >= 2:
            return parts[0]

    except Exception:
        pass

    return "unknown"


def is_noise_text(
    text: str,
) -> bool:
    """
    RAG 검색 품질을 낮추는
    목차 / 참고문헌 / 부록 / 지나치게 짧은
    텍스트 등을 제거한다.
    """

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
        1
        for keyword in noise_keywords
        if keyword in normalized
    )

    if keyword_count >= 2:
        return True

    return False


def load_markdown_documents() -> list[Document]:
    """
    data/rag_documents 이하의
    Markdown 문서를 읽는다.
    """

    documents = []

    if not os.path.exists(
        DOCS_DIR
    ):
        return documents

    for root, _, files in os.walk(
        DOCS_DIR
    ):
        for filename in files:

            if not filename.lower().endswith(
                ".md"
            ):
                continue

            file_path = os.path.join(
                root,
                filename,
            )

            try:
                with open(
                    file_path,
                    "r",
                    encoding="utf-8",
                ) as file:

                    content = (
                        file.read()
                        .strip()
                    )

            except Exception as error:

                print(
                    "[RAG Index] "
                    "Markdown 읽기 실패: "
                    f"{file_path} / "
                    f"{error}"
                )

                continue

            if not content:
                continue

            if is_noise_text(
                content
            ):
                continue

            category = (
                get_document_category(
                    file_path
                )
            )

            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "source":
                            file_path,

                        "type":
                            "markdown",

                        "category":
                            category,
                    },
                )
            )

    return documents


def load_pdf_documents() -> list[Document]:
    """
    data/rag_documents 이하의 PDF 문서를
    페이지 단위로 읽는다.
    """

    documents = []

    if not os.path.exists(
        DOCS_DIR
    ):
        return documents

    for root, _, files in os.walk(
        DOCS_DIR
    ):
        for filename in files:

            if not filename.lower().endswith(
                ".pdf"
            ):
                continue

            file_path = os.path.join(
                root,
                filename,
            )

            category = (
                get_document_category(
                    file_path
                )
            )

            try:
                reader = PdfReader(
                    file_path
                )

            except Exception as error:

                print(
                    "[RAG Index] "
                    "PDF 읽기 실패: "
                    f"{file_path} / "
                    f"{error}"
                )

                continue

            for page_number, page in enumerate(
                reader.pages,
                start=1,
            ):

                try:
                    text = (
                        page.extract_text()
                    )

                except Exception as error:

                    print(
                        "[RAG Index] "
                        "PDF 페이지 추출 실패: "
                        f"{file_path} "
                        f"page={page_number} / "
                        f"{error}"
                    )

                    continue

                if not text:
                    continue

                text = text.strip()

                if not text:
                    continue

                if is_noise_text(
                    text
                ):
                    continue

                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source":
                                file_path,

                            "type":
                                "pdf",

                            "page":
                                page_number,

                            "category":
                                category,
                        },
                    )
                )

    return documents


def split_documents(
    documents: list[Document],
) -> list[Document]:
    """
    검색에 적합하도록 문서를
    chunk 단위로 분리한다.

    LangChain splitter는
    기존 metadata도 각 chunk에 그대로 전달한다.
    """

    splitter = (
        RecursiveCharacterTextSplitter(
            chunk_size=700,
            chunk_overlap=100,
        )
    )

    return splitter.split_documents(
        documents
    )


def build_rag_index() -> dict:
    """
    RAG 문서를 읽고
    새로운 Chroma index를 구축한다.

    주의:
    기존 chroma_db가 존재하면
    삭제 후 새로 생성한다.
    """

    # ============================================================
    # 문서 로딩
    # ============================================================

    markdown_documents = (
        load_markdown_documents()
    )

    pdf_documents = (
        load_pdf_documents()
    )

    documents = (
        markdown_documents
        + pdf_documents
    )

    if not documents:

        return {
            "message": (
                "RAG 인덱스 생성 실패: "
                "문서가 없습니다."
            ),

            "document_count": 0,

            "chunk_count": 0,
        }

    # ============================================================
    # Chunk 분할
    # ============================================================

    chunks = split_documents(
        documents
    )

    if not chunks:

        return {
            "message": (
                "RAG 인덱스 생성 실패: "
                "chunk가 없습니다."
            ),

            "document_count":
                len(documents),

            "chunk_count":
                0,
        }

    # ============================================================
    # 기존 Chroma index 삭제
    # ============================================================

    if os.path.exists(
        CHROMA_DIR
    ):
        shutil.rmtree(
            CHROMA_DIR
        )

    # ============================================================
    # Embedding 모델
    # ============================================================

    embeddings = (
        get_embeddings()
    )

    # ============================================================
    # Chroma index 생성
    # ============================================================

    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR,
    )

    # ============================================================
    # Category별 통계 확인
    # ============================================================

    category_counts = {}

    for chunk in chunks:

        category = (
            chunk.metadata.get(
                "category",
                "unknown",
            )
        )

        category_counts[
            category
        ] = (
            category_counts.get(
                category,
                0,
            )
            + 1
        )

    print(
        "[RAG Index] 생성 완료 "
        f"documents={len(documents)}, "
        f"chunks={len(chunks)}, "
        f"categories={category_counts}"
    )

    # ============================================================
    # 결과 반환
    # ============================================================

    return {
        "message":
            "RAG 인덱스 생성 완료",

        "document_count":
            len(documents),

        "chunk_count":
            len(chunks),

        "embedding_model":
            EMBEDDING_MODEL,

        "category_chunk_count":
            category_counts,
    }