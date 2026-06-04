import sys
import os
import glob
import pandas as pd

# 모듈 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.database.connection import get_db_connection


def setup_databaseToCategory():
    conn = get_db_connection()
    cursor = conn.cursor()

    # main_category 생성 구문
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS main_category (
                main_category_id BIGSERIAL PRIMARY KEY, 
                main_name VARCHAR(100) NOT NULL UNIQUE,
                avg_middle_category_carbon DECIMAL(20,10) NOT NULL,
                sum_middle_category_carbon DECIMAL(20,10) DEFAULT 0
            );
        """)

    # middle_category 생성 구문
    print("⚙️ category 테이블 구조 업데이트 중...")
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS middle_category (
                middle_category_id BIGSERIAL PRIMARY KEY,
                main_category_id BIGINT NOT NULL REFERENCES main_category(main_category_id), 
                middle_name VARCHAR(100) NOT NULL,
                co2eq_KRW DECIMAL(20,10) NOT NULL
            );
        """)

    conn.commit()
    cursor.close()
    conn.close()
    print("테이블 생성완료...")

def process_and_load_xlsx(xlsx_filepath: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    file_name = os.path.basename(xlsx_filepath)

    print(f"[{file_name}] 처리 시작..")

    cols = ['메인 카테고리', '세부 카테고리', '탄소 배출량(kgCo2eq_KRW)']

    df = pd.read_excel(xlsx_filepath)
    df.columns = df.columns.str.strip() # trailing space 제거
    df = df[cols].dropna(subset=['메인 카테고리', '세부 카테고리'])

    try:
        for main_name, group in df.groupby('메인 카테고리'):
            avg_carbon = group['탄소 배출량(kgCo2eq_KRW)'].mean()

            cursor.execute("""
                        INSERT INTO main_category (main_name, avg_middle_category_carbon, sum_middle_category_carbon)
                        VALUES (%s, %s, 0)
                        ON CONFLICT (main_name) DO UPDATE SET main_name = EXCLUDED.main_name
                        RETURNING main_category_id;
                    """, (main_name, float(avg_carbon)))

            main_category_id = cursor.fetchone()[0]

            for _, row in group.iterrows():
                cursor.execute("""
                            INSERT INTO middle_category (main_category_id, middle_name, co2eq_KRW)
                            VALUES (%s, %s, %s);
                        """, (main_category_id, row[cols[1]], float(row[cols[2]])))

        conn.commit()

        print(f"[{file_name}] ✅ 적재 완료 - main: {df['메인 카테고리'].nunique()}개, middle: {len(df)}개")

    except Exception as e:
        conn.rollback()
        print(f"[{file_name}] ❌ 오류 발생: {e}")
        raise

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    setup_databaseToCategory()

    xlsx_path = "../../data/category_carbon.xlsx"
    process_and_load_xlsx(xlsx_path)

    print("\n🎉 카테고리 데이터 적재 완료!")