from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings

CHROMA_DIR = "chroma_db"


def get_rag_context(query: str, k: int = 3) -> str:
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001"
    )

    vectorstore = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings
    )

    docs = vectorstore.similarity_search(query, k=k)

    return "\n\n".join(
        f"[출처: {doc.metadata.get('source')}]\n{doc.page_content}"
        for doc in docs
    )