"""
소상공인 CSV 데이터를 JSON으로 추출하고 중복 현황을 기록하는 1회성 스크립트.
DB 적재(SBO_load_csvToSQL.py) 전 데이터 검증 용도.
실행: python -m app.scripts.SBO_load_csvTojson
"""
import os
import glob
import json
import pandas as pd

from app.core.utils import normalize_region_name, preprocess_merchant_name


def extract_to_json(data_dir: str, output_json: str, sample_only: bool = True):
    """
    CSV 파일들을 읽어 전처리·중복 제거 후 JSON으로 저장.
    sample_only=True 이면 파일당 500건만 추출.
    중복 발생 위치는 duplicate_trace_log.json 별도 저장.
    """
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))
    if not csv_files:
        print(f"'{data_dir}' 경로에 CSV 파일이 없습니다.")
        return

    print(f"총 {len(csv_files)}개 CSV 파일 처리 시작")

    all_extracted_data = pd.DataFrame()
    total_raw_row_count = 0
    duplicate_trace_log = []

    for file_path in csv_files:
        file_name = os.path.basename(file_path)
        try:
            cols_to_use = ["상호명", "지점명", "표준산업분류명", "도로명주소"]
            df = pd.read_csv(file_path, usecols=cols_to_use, low_memory=False)

            raw_count = len(df)
            total_raw_row_count += raw_count

            # CSV 원본 행 번호 기록 (헤더 제외 보정: +2)
            df["csv_line_number"] = df.index + 2
            df = df.dropna(subset=["상호명", "도로명주소"])

            df["상호명"] = df.apply(
                lambda row: preprocess_merchant_name(row["상호명"], row["지점명"]), axis=1
            )
            df["도로명주소"] = df["도로명주소"].apply(normalize_region_name)

            # 중복 행 추적 (동일 상호명+주소가 여러 줄에 존재하는 경우)
            dupes = df[df.duplicated(subset=["상호명", "도로명주소"], keep=False)]
            if not dupes.empty:
                grouped = (
                    dupes.groupby(["상호명", "도로명주소"])["csv_line_number"]
                    .apply(list)
                    .reset_index()
                )
                for _, row in grouped.iterrows():
                    duplicate_trace_log.append({
                        "파일명": file_name,
                        "상호명": row["상호명"],
                        "도로명주소": row["도로명주소"],
                        "중복발견_CSV_행번호": row["csv_line_number"],
                    })

            df_unique = df.drop_duplicates(subset=["상호명", "도로명주소"])
            df_to_add = df_unique.head(500) if sample_only else df_unique
            all_extracted_data = pd.concat(
                [all_extracted_data, df_to_add[["상호명", "표준산업분류명", "도로명주소"]]],
                ignore_index=True,
            )
            print(f"[{file_name}] 원본 {raw_count:,}건 → 고유 {len(df_unique):,}건")

        except Exception as e:
            print(f"[{file_name}] 오류: {e}")

    final_data = all_extracted_data.drop_duplicates(subset=["상호명", "도로명주소"])
    records = final_data.to_dict(orient="records")

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=4)

    trace_file = "duplicate_trace_log.json"
    with open(trace_file, "w", encoding="utf-8") as f:
        json.dump(duplicate_trace_log, f, ensure_ascii=False, indent=4)

    print(f"\n원본 합계: {total_raw_row_count:,}건")
    print(f"최종 추출: {len(records):,}건 → {output_json}")
    print(f"중복 추적 로그 → {trace_file}")


if __name__ == "__main__":
    DATA_DIRECTORY = "../../data/SBO"
    OUTPUT_FILE = "merchant_address_sample.json"

    extract_to_json(DATA_DIRECTORY, OUTPUT_FILE, sample_only=False)
