"""
카테고리 XLSX 파일을 main_category / middle_category 테이블에 적재하는 1회성 스크립트.
실행: python -m app.scripts.category_load_excelToSQL
"""
import sys
import os
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.database.connection import get_db_connection


def setup_databaseToCategory():
    """main_category, middle_category 테이블 생성 (기존 테이블 DROP 후 재생성)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS main_category CASCADE;")
    cursor.execute("""
        CREATE TABLE main_category (
            main_category_id          BIGSERIAL PRIMARY KEY,
            main_name                 VARCHAR(100) NOT NULL UNIQUE,
            avg_middle_category_carbon DECIMAL(20,10) NOT NULL,
            sum_middle_category_carbon DECIMAL(20,10) DEFAULT 0
        );
    """)

    cursor.execute("DROP TABLE IF EXISTS middle_category CASCADE;")
    cursor.execute("""
        CREATE TABLE middle_category (
            middle_category_id BIGSERIAL PRIMARY KEY,
            main_category_id   BIGINT NOT NULL REFERENCES main_category(main_category_id),
            middle_name        VARCHAR(100) NOT NULL,
            co2eq_KRW          DECIMAL(20,10) NOT NULL
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("main_category, middle_category 테이블 생성 완료.")


def process_and_load_xlsx(xlsx_filepath: str):
    """
    XLSX 파일을 읽어 메인 카테고리별로 그룹화 후 main_category → middle_category 순으로 적재.
    XLSX 컬럼: '메인 카테고리', '세부 카테고리', '탄소 배출량(kgCo2eq_KRW)'
    avg_middle_category_carbon = 해당 메인 카테고리 산하 세부 카테고리 탄소배출량 평균값.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    file_name = os.path.basename(xlsx_filepath)

    cols = ["메인 카테고리", "세부 카테고리", "탄소 배출량(kgCo2eq_KRW)"]
    df = pd.read_excel(xlsx_filepath)
    df.columns = df.columns.str.strip()
    df = df[cols].dropna(subset=["메인 카테고리", "세부 카테고리"])

    try:
        for main_name, group in df.groupby("메인 카테고리"):
            avg_carbon = group["탄소 배출량(kgCo2eq_KRW)"].mean()

            cursor.execute(
                """
                INSERT INTO main_category (main_name, avg_middle_category_carbon, sum_middle_category_carbon)
                VALUES (%s, %s, 0)
                ON CONFLICT (main_name) DO UPDATE SET main_name = EXCLUDED.main_name
                RETURNING main_category_id;
                """,
                (main_name, float(avg_carbon)),
            )
            main_category_id = cursor.fetchone()[0]

            for _, row in group.iterrows():
                cursor.execute(
                    """
                    INSERT INTO middle_category (main_category_id, middle_name, co2eq_KRW)
                    VALUES (%s, %s, %s);
                    """,
                    (main_category_id, row[cols[1]], float(row[cols[2]])),
                )

        conn.commit()
        print(f"[{file_name}] 적재 완료 — main: {df['메인 카테고리'].nunique()}개, middle: {len(df)}개")

    except Exception as e:
        conn.rollback()
        print(f"[{file_name}] 오류: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    setup_databaseToCategory()
    xlsx_path = "../../data/category_carbon_v2.xlsx"
    process_and_load_xlsx(xlsx_path)
    print("카테고리 데이터 적재 완료.")
