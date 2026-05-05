# 상호명 정규화 등 유틸리티
import re
import statistics
import pandas as pd

# 상호명 정규화 함수 코드
# 이전에 상호명 정규화를 시도했지만 완벽히 걸러내기 어려워서 사용하지 않음 
# 하지만 코드는 남겨둠
def normalize_merchant_name(name: str) -> str:
    "검색용 상호명 정규화"
    name = str(name).strip()
    if not name or name == 'nan':
        return ""

    name = re.sub(r'\(.*?\)', '', name).strip()
    name = re.sub(
        r'[\s\-_]*(점|호점|지점|지사|본점|매장|센터|타워|플라자|몰)$',
        '', name, flags=re.IGNORECASE
    ).strip()
    name = re.sub(
        r'[\s]*(서울|부산|대구|인천|광주|대전|울산|세종|제주|수원|'
        r'강남|강북|강서|강동|홍대|신촌|잠실|건대|역삼|논현|'
        r'판교|성수|여의도|종로|명동|이태원|신림|구로|영등포|상암).+$',
        '', name
    ).strip()

    name = re.sub(r'[\s]*[A-Z]{1,3}$', '', name).strip()
    name = re.sub(r'[\s]*\d+$', '', name).strip()

    return name if name else ""

# 주유/이동 관련 탄소 배출량을 계산하는 함수 코드
def traffic_fueled_carbon_emissions(gross_calorific_value, net_calorific_value,co2_factor_tC_per_TJ,
                                    ch4_factor_kg_per_TJ, n2o_factor_kg_per_TJ, gwp_ch4, gwp_n2o, fuel_price_per_L):
    # 1. C → CO2 변환
    co2_factor_tCO2_per_TJ = co2_factor_tC_per_TJ * (44 / 12)

    # 2. t → kg 변환
    co2_factor_kg_per_TJ = co2_factor_tCO2_per_TJ * 1000

    # 3. CH4, N2O → CO2eq 변환
    ch4_co2eq = ch4_factor_kg_per_TJ * gwp_ch4
    n2o_co2eq = n2o_factor_kg_per_TJ * gwp_n2o

    # 총 CO2eq (kg/TJ)
    total_co2eq_per_TJ = co2_factor_kg_per_TJ + ch4_co2eq + n2o_co2eq

    # 4. TJ → MJ 변환
    total_co2eq_per_MJ = total_co2eq_per_TJ / 1_000_000

    # 5. MJ → L 변환 (순발열량 사용)
    co2eq_per_L = total_co2eq_per_MJ * net_calorific_value

    # 6. 원당 탄소배출량
    co2eq_per_KRW = co2eq_per_L / fuel_price_per_L

    # 결과 출력
    print("=== 결과 ===")
    print(f"CO2 배출계수: {co2_factor_kg_per_TJ:.2f} kgCO2/TJ")
    print(f"총 배출계수: {total_co2eq_per_TJ:.2f} kgCO2eq/TJ")
    print(f"단위 에너지 기준: {total_co2eq_per_MJ:.8f} kgCO2eq/MJ")
    print(f"리터 기준: {co2eq_per_L:.6f} kgCO2eq/L")
    print(f"원당 배출량: {co2eq_per_KRW:.10f} kgCO2eq/KRW")

# AGRIBALYSE kgCO2eq/kg -> kgCO2eq/KRW 로 정제하는 과정에서 사용한 코드
# 세부 카테고리에 해당하는 모든 식품명의 데이터에 대한 중앙값을 구하는 코드
# 데이터 정제 용도 외에는 사용하지 않음
def get_median():
    print("숫자를 입력하세요. 입력을 마치려면 빈 줄에서 Enter를 누르세요:")

    numbers = []

    while True:
        line = input()
        if line == "":
            break
        try:
            numbers.append(float(line))
        except ValueError:
            print("숫자만 입력 가능합니다.")

    if numbers:
        # statistics 모듈을 사용해 중간값 계산
        median_value = statistics.median(numbers)
        print(f"\n입력한 값의 중간값: {median_value}")

        # 참고를 위해 정렬된 리스트도 출력해볼 수 있습니다.
        # print(f"정렬된 데이터: {sorted(numbers)}")
    else:
        print("입력된 숫자가 없습니다.")

    return median_value
# AGRIBALYSE kgCO2eq/kg -> kgCO2eq/KRW 로 정제하는 과정에서 사용한 코드
# 세부 카테고리에 해당하는 모든 식품명의 데이터에 대한 평균값을 구하는 코드
# 데이터 정제 용도 외에는 사용하지 않음

def get_mean():
    print("숫자를 입력하세요. 입력을 마치려면 빈 줄에서 Enter를 누르세요:")

    numbers = []

    while True:
        line = input()
        if line == "":
            break
        try:
            numbers.append(float(line))
        except ValueError:
            print("숫자만 입력 가능합니다.")

    if numbers:
        # statistics 모듈을 사용해 평균값 계산
        mean_value = statistics.mean(numbers)
        print(f"\n입력한 값의 평균값: {mean_value}")

        # 참고: 소수점 자릿수를 제한하고 싶다면 아래와 같이 round() 함수를 사용할 수 있습니다.
        # print(f"입력한 값의 평균값: {round(mean_value, 2)}")
    else:
        print("입력된 숫자가 없습니다.")
        return None  # 숫자가 없을 때는 None을 반환하도록 처리

    return mean_value

# AGRIBALYSE kgCO2eq/kg -> kgCO2eq/KRW 로 정제하는 과정에서 사용한 코드
# 카테고리 식품명들의 중앙값과 (kgCo2eq/kg) 그 식품명들의 각각의 kg/KRW 에 대한 중앙값을 계산 
# 데이터 정제 용도 외에는 사용하지 않음

def get_kgco2eq_KRW(median_emission_per_kg, weight_kg, price_krw):
    """
    특정 카테고리의 1원당 탄소배출량을 계산하는 함수

    매개변수:
    - median_emission_per_kg (float): 카테고리의 1kg당 평균/중간 탄소배출량 (kgCO2eq/kg)
    - weight_kg (float): 기준이 되는 상품의 무게 (kg)
    - price_krw (float): 해당 무게에 대한 상품의 가격 (원, KRW)

    반환값:
    - float: 1원당 발생하는 탄소배출량 (kgCO2eq/KRW)
    """

    # 0으로 나누는 오류 및 잘못된 값 입력 방지
    if weight_kg <= 0 or price_krw <= 0:
        raise ValueError("무게와 가격은 0보다 커야 합니다.")

    # 1. 1kg당 가격 산출
    # 예: 0.5kg에 500원이면, 1kg당 1000원
    price_per_1kg = price_krw / weight_kg

    # 2. 1원당 탄소 배출량 산출
    # 예: 1.4 kgCO2eq/kg / 1000 원/kg = 0.0014 kgCO2eq/KRW
    emission_per_krw = median_emission_per_kg / price_per_1kg
    print(f"카테고리 중간값: {median_emission_per_kg} kgCO2eq/kg")
    print(f"기준 무게: {weight_kg} kg, 기준 가격: {price_krw} 원")
    print("-" * 40)
    print(f"산출된 1원당 탄소배출량: {emission_per_krw:.8f} kgCO2eq/KRW")

# AGRIBALYSE kgCO2eq/kg -> kgCO2eq/KRW 로 정제하는 과정에서 사용한 코드
# xlsx 파일을 읽어와 세부 카테고리의 식품명과 대치되는 kgCo2eq/kg 을 가져와서 중앙값을 구하는 코드
# 데이터 정제 용도 외에는 사용하지 않음
def get_median_kgco2eq_kg_to_xlsx():
    EXCEL_PATH = "data/AGRIBALYSE_탄소배출량_정렬완료.xlsx"

    try:
        df = pd.read_excel(EXCEL_PATH)
    except FileNotFoundError:
        print(f"[오류] 파일을 찾을 수 없습니다: {EXCEL_PATH}")
        print("스크립트와 같은 디렉터리에 xlsx 파일을 두거나 경로를 수정하세요.")
        exit(1)

    # 식품명 → 탄소배출량 딕셔너리 (빠른 조회용)
    food_dict: dict[str, float] = dict(
        zip(df["식품명"], df["탄소배출량 (kg CO2 eq/kg)"])
    )

    # ── 입력 처리 함수 ───────────────────────────────────────────
    def clean_input(line: str) -> str:
        """
        '4 치즈 피자 -피자' → '4 치즈 피자'
        ' - 피자' 또는 '-피자' 형태의 suffix를 제거한 뒤 공백 정리
        """
        for sep in [" - ", " -", "- "]:
            if sep in line:
                line = line.split(sep)[0]
        return line.strip()

    def lookup(food_name: str) -> float | None:
        """
        1단계: 정확히 일치하는 항목 조회
        2단계: 정확 일치 실패 시 공백 정규화 후 재조회
        """
        if food_name in food_dict:
            return food_dict[food_name]
        # 연속 공백 → 단일 공백 정규화
        normalized = " ".join(food_name.split())
        return food_dict.get(normalized, None)

    # ── 메인 루프 ────────────────────────────────────────────────
    print("=" * 60)
    print("  탄소배출량 중간값 계산기 (AGRIBALYSE DB 기반)")
    print("=" * 60)
    print("식품명을 한 줄에 하나씩 입력하세요.")
    print("빈 줄(엔터)을 입력하면 입력이 종료됩니다.\n")

    values: list[float] = []
    matched: list[tuple[str, float]] = []
    missing: list[str] = []

    while True:
        try:
            line = input()
        except EOFError:
            break

        # 빈 줄 → 입력 종료
        if line.strip() == "":
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

    # ── 결과 출력 ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  입력 항목: {len(matched) + len(missing)}개  |  매칭 성공: {len(matched)}개  |  미매칭: {len(missing)}개")
    print("=" * 60)

    if matched:
        print("\n[매칭된 항목 및 탄소배출량]")
        for name, co2 in matched:
            print(f"  {name:<45}  {co2:.4f} kg CO2 eq/kg")

    if missing:
        print("\n[DB에서 찾지 못한 항목]")
        for name in missing:
            print(f"  ✗ {name}")

    if values:
        median_val = statistics.median(values)
        mean_val = statistics.mean(values)
        min_val = min(values)
        max_val = max(values)

        print("\n" + "=" * 60)
        print("  탄소배출량 통계 (kg CO2 eq/kg)")
        print("=" * 60)
        print(f"  항목 수  : {len(values)}개")
        print(f"  중간값   : {median_val:.8f}")
        print(f"  평균값   : {mean_val:.8f}")
        print(f"  최솟값   : {min_val:.8f}")
        print(f"  최댓값   : {max_val:.8f}")
        print("=" * 60)
        print(f"\n  ✔ 최종 중간값(median) = {median_val:.8f} kg CO2 eq/kg\n")

        # 탄소배출량 배열 (오름차순 정렬)
        sorted_values = sorted(values)
        print(f"  탄소배출량 배열 (정렬): {[round(v, 6) for v in sorted_values]}\n")
    else:
        print("\n매칭된 항목이 없어 중간값을 계산할 수 없습니다.")
    return median_val