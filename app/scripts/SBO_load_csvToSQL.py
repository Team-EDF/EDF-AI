import sys
import os
import glob
import pandas as pd

# 모듈 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.database.connection import get_db_connection


def setup_databaseToSME():
    """1. DB 테이블 및 인덱스 세팅 (collected_at 컬럼 유지)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    print("⚙️ MERCHANT_SME 테이블 구조 업데이트 중 (collected_at 포함)...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS MERCHANT_SME (
            merchant_id BIGSERIAL PRIMARY KEY, 
            original_name VARCHAR(100) NOT NULL,
            industry_name VARCHAR(100),
            road_address VARCHAR(100) NOT NULL,
            collected_at DATE, 
            UNIQUE(original_name, road_address)
        );

        CREATE INDEX IF NOT EXISTS idx_road_address ON MERCHANT_SME (road_address);
        CREATE INDEX IF NOT EXISTS idx_original_name ON MERCHANT_SME (original_name);
    """)
    conn.commit()
    cursor.close()
    conn.close()


def process_and_load_csv(csv_filepath: str, target_date: str):
    """2. CSV 파일을 읽어서 우리가 지정한 수동 날짜와 함께 DB에 적재"""
    conn = get_db_connection()
    cursor = conn.cursor()
    file_name = os.path.basename(csv_filepath)

    print(f"📥 [{file_name}] 처리 시작...")

    # CSV에는 날짜가 없으므로 필요한 3개만 읽어옵니다.
    cols_to_use = ['상호명', '표준산업분류명', '도로명주소']

    try:
        df = pd.read_csv(csv_filepath, usecols=cols_to_use, low_memory=False)
        df = df.dropna(subset=['상호명', '도로명주소'])
        df_unique = df.drop_duplicates(subset=['상호명', '도로명주소'])

        insert_query = """
            INSERT INTO MERCHANT_SME (original_name, industry_name, road_address, collected_at) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (original_name, road_address) 
            DO UPDATE SET collected_at = EXCLUDED.collected_at
        """

        # 🌟 핵심: CSV 데이터 리스트 끝에 우리가 지정한 target_date를 억지로 끼워 넣습니다.
        data_tuples = list(zip(
            df_unique['상호명'],
            df_unique['표준산업분류명'],
            df_unique['도로명주소'],
            [target_date] * len(df_unique)  # 데이터 개수만큼 날짜를 복제해서 붙임!
        ))

        cursor.executemany(insert_query, data_tuples)
        conn.commit()

        print(f"✅ [{file_name}] 완료! ({len(data_tuples):,}건 적재 및 '{target_date}' 도장 쾅!)")

    except Exception as e:
        print(f"❌ [{file_name}] 처리 중 에러 발생: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    # 🌟 수집 날짜 수동 설정 (여기를 나중에 업데이트 날짜로 바꿔주시면 됩니다)
    TARGET_DATE = '2026-03-31'

    setup_databaseToSME()

    data_dir = "../../data/SBO"
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))

    if not csv_files:
        print(f"⚠️ '{data_dir}' 경로에 CSV 파일이 없습니다.")
    else:
        print(f"\n🔍 총 {len(csv_files)}개 파일 적재 시작... (기준일: {TARGET_DATE})\n")
        for file_path in csv_files:
            # 지정한 날짜를 함수로 넘겨줍니다.
            process_and_load_csv(file_path, TARGET_DATE)

        print(f"\n🎉 모든 데이터가 '{TARGET_DATE}' 수집 날짜를 달고 성공적으로 적재되었습니다!")