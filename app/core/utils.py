"""
데이터 전처리 유틸리티 모음.
scripts/ 에서 소상공인 CSV 적재 및 AGRIBALYSE 데이터 정제 시 사용.
"""

import re
import statistics
import pandas as pd


def normalize_merchant_name(name: str) -> str:
    name = str(name).strip()
    if not name or name == "nan":
        return ""

    name = re.sub(r"\(.*?\)", "", name).strip()
    name = re.sub(
        r"[\s\-_]*(점|호점|지점|지사|본점|매장|센터|타워|플라자|몰)$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    name = re.sub(
        r"[\s]*(서울|부산|대구|인천|광주|대전|울산|세종|제주|수원|"
        r"강남|강북|강서|강동|홍대|신촌|잠실|건대|역삼|논현|"
        r"판교|성수|여의도|종로|명동|이태원|신림|구로|영등포|상암).+$",
        "",
        name,
    ).strip()
    name = re.sub(r"[\s]*[A-Z]{1,3}$", "", name).strip()
    name = re.sub(r"[\s]*\d+$", "", name).strip()

    return name if name else ""


def normalize_region_name(address: str) -> str:
    if not address or pd.isna(address):
        return ""

    region_mapping = {
        "서울특별시": "서울",
        "부산광역시": "부산",
        "대구광역시": "대구",
        "인천광역시": "인천",
        "광주광역시": "광주",
        "대전광역시": "대전",
        "울산광역시": "울산",
        "세종특별자치시": "세종",
        "세종시": "세종",
        "경기도": "경기",
        "강원특별자치도": "강원",
        "강원도": "강원",
        "충청북도": "충북",
        "충청남도": "충남",
        "전북특별자치도": "전북",
        "전라북도": "전북",
        "전라남도": "전남",
        "경상북도": "경북",
        "경상남도": "경남",
        "제주특별자치도": "제주",
        "제주도": "제주",
    }

    parts = str(address).strip().split()
    if parts and parts[0] in region_mapping:
        parts[0] = region_mapping[parts[0]]

    return "".join(parts)


def preprocess_merchant_name(name, branch) -> str:
    name_str = str(name).strip() if pd.notna(name) else ""
    branch_str = str(branch).strip() if pd.notna(branch) else ""

    combined = name_str + branch_str if branch_str and branch_str.lower() != "nan" else name_str
    return combined.upper().replace(" ", "")


def traffic_fueled_carbon_emissions(
    gross_calorific_value,
    net_calorific_value,
    co2_factor_tC_per_TJ,
    ch4_factor_kg_per_TJ,
    n2o_factor_kg_per_TJ,
    gwp_ch4,
    gwp_n2o,
    fuel_price_per_L,
):
    co2_factor_tCO2_per_TJ = co2_factor_tC_per_TJ * (44 / 12)
    co2_factor_kg_per_TJ = co2_factor_tCO2_per_TJ * 1000

    ch4_co2eq = ch4_factor_kg_per_TJ * gwp_ch4
    n2o_co2eq = n2o_factor_kg_per_TJ * gwp_n2o

    total_co2eq_per_TJ = co2_factor_kg_per_TJ + ch4_co2eq + n2o_co2eq
    total_co2eq_per_MJ = total_co2eq_per_TJ / 1_000_000
    co2eq_per_L = total_co2eq_per_MJ * net_calorific_value
    co2eq_per_KRW = co2eq_per_L / fuel_price_per_L

    return co2eq_per_KRW


def get_median() -> float | None:
    print("숫자를 입력하세요. 완료하려면 빈 줄에서 Enter:")
    numbers = []

    while True:
        line = input()
        if not line:
            break
        try:
            numbers.append(float(line))
        except ValueError:
            print("숫자만 입력 가능합니다.")

    if not numbers:
        print("입력된 숫자가 없습니다.")
        return None

    median_value = statistics.median(numbers)
    print(f"중앙값: {median_value}")
    return median_value


def get_mean() -> float | None:
    print("숫자를 입력하세요. 완료하려면 빈 줄에서 Enter:")
    numbers = []

    while True:
        line = input()
        if not line:
            break
        try:
            numbers.append(float(line))
        except ValueError:
            print("숫자만 입력 가능합니다.")

    if not numbers:
        print("입력된 숫자가 없습니다.")
        return None

    mean_value = statistics.mean(numbers)
    print(f"평균값: {mean_value}")
    return mean_value


def get_kgco2eq_KRW(
    median_emission_per_kg: float,
    weight_kg: float,
    price_krw: float,
) -> float:
    if weight_kg <= 0 or price_krw <= 0:
        raise ValueError("무게와 가격은 0보다 커야 합니다.")

    price_per_kg = price_krw / weight_kg
    emission_per_krw = median_emission_per_kg / price_per_kg

    print(f"1원당 탄소배출량: {emission_per_krw:.8f} kgCO2eq/KRW")
    return emission_per_krw


def get_median_kgco2eq_kg_to_xlsx() -> float | None:
    excel_path = "data/AGRIBALYSE_탄소배출량_정렬완료.xlsx"

    try:
        df = pd.read_excel(excel_path)
    except FileNotFoundError:
        print(f"[오류] 파일 없음: {excel_path}")
        return None

    food_dict = dict(zip(df["식품명"], df["탄소배출량 (kg CO2 eq/kg)"]))

    def clean_input(line: str) -> str:
        for sep in [" - ", " -", "- "]:
            if sep in line:
                line = line.split(sep)[0]
        return line.strip()

    def lookup(food_name: str) -> float | None:
        if food_name in food_dict:
            return food_dict[food_name]

        normalized = " ".join(food_name.split())
        return food_dict.get(normalized)

    print("식품명을 한 줄씩 입력하세요. 빈 줄에서 Enter로 종료:")

    values: list[float] = []
    matched: list[tuple[str, float]] = []
    missing: list[str] = []

    while True:
        try:
            line = input()
        except EOFError:
            break

        if not line.strip():
            break

        food_name = clean_input(line)
        if not food_name:
            continue

        co2 = lookup(food_name)

        if co2 is not None:
            values.append(co2)
            matched.append((food_name, co2))
        else:
            missing.append(food_name)

    print(f"\n매칭 성공: {len(matched)}개 / 미매칭: {len(missing)}개")

    if matched:
        print("\n[매칭된 항목 및 탄소배출량]")
        for name, co2 in matched:
            print(f"  {name:<45} {co2:.4f} kg CO2 eq/kg")

    if missing:
        print("\n[DB에서 찾지 못한 항목]")
        for name in missing:
            print(f"  {name}")

    if not values:
        print("매칭된 항목이 없어 중앙값을 계산할 수 없습니다.")
        return None

    median_val = statistics.median(values)
    print(f"\n중앙값: {median_val:.8f} kg CO2 eq/kg")
    return median_val