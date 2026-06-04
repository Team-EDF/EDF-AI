from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings

load_dotenv()

CHROMA_DIR = "chroma_db"


def retrieve_eco_documents(query: str, k: int = 3) -> str:
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001"
    )

    vector_store = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings
    )

    docs = vector_store.similarity_search(query, k=k)

    if not docs:
        return ""

    return "\n\n".join(
        [
            f"[출처: {doc.metadata.get('source')}]\n{doc.page_content}"
            for doc in docs
        ]
    )