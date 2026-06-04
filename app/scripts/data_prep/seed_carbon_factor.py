import os
import pymrio
import pandas as pd
import shutil

# EXIOBASE의 카테고리 별 추출을 위해 사용
# ixi(산업), pxp(제품)

# 설정값 및 경로
ZIP_PATH_IXI = "../../../data/IOT_2020_ixi.zip"
ZIP_PATH_PXP = "../../../data/IOT_2020_pxp.zip"
SAVE_FOLDER_IXI = "../../data/exiobase_parsed_ixi"
SAVE_FOLDER_PXP = "../../data/exiobase_parsed_pxp"


OUTPUT_RAW_DATA = "../../data/greenstep_raw_sectors_for_mapping.csv"

EUR_TO_KRW_2020 = 1346.0
CPI_RATIO = 100.0 / 111.8

# 데이터 추출 모듈 (평균/그룹화 로직 일절 없음)


def extract_exiobase_data(zip_path, save_folder, data_type="ixi"):
    print(f"\n[{data_type.upper()}] 데이터 파싱 시작...")
    json_path = os.path.join(save_folder, "file_parameters.json")

    # 찌꺼기 파일 방어 로직
    if os.path.exists(json_path) and os.path.getsize(json_path) == 0:
        print(f"⚠️ {data_type} 설정 파일 깨짐 감지. 폴더 초기화 중...")
        shutil.rmtree(save_folder)

    if os.path.exists(save_folder) and os.path.exists(json_path):
        print(f"📂 저장된 {data_type} 데이터 로드 중...")
        exio = pymrio.load_all(save_folder)
    else:
        print(f"📂 {data_type} 최초 파싱 및 역행렬 계산 중... (약 10~20분 소요)")
        if os.path.exists(save_folder):
            shutil.rmtree(save_folder)
        exio = pymrio.parse_exiobase3(path=zip_path)
        exio.calc_all()
        exio.save_all(save_folder)

    ext_obj = None
    for attr_name in ["air_emissions", "satellite", "emissions", "impacts"]:
        if hasattr(exio, attr_name) and hasattr(getattr(exio, attr_name), "M"):
            ext_obj = getattr(exio, attr_name)
            break

    if ext_obj is None:
        raise ValueError(f"❌ {data_type} 탄소 배출량(M) 행렬을 찾을 수 없습니다.")

    M_matrix = ext_obj.M
    units_df = ext_obj.unit

    # 한국(KR) 데이터만 필터링
    kr_cols = [(country, item) for country, item in M_matrix.columns if country == "KR"]

    # 온실가스 성분별 행 탐색
    co2_rows = [r for r in M_matrix.index if 'CO2' in r and 'bio' not in r.lower()]
    ch4_rows = [r for r in M_matrix.index if 'CH4' in r]
    n2o_rows = [r for r in M_matrix.index if 'N2O' in r]
    f_gas_rows = [r for r in M_matrix.index if any(f in r for f in ['SF6', 'HFC', 'PFC'])]

    ghg_sum = pd.Series(0.0, index=kr_cols)

    def get_unit_multiplier(row_name):
        try:
            unit_str = str(units_df.loc[row_name].values[0]).lower().strip()
            if unit_str == 'g':
                return 0.001
            elif unit_str == 'mg':
                return 0.000001
            elif 'ton' in unit_str:
                return 1000.0
            return 1.0
        except:
            return 1.0

    # 배출량 합산 (수학적 단위 변환만 수행)
    for r in co2_rows: ghg_sum += M_matrix.loc[r, kr_cols] * get_unit_multiplier(r) * 1.0
    for r in ch4_rows: ghg_sum += M_matrix.loc[r, kr_cols] * get_unit_multiplier(r) * 28.0
    for r in n2o_rows: ghg_sum += M_matrix.loc[r, kr_cols] * get_unit_multiplier(r) * 265.0
    for r in f_gas_rows: ghg_sum += M_matrix.loc[r, kr_cols] * get_unit_multiplier(r) * 1.0

    # 최종 탄소계수 환산 (kgCO2e / KRW)
    kr_factors_krw = (ghg_sum / 1_000_000 / EUR_TO_KRW_2020 * CPI_RATIO)

    results = []
    for (country, item), factor in kr_factors_krw.items():
        results.append({
            "데이터구분": f"산업({data_type})" if data_type == "ixi" else f"제품({data_type})",
            "원문명칭": item,
            "탄소계수_KRW": round(factor, 8)
        })

    return pd.DataFrame(results)


# 메인 실행부
def main():
    print("GreenStep 순수 데이터 추출 시작")

    df_ixi = extract_exiobase_data(ZIP_PATH_IXI, SAVE_FOLDER_IXI, "ixi")
    df_pxp = extract_exiobase_data(ZIP_PATH_PXP, SAVE_FOLDER_PXP, "pxp")

    # 두 데이터 단순 병합 (위아래로 이어 붙이기만 함)
    df_master = pd.concat([df_ixi, df_pxp], ignore_index=True)


    # 컬럼 순서 정리 및 정렬
    cols = ['원문명칭', '데이터구분', '탄소계수_KRW']
    df_master = df_master[cols].sort_values(by=['원문명칭', '데이터구분'])

    # 파일 저장 (평균 계산 일절 없음)
    df_master.to_csv(OUTPUT_RAW_DATA, index=False, encoding='utf-8-sig')

    print(f"\n✅ 데이터 추출 완료: 총 {len(df_master)}건")
    print(f"💾 저장 위치: {OUTPUT_RAW_DATA}")

if __name__ == "__main__":
    main()
