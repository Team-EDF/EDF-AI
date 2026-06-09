import os
import shutil
from dotenv import load_dotenv

from pypdf import PdfReader
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

DOCS_DIR = "data/rag_documents"
CHROMA_DIR = "chroma_db"

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def is_noise_text(text: str) -> bool:
    text = text.strip()

    if len(text) < 100:
        return True

    noise_keywords = [
        "목차",
        "표 목차",
        "그림 목차",
        "참고문헌",
        "부록",
        "······",
        "-----"
    ]

    if text.count("··") >= 5:
        return True

    keyword_count = sum(1 for keyword in noise_keywords if keyword in text)

    if keyword_count >= 2:
        return True

    return False


def load_markdown_documents():
    documents = []

    if not os.path.exists(DOCS_DIR):
        return documents

    for root, _, files in os.walk(DOCS_DIR):
        for filename in files:
            if not filename.lower().endswith(".md"):
                continue

            file_path = os.path.join(root, filename)

            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read().strip()

            if not content:
                continue

            if is_noise_text(content):
                continue

            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "source": file_path,
                        "type": "markdown"
                    }
                )
            )

    return documents


def load_pdf_documents():
    documents = []

    if not os.path.exists(DOCS_DIR):
        return documents

    for root, _, files in os.walk(DOCS_DIR):
        for filename in files:
            if not filename.lower().endswith(".pdf"):
                continue

            file_path = os.path.join(root, filename)

            try:
                reader = PdfReader(file_path)
            except Exception as e:
                print(f"PDF 읽기 실패: {file_path} / {e}")
                continue

            for page_number, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text()
                except Exception as e:
                    print(f"PDF 페이지 추출 실패: {file_path} page {page_number} / {e}")
                    continue

                if not text or not text.strip():
                    continue

                text = text.strip()

                if is_noise_text(text):
                    continue

                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source": file_path,
                            "type": "pdf",
                            "page": page_number
                        }
                    )
                )

    return documents


def split_documents(documents):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=700,
        chunk_overlap=100
    )

    return splitter.split_documents(documents)


def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL
    )


def build_rag_index():
    documents = load_markdown_documents() + load_pdf_documents()

    if not documents:
        return {
            "message": "RAG 인덱스 생성 실패: 문서가 없습니다.",
            "document_count": 0,
            "chunk_count": 0
        }

    chunks = split_documents(documents)

    if not chunks:
        return {
            "message": "RAG 인덱스 생성 실패: chunk가 없습니다.",
            "document_count": len(documents),
            "chunk_count": 0
        }

    if os.path.exists(CHROMA_DIR):
        shutil.rmtree(CHROMA_DIR)

    embeddings = get_embeddings()

    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR
    )

    return {
        "message": "RAG 인덱스 생성 완료",
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "embedding_model": EMBEDDING_MODEL
    }