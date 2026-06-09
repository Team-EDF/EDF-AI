from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

CHROMA_DIR = "chroma_db"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL
    )


def search_rag(query: str, k: int = 3):
    embeddings = get_embeddings()

    vectorstore = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings
    )

    docs = vectorstore.similarity_search(query, k=k)

    context_parts = []
    sources = []

    for idx, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source")
        page = doc.metadata.get("page")
        doc_type = doc.metadata.get("type")

        context_parts.append(
            f"[문서 {idx}]\n"
            f"출처: {source}\n"
            f"페이지: {page}\n"
            f"내용:\n{doc.page_content}"
        )

        sources.append(
            {
                "source": source,
                "page": page,
                "type": doc_type,
                "preview": doc.page_content[:200]
            }
        )

    return {
        "context": "\n\n".join(context_parts),
        "sources": sources
    }


def get_rag_context(query: str, k: int = 3) -> str:
    result = search_rag(query, k)
    return result["context"]