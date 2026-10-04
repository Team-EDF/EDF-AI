"""
가정 에너지(관리비) 월간 탄소 계산: 전기, 수도, 도시가스, 지역난방.

우선순위
1) 사용량이 있으면 "사용량 x 배출계수" (정확)
2) 사용량이 없고 금액만 있으면 "금액 x 원당 탄소계수" (추정). 원당 계수는 DB의 가정에너지 중분류
   (전기비/수도비/도시가스비)에 이미 들어 있는 값을 쓰고, DB를 못 읽으면 같은 값의 상수를 쓴다.

배출계수 출처와 한계 (2026-10 조사, 근거는 GreenAction_household_evidence.md)
- 전기 0.4173 kgCO2eq/kWh: 기후에너지환경부가 확정한 2023년 전력배출계수(0.4173 tCO2eq/MWh). 정부는 갱신 주기를
  3년에서 1년으로 줄였으므로 매년 바뀐다. (탄소포인트제 지자체 안내는 0.424, 탄소발자국 계산기는 0.4781)
- 도시가스 2.176 kgCO2eq/m3: 한국환경공단 탄소발자국 계산기(kcen.kr) 계수. 비교: 탄소포인트제 지자체 안내 2.240,
  EG-TIPS 값으로 직접 계산 15.236 tC/TJ x 44/12 x 순발열량 38.5MJ/Nm3 = 2.15 -> 자료마다 2.15~2.24 (약 +-3%)
- 수도 0.237 kgCO2eq/m3: 한국환경공단 탄소발자국 계산기 계수. (탄소포인트제 지자체 안내는 0.332) 교차검증: 광역상수도
  전력원단위 2025년 0.3831 kWh/m3(취수·정수·가압 전력만) x 전력배출계수 0.4173 = 0.160 이라 0.237 이 과대하지 않다.
  0.160 은 지방 배수·가압과 하수 처리 에너지가 빠진 하한에 가까운 값이다.
- 지역난방 146.9 kgCO2eq/Gcal: 한국지역난방공사가 "지역난방용 열 사용 온실가스 산정"용으로 공개한 열 생산량 기준 배출계수
  (수도권 연계지사 2024년 4기: CO2 35,058 kg/TJ, CH4 0.634 kg/TJ, N2O 0.064 kg/TJ). GWP 28/265 를 적용해 합산하고
  1 Gcal = 0.0041868 TJ 로 환산: (35058 + 0.634x28 + 0.064x265) x 0.0041868 = 146.9. 지사마다 달라서(청주 약 237,
  대구 약 202, 세종 약 179) 수도권 값을 기본으로 쓴다. 앱은 지역을 모르므로 지방 거주자는 오차가 있다.
  (이전 값 122.6 은 출처가 불분명한 고정값이라 교체함)
"""
import logging

logger = logging.getLogger(__name__)

# 사용량 기준 배출계수 (kgCO2eq / 단위)
FACTORS = {
    "electricity": {"label": "전기", "unit": "kWh", "factor": 0.4173, "source": "2023 전력배출계수"},
    "water": {"label": "수도", "unit": "m3", "factor": 0.237, "source": "한국환경공단 탄소발자국 계산기 계수"},
    "gas": {"label": "도시가스", "unit": "m3", "factor": 2.176, "source": "가정용 탄소발자국 계산기 계수"},
    "heat": {"label": "지역난방", "unit": "Gcal", "factor": 146.9, "source": "한국지역난방공사 수도권 연계지사 배출계수(2024)"},
}
UTILITY_KEYS = tuple(FACTORS)

# 금액만 있을 때 쓰는 원당 계수 (kgCO2eq / 원). DB 가정에너지 중분류 값과 같다 (DB를 못 읽을 때의 대체값).
SPEND_FACTOR_FALLBACK = {
    "electricity": 0.0025380370,   # 전기비
    "water": 0.0001928440,         # 수도비
    "gas": 0.0026474220,           # 도시가스비
    "heat": 0.0026474220,          # 지역난방은 따로 없어 도시가스비 계수로 근사 (추정임을 표시)
}
_SPEND_DB_NAMES = {"electricity": "전기비", "water": "수도비", "gas": "도시가스비"}

_spend_cache: dict | None = None


def get_spend_factors() -> dict:
    """원당 탄소계수를 DB(가정에너지 중분류)에서 읽어 캐시한다. 못 읽으면 상수."""
    global _spend_cache
    if _spend_cache is not None:
        return _spend_cache
    factors = dict(SPEND_FACTOR_FALLBACK)
    try:
        from app.database.connection import get_db_connection

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mc.middle_name, mc.co2eq_krw
                FROM middle_category mc
                JOIN main_category m ON m.main_category_id = mc.main_category_id
                WHERE m.main_name = '가정에너지'
                """
            )
            rows = {name: float(value) for name, value in cursor.fetchall() if value is not None}
            cursor.close()
        finally:
            conn.close()
        for key, db_name in _SPEND_DB_NAMES.items():
            if db_name in rows:
                factors[key] = rows[db_name]
        factors["heat"] = factors["gas"]
    except Exception as e:  # noqa: BLE001
        logger.warning("원당 탄소계수를 DB에서 읽지 못해 상수를 씁니다: %s: %s", type(e).__name__, str(e)[:100])
    _spend_cache = factors
    return factors


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def calculate_household_carbon(bill: dict, spend_factors: dict | None = None) -> dict:
    """
    한 달 관리비의 탄소를 계산한다.

    bill 예: {"electricity_kwh": 300, "electricity_krw": 52000, "water_m3": 12, "water_krw": 9000,
              "gas_m3": 25, "gas_krw": 30000, "heat_gcal": None, "heat_krw": None}
    반환: {"total_kg", "items": [{key, label, usage, unit, krw, carbon_kg, basis}], "estimated": bool, "note"}
      basis = "usage"(사용량 기준) | "spend"(금액으로 추정) | "none"(값 없음)
    """
    spend = spend_factors or get_spend_factors()
    usage_field = {"electricity": "electricity_kwh", "water": "water_m3", "gas": "gas_m3", "heat": "heat_gcal"}
    items = []
    total = 0.0
    estimated = False

    for key in UTILITY_KEYS:
        info = FACTORS[key]
        usage = _number(bill.get(usage_field[key]))
        krw = _number(bill.get(f"{key}_krw"))
        if usage is not None and usage > 0:
            carbon, basis = usage * info["factor"], "usage"
        elif krw is not None and krw > 0:
            carbon, basis = krw * spend[key], "spend"
            estimated = True
        else:
            carbon, basis = 0.0, "none"
        total += carbon
        items.append({
            "key": key,
            "label": info["label"],
            "usage": usage,
            "unit": info["unit"],
            "krw": int(krw) if krw is not None else None,
            "carbon_kg": round(carbon, 2),
            "basis": basis,
        })

    note = "사용량이 없는 항목은 금액으로 추정한 값이라 오차가 클 수 있어요." if estimated else None
    return {"total_kg": round(total, 2), "items": items, "estimated": estimated, "note": note}
