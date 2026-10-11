"""가정 에너지(관리비) 월간 탄소 계산: 전기, 수도, 도시가스, 지역난방.

사용량이 있으면 사용량 기반, 사용량이 없고 금액이 있으면 금액 기반 추정.
난방 원본 사용량/단위를 보존하며 m3·톤은 근거 없이 열량으로 변환하지 않는다.
배출계수 및 근거: 프로젝트 GreenAction_household_evidence.md (주기적 재검증 필요).
"""

import logging
import math

logger = logging.getLogger(__name__)

FACTORS = {
    "electricity": {"label": "전기", "unit": "kWh", "factor": 0.4173, "source": "2023 전력배출계수"},
    "water": {"label": "수도", "unit": "m3", "factor": 0.237, "source": "한국환경공단 탄소발자국 계산기 계수"},
    "gas": {"label": "도시가스", "unit": "m3", "factor": 2.176, "source": "가정용 탄소발자국 계산기 계수"},
    "heat": {"label": "지역난방", "unit": "Gcal", "factor": 146.9, "source": "한국지역난방공사 수도권 연계지사 배출계수(2024)"},
}
UTILITY_KEYS = tuple(FACTORS)

# 금액 기반 난방 추정: 기본요금·지역·사업자·요금제에 따라 오차가 클 수 있음.
HEAT_TARIFF_KRW_PER_MCAL = 112.32
HEAT_SPEND_FACTOR = FACTORS["heat"]["factor"] / (HEAT_TARIFF_KRW_PER_MCAL * 1000)

SPEND_FACTOR_FALLBACK = {
    "electricity": 0.0025380370,
    "water": 0.0001928440,
    "gas": 0.0026474220,
    "heat": HEAT_SPEND_FACTOR,
}

# 1 Gcal = 1000 Mcal = 1,000,000 kcal = 4.1868 GJ = 4186.8 MJ
# 1 Gcal ≈ 1163 kWh ≈ 1.163 MWh (반올림된 변환 계수).
HEAT_UNIT_TO_GCAL = {
    "gcal": 1.0,
    "mcal": 0.001,
    "kcal": 0.000001,
    "gj": 1 / 4.1868,
    "mj": 1 / 4186.8,   
    "mwh": 3.6 / 4.1868,
    "kwh": 3.6 / 4186.8,
}

_SPEND_DB_NAMES = {"electricity": "전기비", "water": "수도비", "gas": "도시가스비"}
_spend_cache: dict | None = None


def _number(value) -> float | None:
    """0 이상의 유한 숫자만 허용한다. 0은 유효한 측정값이다."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def normalize_heat_unit(unit: str | None) -> str | None:
    """단위 표기를 표준화하되, 모르는 단위는 원본 문자열로 남긴다."""
    if not isinstance(unit, str) or not unit.strip():
        return None
    normalized = (
        unit.strip()
        .replace(" ", "")
        .replace("㎥", "m3")
        .replace("³", "3")
        .replace("㎉", "kcal")
    )
    aliases = {
        "m3": "m3", "ton": "ton", "t": "ton", "톤": "ton",
        "gcal": "Gcal", "mcal": "Mcal", "kcal": "kcal",
        "gj": "GJ", "mj": "MJ", "kwh": "kWh", "mwh": "MWh",
    }
    return aliases.get(normalized.lower(), normalized)


def heat_to_gcal(usage, unit: str | None) -> float | None:
    """열에너지 단위만 Gcal로 변환. m3·톤·미확인 단위는 None."""
    number = _number(usage)
    normalized = normalize_heat_unit(unit)
    if number is None or normalized is None:
        return None
    ratio = HEAT_UNIT_TO_GCAL.get(normalized.lower())
    return None if ratio is None else number * ratio


def get_spend_factors() -> dict:
    """DB의 가정에너지 원당 배출계수를 우선 조회, 실패 시 기본값 사용."""
    global _spend_cache
    if _spend_cache is not None:
        return dict(_spend_cache)

    factors = dict(SPEND_FACTOR_FALLBACK)
    try:
        from app.database.connection import get_db_connection

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT mc.middle_name, mc.co2eq_krw
                    FROM middle_category mc
                    JOIN main_category m ON m.main_category_id = mc.main_category_id
                    WHERE m.main_name = '가정에너지'
                    """
                )
                rows = {name: _number(value) for name, value in cursor.fetchall()}
        finally:
            conn.close()

        for key, db_name in _SPEND_DB_NAMES.items():
            value = rows.get(db_name)
            if value is not None and value > 0:
                factors[key] = value
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "원당 탄소계수를 DB에서 읽지 못해 기본값을 씁니다: %s: %s",
            type(exc).__name__, str(exc)[:100],
        )

    _spend_cache = factors
    return dict(factors)


def clear_spend_factors_cache() -> None:
    global _spend_cache
    _spend_cache = None


def calculate_household_carbon(bill: dict, spend_factors: dict | None = None) -> dict:
    """4개 항목의 월간 탄소배출량 계산.

    heat_gcal이 있으면 사용하고, 없으면 heat_usage + heat_unit에서 열량을 환산한다.
    m3·톤 등의 난방 원본 사용량은 탄소 계산용 열량으로 취급하지 않는다.
    응답 items 스키마는 기존과 동일하게 유지한다.
    """
    spend = get_spend_factors() if spend_factors is None else spend_factors
    usage_field = {
        "electricity": "electricity_kwh",
        "water": "water_m3",
        "gas": "gas_m3",
        "heat": "heat_gcal",
    }
    items = []
    total = 0.0
    estimated = False

    for key in UTILITY_KEYS:
        info = FACTORS[key]
        usage = _number(bill.get(usage_field[key]))
        krw = _number(bill.get(f"{key}_krw"))

        if key == "heat":
            raw_usage = _number(bill.get("heat_usage"))
            raw_unit = normalize_heat_unit(bill.get("heat_unit"))
            if raw_usage is not None and raw_unit is not None:
                # 원본이 존재하면 그 값이 우선이다. 열량 단위가 아니면
                # 이전 단계의 잘못된 heat_gcal 값도 신뢰하지 않는다.
                usage = heat_to_gcal(raw_usage, raw_unit)

        if usage is not None and usage > 0:
            carbon, basis = usage * info["factor"], "usage"
        elif krw is not None and krw > 0:
            spend_factor = _number(spend.get(key))
            if spend_factor is None:
                spend_factor = SPEND_FACTOR_FALLBACK[key]
            carbon, basis = krw * spend_factor, "spend"
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
