"""
ERD 기반 테이블 생성 스크립트.
실행: python -m app.scripts.create_records_tables
"""
from app.database.connection import get_db_connection

DDL = """
DROP TABLE IF EXISTS category_stats CASCADE;
DROP TABLE IF EXISTS periodic_stats CASCADE;
DROP TABLE IF EXISTS items CASCADE;
DROP TABLE IF EXISTS consumption_records CASCADE;

CREATE TABLE consumption_records (
    record_id        SERIAL PRIMARY KEY,
    user_id          INTEGER,
    merchant_name    VARCHAR(200) NOT NULL,
    payment_location VARCHAR(300),
    source_type      VARCHAR(20) NOT NULL DEFAULT 'receipt',
    raw_text         TEXT,
    image_url        TEXT,
    ocr_status       VARCHAR(20),
    record_date      DATE,
    total_amount     FLOAT,
    total_carbon_kg  FLOAT
);

CREATE TABLE items (
    item_id            SERIAL PRIMARY KEY,
    record_id          INTEGER NOT NULL REFERENCES consumption_records(record_id) ON DELETE CASCADE,
    main_category_id   INTEGER REFERENCES main_category(main_category_id),
    middle_category_id INTEGER REFERENCES middle_category(middle_category_id),
    source_type        VARCHAR(20) NOT NULL,
    source_msg         VARCHAR(300),
    amount             FLOAT,
    classify_stage     INTEGER,
    carbon_kg          FLOAT
);

CREATE TABLE periodic_stats (
    stat_id        SERIAL PRIMARY KEY,
    user_id        INTEGER,
    goal_id        INTEGER,
    period_type    VARCHAR(10) NOT NULL,
    period_start   DATE NOT NULL,
    total_carbon   FLOAT NOT NULL DEFAULT 0,
    total_spending FLOAT NOT NULL DEFAULT 0
);

CREATE TABLE category_stats (
    cat_stat_id       SERIAL PRIMARY KEY,
    -- periodic_stats FK 제거: category_stats를 독립 집계 테이블로 분리
    -- (user_id, period_type, period_start, main_category_id) 조합으로 직접 UPSERT
    user_id           INTEGER,
    period_type       VARCHAR(10) NOT NULL,
    period_start      DATE NOT NULL,
    main_category_id  INTEGER REFERENCES main_category(main_category_id),
    category_name     VARCHAR(50) NOT NULL,
    category_carbon   FLOAT NOT NULL DEFAULT 0,
    category_spending FLOAT NOT NULL DEFAULT 0
);

-- NULL을 포함한 복합 UNIQUE: COALESCE로 NULL을 sentinel(-1)로 치환해
-- PostgreSQL의 "NULL != NULL" 특성으로 인한 중복 INSERT를 방지한다
CREATE UNIQUE INDEX uq_category_stats
ON category_stats (
    COALESCE(user_id, -1),
    period_type,
    period_start,
    COALESCE(main_category_id, -1)
);
"""


def main():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        print("테이블 생성 완료: consumption_records, items, periodic_stats, category_stats")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
