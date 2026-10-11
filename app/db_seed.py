import os

from app.database.connection import get_db_connection
from app.scripts.embed_categories import (
    embed_main_categories,
    embed_middle_categories,
)
from app.scripts.seed_categories import load_categories


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CATEGORY_XLSX_PATH = os.getenv(
    "CATEGORY_XLSX_PATH",
    os.path.join(BASE_DIR, "data", "category_carbon.xlsx"),
)

# 여러 Pod/워커가 동시에 기동해도 시드는 한 곳에서만 실행되도록
# 트랜잭션 단위 advisory lock을 건다. (임의의 고정 키)
SEED_LOCK_KEY = 7_420_001


def _count(cursor, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {table};")
    return cursor.fetchone()[0]


def _create_tables(cursor) -> None:
    """
    분류에 필요한 참조 테이블을 생성한다.

    scripts/의 1회성 스크립트와 달리 DROP 하지 않는다.
    consumption 계열 테이블이 main/middle_category를 FK로 참조하므로
    DROP ... CASCADE 시 해당 FK까지 사라지기 때문.
    """
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS main_category (
            main_category_id           BIGSERIAL PRIMARY KEY,
            main_name                  VARCHAR(100) NOT NULL UNIQUE,
            avg_middle_category_carbon DECIMAL(20,10) NOT NULL,
            sum_middle_category_carbon DECIMAL(20,10) DEFAULT 0
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS middle_category (
            middle_category_id BIGSERIAL PRIMARY KEY,
            main_category_id   BIGINT NOT NULL REFERENCES main_category(main_category_id),
            middle_name        VARCHAR(100) NOT NULL,
            co2eq_KRW          DECIMAL(20,10) NOT NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS main_category_vec (
            vec_id           BIGSERIAL PRIMARY KEY,
            main_category_id BIGINT REFERENCES main_category(main_category_id),
            main_name        VARCHAR(255),
            embedding        VECTOR(1024)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS middle_category_vec (
            vec_id             BIGSERIAL PRIMARY KEY,
            middle_category_id BIGINT REFERENCES middle_category(middle_category_id),
            middle_name        VARCHAR(255),
            embedding          VECTOR(1024)
        );
    """)

    # 데이터(data/SBO/*.csv)는 scripts/seed_merchants.py로 별도 적재.
    # 테이블이 없으면 가맹점 조회 쿼리가 실패하므로 빈 테이블이라도 만들어 둔다.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS MERCHANT_SME (
            merchant_id   BIGSERIAL PRIMARY KEY,
            original_name VARCHAR(100) NOT NULL,
            business_name VARCHAR(100),
            road_address  VARCHAR(100) NOT NULL,
            collected_at  DATE,
            UNIQUE(original_name, road_address)
        );
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_road_address ON MERCHANT_SME (road_address);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_original_name ON MERCHANT_SME (original_name);"
    )
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trgm_original_name
            ON MERCHANT_SME USING GIN (original_name gin_trgm_ops);
    """)


def seed_reference_data(model) -> None:
    """
    DB가 초기화된 경우 분류용 참조 데이터를 자동으로 채운다.

    - main/middle_category가 비어 있으면 CATEGORY_XLSX_PATH에서 적재
    - *_vec 행 수가 카테고리 수와 다르면 SBERT 임베딩 재생성
    - 이미 채워진 DB에서는 아무것도 바꾸지 않는다.
    """
    print("[DB Seed] Checking reference data...")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT pg_advisory_xact_lock(%s);", (SEED_LOCK_KEY,))
        _create_tables(cursor)

        if _count(cursor, "main_category") == 0:
            main_count, middle_count = load_categories(cursor, CATEGORY_XLSX_PATH)
            print(
                f"[DB Seed] Loaded categories from {CATEGORY_XLSX_PATH} "
                f"(main: {main_count}, middle: {middle_count})"
            )

        if _count(cursor, "main_category_vec") != _count(cursor, "main_category"):
            count = embed_main_categories(cursor, model)
            print(f"[DB Seed] Embedded {count} main categories.")

        if _count(cursor, "middle_category_vec") != _count(cursor, "middle_category"):
            count = embed_middle_categories(cursor, model)
            print(f"[DB Seed] Embedded {count} middle categories.")

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()

    print("[DB Seed] Reference data is ready.")
