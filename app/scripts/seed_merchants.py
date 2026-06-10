"""
소상공인(SBO) CSV 데이터를 MERCHANT_SME 테이블에 적재하는 1회성 스크립트.
실행: python -m app.scripts.SBO_load_csvToSQL
"""
import sys
import os
import glob
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.database.connection import get_db_connection
from app.core.utils import normalize_region_name, preprocess_merchant_name


def setup_databaseToSME():
    """MERCHANT_SME 테이블 및 인덱스 생성 (pg_trgm 확장 포함)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS MERCHANT_SME CASCADE;")
    cursor.execute("""
        CREATE TABLE MERCHANT_SME (
            merchant_id   BIGSERIAL PRIMARY KEY,
            original_name VARCHAR(100) NOT NULL,
            business_name VARCHAR(100),
            road_address  VARCHAR(100) NOT NULL,
            collected_at  DATE,
            UNIQUE(original_name, road_address)
        );
        CREATE INDEX IF NOT EXISTS idx_road_address
            ON MERCHANT_SME (road_address);
        CREATE INDEX IF NOT EXISTS idx_original_name
            ON MERCHANT_SME (original_name);
    """)
    cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trgm_original_name
            ON MERCHANT_SME USING GIN (original_name gin_trgm_ops);
    """)
    conn.commit()
    cursor.close()
    conn.close()
    print("MERCHANT_SME 테이블 생성 완료.")


def process_and_load_csv(csv_filepath: str, target_date: str):
    """CSV 파일 1개를 읽어 전처리 후 MERCHANT_SME에 적재."""
    conn = get_db_connection()
    cursor = conn.cursor()
    file_name = os.path.basename(csv_filepath)

    cols_to_use = ["상호명", "지점명", "상권업종중분류명", "도로명주소"]
    try:
        df = pd.read_csv(csv_filepath, usecols=cols_to_use, low_memory=False)
        df = df.dropna(subset=["상호명", "도로명주소"])

        # 지점명 병합 + 대문자 변환 + 공백 제거
        df["상호명"] = df.apply(
            lambda row: preprocess_merchant_name(row["상호명"], row["지점명"]), axis=1
        )
        # 시/도 약칭 정규화 (예: 경기도 → 경기)
        df["도로명주소"] = df["도로명주소"].apply(normalize_region_name)
        df_unique = df.drop_duplicates(subset=["상호명", "도로명주소"])

        insert_query = """
            INSERT INTO MERCHANT_SME (original_name, business_name, road_address, collected_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (original_name, road_address)
            DO UPDATE SET collected_at = EXCLUDED.collected_at
        """
        data_tuples = list(zip(
            df_unique["상호명"],
            df_unique["상권업종중분류명"],
            df_unique["도로명주소"],
            [target_date] * len(df_unique),
        ))
        cursor.executemany(insert_query, data_tuples)
        conn.commit()
        print(f"[{file_name}] {len(data_tuples):,}건 적재 완료 (기준일: {target_date})")

    except Exception as e:
        conn.rollback()
        print(f"[{file_name}] 오류: {e}")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    TARGET_DATE = "2026-03-31"  # 수집 기준일: 새 데이터 적재 시 업데이트

    setup_databaseToSME()

    data_dir = "../../data/SBO"
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))

    if not csv_files:
        print(f"'{data_dir}' 경로에 CSV 파일이 없습니다.")
    else:
        print(f"총 {len(csv_files)}개 파일 적재 시작 (기준일: {TARGET_DATE})")
        for file_path in csv_files:
            process_and_load_csv(file_path, TARGET_DATE)
        print("모든 파일 적재 완료.")
