import os
import shutil
from dotenv import load_dotenv

from pypdf import PdfReader
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

DOCS_DIR = "data/rag_documents"
CHROMA_DIR = "chroma_db"


def load_markdown_documents():
    documents = []

    for root, _, files in os.walk(DOCS_DIR):
        for filename in files:
            if not filename.lower().endswith(".md"):
                continue

            file_path = os.path.join(root, filename)

            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read().strip()

            if not content:
                continue

            documents.append(
                Document(
                    page_content=content,
                    metadata={"source": file_path, "type": "markdown"}
                )
            )

    return documents


def load_pdf_documents():
    documents = []

    for root, _, files in os.walk(DOCS_DIR):
        for filename in files:
            if not filename.lower().endswith(".pdf"):
                continue

            file_path = os.path.join(root, filename)
            reader = PdfReader(file_path)

            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text()

                if not text or not text.strip():
                    continue

                documents.append(
                    Document(
                        page_content=text.strip(),
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
        chunk_size=1000,
        chunk_overlap=150
    )

    return splitter.split_documents(documents)


def build_rag_index():
    documents = load_markdown_documents() + load_pdf_documents()

    if not documents:
        return {
            "message": "RAG 인덱스 생성 실패: 문서가 없습니다.",
            "document_count": 0,
            "chunk_count": 0
        }

    chunks = split_documents(documents)

    if os.path.exists(CHROMA_DIR):
        shutil.rmtree(CHROMA_DIR)

    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001"
    )

    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR
    )

    return {
        "message": "RAG 인덱스 생성 완료",
        "document_count": len(documents),
        "chunk_count": len(chunks)
    }