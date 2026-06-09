import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

sys.path.append(BASE_DIR)

from app.database.connection import get_db_connection


EXCEL_PATH = os.path.join(BASE_DIR, "data", "category_carbon.xlsx")


def setup_database_to_category():
    conn = get_db_connection()
    cursor = conn.cursor()

    print("⚙️ category 테이블 구조 업데이트 중...")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS main_category (
            main_category_id BIGSERIAL PRIMARY KEY,
            main_name VARCHAR(100) NOT NULL UNIQUE,
            avg_middle_category_carbon DECIMAL(20,10) NOT NULL,
            sum_middle_category_carbon DECIMAL(20,10) DEFAULT 0
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS middle_category (
            middle_category_id BIGSERIAL PRIMARY KEY,
            main_category_id BIGINT NOT NULL REFERENCES main_category(main_category_id),
            middle_name VARCHAR(100) NOT NULL UNIQUE,
            co2eq_KRW DECIMAL(20,10) NOT NULL
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()

    print("✅ 테이블 생성 완료")


def process_and_load_xlsx(xlsx_filepath: str):
    if not os.path.exists(xlsx_filepath):
        raise FileNotFoundError(f"엑셀 파일을 찾을 수 없습니다: {xlsx_filepath}")

    conn = get_db_connection()
    cursor = conn.cursor()
    file_name = os.path.basename(xlsx_filepath)

    print(f"[{file_name}] 처리 시작")
    print("EXCEL PATH =", xlsx_filepath)
    print("FILE EXISTS =", os.path.exists(xlsx_filepath))

    cols = [
        "메인 카테고리",
        "세부 카테고리",
        "탄소 배출량(kgCo2eq_KRW)",
    ]

    df = pd.read_excel(xlsx_filepath)
    df.columns = df.columns.str.strip()

    missing_cols = [col for col in cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"엑셀에 필요한 컬럼이 없습니다: {missing_cols}")

    df = df[cols].dropna(
        subset=[
            "메인 카테고리",
            "세부 카테고리",
            "탄소 배출량(kgCo2eq_KRW)",
        ]
    )

    try:
        cursor.execute("DELETE FROM middle_category;")
        cursor.execute("DELETE FROM main_category;")

        for main_name, group in df.groupby("메인 카테고리"):
            avg_carbon = group["탄소 배출량(kgCo2eq_KRW)"].mean()
            sum_carbon = group["탄소 배출량(kgCo2eq_KRW)"].sum()

            cursor.execute(
                """
                INSERT INTO main_category (
                    main_name,
                    avg_middle_category_carbon,
                    sum_middle_category_carbon
                )
                VALUES (%s, %s, %s)
                RETURNING main_category_id;
                """,
                (
                    str(main_name).strip(),
                    float(avg_carbon),
                    float(sum_carbon),
                ),
            )

            main_category_id = cursor.fetchone()[0]

            for _, row in group.iterrows():
                middle_name = str(row["세부 카테고리"]).strip()
                co2eq_krw = float(row["탄소 배출량(kgCo2eq_KRW)"])

                cursor.execute(
                    """
                    INSERT INTO middle_category (
                        main_category_id,
                        middle_name,
                        co2eq_KRW
                    )
                    VALUES (%s, %s, %s);
                    """,
                    (
                        main_category_id,
                        middle_name,
                        co2eq_krw,
                    ),
                )

        conn.commit()

        print(
            f"[{file_name}] ✅ 적재 완료 - "
            f"main: {df['메인 카테고리'].nunique()}개, "
            f"middle: {len(df)}개"
        )

    except Exception as e:
        conn.rollback()
        print(f"[{file_name}] ❌ 오류 발생: {e}")
        raise

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    setup_database_to_category()
    process_and_load_xlsx(EXCEL_PATH)

    print("\n🎉 카테고리 데이터 적재 완료")