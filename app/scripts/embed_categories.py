"""
카테고리 임베딩 스크립트 — SBERT 분류(2단계) 사용 전 1회 필수 실행.

middle_category, main_category 이름만 임베딩하여 각 vec 테이블에 저장.
탄소배출량은 임베딩 대상 아님 — 매칭 후 원본 테이블에서 JOIN으로 가져옴.
"""

import os
import sys

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

sys.path.append(BASE_DIR)

from sentence_transformers import SentenceTransformer

from app.database.connection import get_db_connection
from app.services.category_maps import MAIN_CATEGORY_KEYWORD, MIDDLE_CATEGORY_KEYWORD


SBERT_MODEL_NAME = os.getenv(
    "SBERT_MODEL_PATH",
    os.path.join(BASE_DIR, "app", "services", "greenstep_sbert_v2"),
)


def setup_tables(cursor) -> None:
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    cursor.execute("DROP TABLE IF EXISTS middle_category_vec CASCADE;")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS middle_category_vec (
            vec_id             BIGSERIAL PRIMARY KEY,
            middle_category_id BIGINT REFERENCES middle_category(middle_category_id),
            middle_name        VARCHAR(255),
            embedding          VECTOR(1024)
        );
    """)

    cursor.execute("DROP TABLE IF EXISTS main_category_vec CASCADE;")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS main_category_vec (
            vec_id           BIGSERIAL PRIMARY KEY,
            main_category_id BIGINT REFERENCES main_category(main_category_id),
            main_name        VARCHAR(255),
            embedding        VECTOR(1024)
        );
    """)


def _to_embedding_str(vec: list) -> str:
    return "[" + ",".join(map(str, vec)) + "]"


def embed_middle_categories(cursor, model: SentenceTransformer) -> int:
    cursor.execute("TRUNCATE TABLE middle_category_vec;")
    cursor.execute("SELECT middle_category_id, middle_name FROM middle_category;")
    rows = cursor.fetchall()

    for middle_category_id, middle_name in rows:
        context_text = MIDDLE_CATEGORY_KEYWORD.get(middle_name, middle_name)
        embedding_str = _to_embedding_str(model.encode(context_text).tolist())

        cursor.execute(
            """
            INSERT INTO middle_category_vec (middle_category_id, middle_name, embedding)
            VALUES (%s, %s, %s::vector);
            """,
            (middle_category_id, middle_name, embedding_str),
        )

    return len(rows)


def embed_main_categories(cursor, model: SentenceTransformer) -> int:
    cursor.execute("TRUNCATE TABLE main_category_vec;")
    cursor.execute("SELECT main_category_id, main_name FROM main_category;")
    rows = cursor.fetchall()

    for main_category_id, main_name in rows:
        context_text = MAIN_CATEGORY_KEYWORD.get(main_name, main_name)
        embedding_str = _to_embedding_str(model.encode(context_text).tolist())

        cursor.execute(
            """
            INSERT INTO main_category_vec (main_category_id, main_name, embedding)
            VALUES (%s, %s, %s::vector);
            """,
            (main_category_id, main_name, embedding_str),
        )

    return len(rows)


def main():
    print(f"Loading SBERT model ({SBERT_MODEL_NAME})...")

    modules_file = os.path.join(SBERT_MODEL_NAME, "modules.json")
    if not os.path.isfile(modules_file):
        raise RuntimeError(
            "SBERT model is not mounted correctly. "
            f"Expected file: {modules_file}"
        )

    model = SentenceTransformer(
        SBERT_MODEL_NAME,
        local_files_only=True,
        device="cpu",
    )

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        print("Setting up pgvector tables...")
        setup_tables(cursor)

        print("Embedding main categories...")
        main_count = embed_main_categories(cursor, model)
        print(f"✅ Inserted {main_count} main category embeddings.")

        print("Embedding middle categories...")
        middle_count = embed_middle_categories(cursor, model)
        print(f"✅ Inserted {middle_count} middle category embeddings.")

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Error occurred: {e}")
        raise

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()