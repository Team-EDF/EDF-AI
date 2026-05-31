import os
from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings

load_dotenv()

DOCS_DIR = "data/rag_documents"
CHROMA_DIR = "chroma_db"


def load_markdown_documents():
    documents = []

    for filename in os.listdir(DOCS_DIR):
        if not filename.endswith(".md"):
            continue

        file_path = os.path.join(DOCS_DIR, filename)

        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()

        documents.append(
            Document(
                page_content=content,
                metadata={"source": filename}
            )
        )

    return documents


def build_rag_index():
    documents = load_markdown_documents()

    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/text-embedding-004"
    )

    Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory=CHROMA_DIR
    )

    return {
        "message": "RAG 인덱스 생성 완료",
        "document_count": len(documents)
    }