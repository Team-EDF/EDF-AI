import os
import glob
import json
import pandas as pd


def test_extract_to_json(data_dir: str, output_json: str, sample_only: bool = True):
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))

    if not csv_files:
        print(f"⚠️ '{data_dir}' 경로에 CSV 파일이 없습니다. 경로를 확인해 주세요.")
        return

    print(f"🔍 총 {len(csv_files)}개의 CSV 파일을 발견했습니다. 추출 테스트 및 중복 추적을 시작합니다!\n")

    # 전체 데이터를 누적할 빈 데이터프레임
    all_extracted_data = pd.DataFrame()
    total_raw_row_count = 0

    # 🌟 [추가됨] 중복 데이터의 위치 정보를 담을 리스트
    duplicate_trace_log = []

    for file_path in csv_files:
        file_name = os.path.basename(file_path)
        print(f"📥 [{file_name}] 데이터 추출 및 추적 중...")

        try:
            # 1. 파일 읽기
            cols_to_use = ['상호명', '표준산업분류명', '도로명주소']
            df = pd.read_csv(file_path, usecols=cols_to_use, low_memory=False)

            raw_count = len(df)
            total_raw_row_count += raw_count

            # 🌟 [추가됨] CSV 파일에서의 실제 줄 번호(Line Number) 기록 (헤더 1줄 + 0-인덱스 = +2)
            df['csv_line_number'] = df.index + 2

            # 2. 필수 데이터가 비어있는 불량 행 제거
            df = df.dropna(subset=['상호명', '도로명주소'])

            # 🌟 [추가됨] 중복 추적 로직 (keep=False로 복사본들 전부 색출)
            all_dupes = df[df.duplicated(subset=['상호명', '도로명주소'], keep=False)]
            if not all_dupes.empty:
                # 상호명+주소 기준으로 그룹화하여 겹치는 줄 번호들을 리스트로 묶음
                grouped_dupes = all_dupes.groupby(['상호명', '도로명주소'])['csv_line_number'].apply(list).reset_index()
                for _, row in grouped_dupes.iterrows():
                    duplicate_trace_log.append({
                        "파일명": file_name,
                        "상호명": row['상호명'],
                        "도로명주소": row['도로명주소'],
                        "중복발견_CSV_행번호": row['csv_line_number']  # 예: [150, 2800, 3100]
                    })

            # 3. 1차 중복 제거
            df_unique = df.drop_duplicates(subset=['상호명', '도로명주소'])

            # 4. 샘플링 제어
            if sample_only:
                df_to_add = df_unique.head(500)
            else:
                df_to_add = df_unique

            # 5. 전체 누적 데이터에 병합 (줄 번호 컬럼은 제외하고 저장)
            all_extracted_data = pd.concat([all_extracted_data, df_to_add[['상호명', '표준산업분류명', '도로명주소']]],
                                           ignore_index=True)
            print(f"   ➔ 원본 {raw_count:,}건 중 고유 데이터 {len(df_unique):,}건 추출 완료.")

        except Exception as e:
            print(f"❌ [{file_name}] 처리 중 에러 발생: {e}")

    # 6. 전국 데이터 병합 후 혹시 모를 2차 중복 제거
    print("\n⏳ 통합 데이터 정렬 및 최종 중복 제거 중...")
    final_data = all_extracted_data.drop_duplicates(subset=['상호명', '도로명주소'])

    # 7. JSON 형식으로 변환 및 저장
    records = final_data.to_dict(orient='records')

    print(f"💾 결과 JSON 파일 저장 중... ({output_json})")
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=4)

    # 🌟 [추가됨] 중복 추적 로그 JSON 저장
    trace_file = "duplicate_trace_log.json"
    with open(trace_file, 'w', encoding='utf-8') as f:
        json.dump(duplicate_trace_log, f, ensure_ascii=False, indent=4)

    # 8. 최종 리포트 출력
    print("=" * 50)
    print("🎉 테스트 및 추적 완료!")
    print(f"📊 스캔한 전체 원본 데이터: {total_raw_row_count:,}건")
    if sample_only:
        print(f"⚠️ 샘플 모드 작동: 지역별 500건씩 총 {len(records):,}건만 안전하게 JSON으로 추출했습니다.")
    else:
        print(f"📊 전체 추출 완료: 총 {len(records):,}건이 JSON으로 저장되었습니다.")
    print(f"👉 엑기스 데이터 확인 경로: {output_json}")
    print(f"🕵️‍♂️ 중복 추적 로그 확인 경로: {trace_file}")


if __name__ == "__main__":
    DATA_DIRECTORY = "../../data/SBO"  # CSV 파일들이 있는 폴더
    OUTPUT_FILE = "test_merchant_address_sample.json"  # 저장될 JSON 파일명

    test_extract_to_json(DATA_DIRECTORY, OUTPUT_FILE, sample_only=False)