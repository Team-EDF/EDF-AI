"""
데이터 전처리 유틸리티 모음.
scripts/ 에서 소상공인 CSV 적재 및 AGRIBALYSE 데이터 정제 시 사용.
서비스 레이어(services/)에서는 사용하지 않음.
"""
import re
import statistics
import pandas as pd


def normalize_merchant_name(name: str) -> str:
    """
    상호명에서 지점 접미사·지역명·숫자·영문 호점 번호를 제거해 핵심 상호명만 반환.
    현재 서비스에서 사용하지 않음 — 정규화 범위가 너무 넓어 오분류 발생 가능.
    """
    name = str(name).strip()
    if not name or name == "nan":
        return ""

    name = re.sub(r"\(.*?\)", "", name).strip()
    name = re.sub(
        r"[\s\-_]*(점|호점|지점|지사|본점|매장|센터|타워|플라자|몰)$",
        "", name, flags=re.IGNORECASE,
    ).strip()
    name = re.sub(
        r"[\s]*(서울|부산|대구|인천|광주|대전|울산|세종|제주|수원|"
        r"강남|강북|강서|강동|홍대|신촌|잠실|건대|역삼|논현|"
        r"판교|성수|여의도|종로|명동|이태원|신림|구로|영등포|상암).+$",
        "", name,
    ).strip()
    name = re.sub(r"[\s]*[A-Z]{1,3}$", "", name).strip()
    name = re.sub(r"[\s]*\d+$", "", name).strip()

    return name if name else ""


def normalize_region_name(address: str) -> str:
    """
    주소 첫 단어(시/도 정식 명칭)를 2글자 약칭으로 변환.
    SBO CSV 적재 시 road_address 정규화에 사용.
    예: '경기도 용인시...' → '경기 용인시...'
    """
    if not address or pd.isna(address):
        return ""

    region_mapping = {
        "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
        "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
        "울산광역시": "울산", "세종특별자치시": "세종", "세종시": "세종",
        "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원",
        "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북",
        "전라북도": "전북", "전라남도": "전남", "경상북도": "경북",
        "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주",
    }

    parts = str(address).strip().split()
    if parts and parts[0] in region_mapping:
        parts[0] = region_mapping[parts[0]]

    return "".join(parts)


def preprocess_merchant_name(name, branch) -> str:
    """
    상호명 + 지점명 병합 후 대문자 변환 및 공백 제거.
    SBO CSV 적재 시 original_name 전처리에 사용.
    예: name='gs 25', branch='강남점' → 'GS25강남점'
    """
    name_str = str(name).strip() if pd.notna(name) else ""
    branch_str = str(branch).strip() if pd.notna(branch) else ""

    combined = name_str + branch_str if (branch_str and branch_str.lower() != "nan") else name_str
    return combined.upper().replace(" ", "")


# ── AGRIBALYSE 데이터 정제 도구 (DB 구축 완료 후 재실행 불필요) ─────────────

def get_median() -> float | None:
    """
    표준 입력으로 숫자를 받아 중앙값 반환.
    AGRIBALYSE kgCO2eq/kg 값들의 중앙값 산출 시 사용.
    """
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
    """
    표준 입력으로 숫자를 받아 평균값 반환.
    AGRIBALYSE kgCO2eq/kg 값들의 평균 산출 시 사용.
    """
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


def get_kgco2eq_KRW(median_emission_per_kg: float, weight_kg: float, price_krw: float) -> float:
    """
    AGRIBALYSE 중앙값(kgCO2eq/kg)과 기준 상품 무게·가격으로 1원당 탄소배출량 산출.
    반환값: kgCO2eq/KRW
    """
    if weight_kg <= 0 or price_krw <= 0:
        raise ValueError("무게와 가격은 0보다 커야 합니다.")

    price_per_kg = price_krw / weight_kg
    emission_per_krw = median_emission_per_kg / price_per_kg
    print(f"중앙값: {median_emission_per_kg} kgCO2eq/kg")
    print(f"기준 무게: {weight_kg} kg, 기준 가격: {price_krw} 원")
    print(f"1원당 탄소배출량: {emission_per_krw:.8f} kgCO2eq/KRW")
    return emission_per_krw


def get_median_kgco2eq_kg_to_xlsx() -> float | None:
    """
    AGRIBALYSE_탄소배출량_정렬완료.xlsx를 읽어 입력된 식품명들의 탄소배출량 중앙값 반환.
    세부 카테고리별 co2eq_KRW 산출 시 사용.
    """
    EXCEL_PATH = "data/AGRIBALYSE_탄소배출량_정렬완료.xlsx"
    try:
        df = pd.read_excel(EXCEL_PATH)
    except FileNotFoundError:
        print(f"[오류] 파일 없음: {EXCEL_PATH}")
        return None

    food_dict: dict[str, float] = dict(zip(df["식품명"], df["탄소배출량 (kg CO2 eq/kg)"]))

    def clean_input(line: str) -> str:
        for sep in [" - ", " -", "- "]:
            if sep in line:
                line = line.split(sep)[0]
        return line.strip()

    def lookup(food_name: str) -> float | None:
        if food_name in food_dict:
            return food_dict[food_name]
        return food_dict.get(" ".join(food_name.split()), None)

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
        for name, co2 in matched:
            print(f"  {name:<45} {co2:.4f} kg CO2 eq/kg")
    if missing:
        print("미매칭 항목:", missing)

    if not values:
        print("매칭된 항목이 없어 중앙값을 계산할 수 없습니다.")
        return None

    median_val = statistics.median(values)
    print(f"\n중앙값: {median_val:.8f} kg CO2 eq/kg")
    return median_val
