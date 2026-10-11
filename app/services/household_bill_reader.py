"""Gemini Vision 관리비 고지서 판독 및 검증.

인증은 최근 13개월 제한, 일반 탄소 분석은 과거 고지서도 허용한다.
난방의 원본 사용량/단위를 보존하며, 열에너지 단위만 Gcal로 환산한다.
"""
import hashlib
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from pydantic import BaseModel, Field

from app.services.challenge_verifier import (
    BadImages, VerifierUnavailable,
    _get_client as get_vision_client, llm_enabled, prepare_images,
)

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
MODEL = "gemini-2.5-flash"
MAX_BILL_IMAGE_SIDE = 2000
MAX_MONTHS_BACK = 13
LIMITS = {
    "electricity_kwh": 3000.0, "electricity_krw": 2_000_000,
    "water_m3": 300.0, "water_krw": 1_000_000,
    "gas_m3": 1000.0, "gas_krw": 2_000_000,
    "heat_gcal": 40.0, "heat_krw": 2_000_000,
}
VALUE_KEYS = tuple(LIMITS)
HEAT_RAW_MAX = 1_000_000.0
# 1 Gcal = 1000 Mcal = 4.1868 GJ = 4186.8 MJ ≈ 1163 kWh.
# 국제 열화학 칼로리(1 cal = 4.184 J)와의 차이를 피하기 위해
# 여기서는 기존 에너지 공학에서 흔히 쓰는 1 kcal = 4.1868 kJ 기준을 명시한다.
HEAT_GCAL_DIVISORS = {
    "Gcal": 1.0,
    "Mcal": 1000.0,
    "GJ": 4.1868,
    "MJ": 4186.8,
    "kWh": 4186.8 / 3.6,
    "MWh": 4.1868 / 3.6,
}


class UtilityReading(BaseModel):
    usage: Optional[float] = Field(default=None, description="고지서에 인쇄된 사용량. 난방은 인쇄 단위 그대로")
    unit: Optional[str] = Field(default=None, description="난방의 인쇄 단위. 다른 항목은 null")
    krw: Optional[int] = Field(default=None, description="항목 금액(원)")


class BillReading(BaseModel):
    is_utility_bill: bool = Field(description="관리비/공과금 고지서 여부")
    readable: bool = Field(description="숫자 판독 가능 여부")
    bill_month: Optional[str] = Field(default=None, description="사용(검침) 월 YYYY-MM")
    electricity: UtilityReading = Field(default_factory=UtilityReading)
    water: UtilityReading = Field(default_factory=UtilityReading)
    gas: UtilityReading = Field(default_factory=UtilityReading)
    heat: UtilityReading = Field(default_factory=UtilityReading)
    total_krw: Optional[int] = None
    looks_edited: bool = False
    notes: Optional[str] = None


def build_prompt(today: date, count: int) -> str:
    return f"""너는 한국의 아파트 관리비 고지서/명세서와 전기·수도·도시가스·지역난방 요금 고지서 판독기다.
사진 {count}장이 주어진다. 모두 같은 가구·같은 달의 고지서(앞뒤 장, 또는 전기/가스/수도 고지서 따로)이므로 내용을 합쳐서 한 번에 읽는다.
사진에 실제로 인쇄된 숫자만 읽고 보이지 않는 값은 null로 둔다. 추측하거나 계산해서 채우지 않는다. 오늘 날짜는 {today.isoformat()}이다.
이름·주소·전화번호·계좌번호·고객번호 같은 개인정보는 읽지도 출력하지도 않는다.

읽을 항목:
1. is_utility_bill: 공과금/관리비 고지서가 맞으면 true, 영수증·사진·무관한 문서이면 false.
2. readable: 숫자가 선명하게 읽히면 true.
3. bill_month: 사용(검침)한 달 YYYY-MM. 사용 기간이 적혀 있으면 그 기간 끝이 속한 달, 없으면 청구월.
4. electricity: 전기 사용량(kWh)과 전기요금(원). 한전 고지서에는 '사용량 kWh'와 '청구금액'이 있다. 아파트 관리비 명세서의 '전기료'/'세대전기료'는 금액이다.
5. water: 수도 사용량(m3 또는 톤)과 수도요금(원). 명세서의 '수도료'/'급수비'.
6. gas: 도시가스 사용량(m3)과 가스요금(원). 명세서의 '가스사용료'. 난방용 지역난방이 아니라 도시가스만.
7. heat: 지역난방의 세대 난방 사용량과 세대난방비(원). 고지서에 '난방 1.07m³ 104,860'이라고 표시되면 heat.usage=1.07, heat.unit='m3', heat.krw=104860으로 추출한다.
   난방 사용량 단위가 Gcal이 아니더라도 원본 숫자와 단위를 그대로 읽는다. 단위 예: Gcal, Mcal, GJ, MJ, kWh, MWh, m³, 톤.
   열량 환산이나 탄소배출량 계산은 하지 않는다. 단위가 보이지 않으면 unit=null.
   '난방 1.07m³'와 '온수 7m³'는 다른 항목이다. 온수·급탕 사용량을 난방 사용량으로 읽지 않는다.
   세대난방비에 공동난방비·공용난방비·기본열요금·급탕비를 합산하지 않는다.
   도시가스 사용량을 지역난방 사용량으로 읽지 않는다. 보이지 않는 사용량을 추측하지 않는다.
   공용 부분(공용전기료·공동수도료·공동난방비 등)은 세대 사용량이 아니므로 읽지 않는다.
8. total_krw: 이번 달 총 청구 금액(원).
9. looks_edited: 숫자·글자를 지우거나 덧쓴 흔적, 합성의 뚜렷한 흔적이 있으면 true. 확실할 때만 true.
notes: 읽기 어려웠던 점이 있으면 짧게(없으면 null).

JSON 하나만 출력한다."""


def analyze_bill(images: list[bytes], today: date) -> BillReading:
    """Gemini 비전으로 고지서를 읽는다. 실패하면 VerifierUnavailable."""
    from google.genai import types
    try:
        parts = [types.Part.from_bytes(data=data, mime_type="image/jpeg") for data in images]
        response = get_vision_client().models.generate_content(
            model=MODEL,
            contents=[*parts, build_prompt(today, len(images))],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BillReading,
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return BillReading.model_validate_json(response.text or "")
    except Exception as exc:
        logger.warning("고지서 판독 실패: %s: %s", type(exc).__name__, str(exc)[:150])
        raise VerifierUnavailable(type(exc).__name__) from exc


def month_in_range(month: str, today: date) -> bool:
    """YYYY-MM이 현재 월부터 MAX_MONTHS_BACK개월 전 사이인지 검사."""
    try:
        year, mon = month.split("-")
        target = date(int(year), int(mon), 1)
    except (ValueError, AttributeError):
        return False
    current = date(today.year, today.month, 1)
    oldest_year = current.year + (current.month - 1 - MAX_MONTHS_BACK) // 12
    oldest_month = (current.month - 1 - MAX_MONTHS_BACK) % 12 + 1
    return date(oldest_year, oldest_month, 1) <= target <= current


def valid_month(month: str) -> bool:
    """과거 분석 허용 시에도 월 형식 자체는 검증한다."""
    if not isinstance(month, str) or len(month) != 7:
        return False
    try:
        datetime.strptime(month, "%Y-%m")
        return True
    except ValueError:
        return False


def normalize_heat_unit(unit: str | None) -> str | None:
    """난방 단위를 표준 표기로 정규화한다. 미확인 단위는 원문을 보존한다."""
    if not unit or not unit.strip():
        return None
    raw = unit.strip().replace(" ", "").replace("㎥", "m3").replace("³", "3")
    aliases = {
        "m3": "m3", "M3": "m3", "㎥": "m3",
        "톤": "ton", "t": "ton", "ton": "ton", "TON": "ton",
        "gcal": "Gcal", "mcal": "Mcal", "gj": "GJ", "mj": "MJ",
        "kwh": "kWh", "mwh": "MWh",
    }
    return aliases.get(raw, aliases.get(raw.lower(), raw))


def convert_heat_to_gcal(usage: float | None, unit: str | None) -> float | None:
    """열에너지 단위만 Gcal로 변환한다. m3·ton은 임의 환산하지 않는다."""
    if usage is None or unit not in HEAT_GCAL_DIVISORS:
        return None
    try:
        number = float(usage)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number / HEAT_GCAL_DIVISORS[unit]


def flatten_values(reading: BillReading) -> dict:
    heat_unit = normalize_heat_unit(reading.heat.unit)
    return {
        "electricity_kwh": reading.electricity.usage,
        "electricity_krw": reading.electricity.krw,
        "water_m3": reading.water.usage,
        "water_krw": reading.water.krw,
        "gas_m3": reading.gas.usage,
        "gas_krw": reading.gas.krw,
        "heat_usage": reading.heat.usage,
        "heat_unit": heat_unit,
        "heat_gcal": convert_heat_to_gcal(reading.heat.usage, heat_unit),
        "heat_krw": reading.heat.krw,
    }


def clean_values(values: dict) -> tuple[dict, list[str]]:
    cleaned, dropped = {}, []
    for key in VALUE_KEYS:
        value = values.get(key)
        if value is None:
            cleaned[key] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            cleaned[key] = None
            dropped.append(key)
            continue
        if not math.isfinite(number) or number < 0 or number > LIMITS[key]:
            cleaned[key] = None
            dropped.append(key)
        elif key.endswith("gcal"):
            cleaned[key] = round(number, 4)
        elif key.endswith(("kwh", "m3")):
            cleaned[key] = round(number, 2)
        else:
            cleaned[key] = int(round(number))

    cleaned["heat_usage"] = None
    cleaned["heat_unit"] = None
    raw_usage = values.get("heat_usage")
    raw_unit = values.get("heat_unit")
    if raw_usage is not None:
        try:
            number = float(raw_usage)
            if math.isfinite(number) and 0 <= number <= HEAT_RAW_MAX:
                cleaned["heat_usage"] = round(number, 4)
                cleaned["heat_unit"] = raw_unit
            else:
                dropped.append("heat_usage")
        except (TypeError, ValueError, OverflowError):
            dropped.append("heat_usage")
    return cleaned, dropped


def make_fingerprint(month: str, values: dict) -> str | None:
    """월과 읽은 값 기반의 중복 판별 보조 해시(단독으로 중복 방지 보장 불가)."""
    fingerprint_keys = (*VALUE_KEYS, "heat_usage", "heat_unit")
    parts = [f"{key}={values.get(key)}" for key in fingerprint_keys if values.get(key) is not None]
    if not parts:
        return None
    return hashlib.sha256((month + "|" + "|".join(parts)).encode("utf-8")).hexdigest()[:40]


def _fail(code: str, message: str, **extra) -> dict:
    return {
        "readable": False, "code": code, "message": message,
        "bill_month": None, "values": None, "total_krw": None,
        "fingerprint": None, "note": None, **extra,
    }


def judge_bill(reading: BillReading, today: date, enforce_month_range: bool = True) -> dict:
    """인증은 월 제한을 적용하고, 탄소 분석은 과거 월을 허용한다."""
    if not reading.is_utility_bill:
        return _fail("NOT_A_BILL", "관리비·전기·수도·가스 고지서가 아닌 것 같아요. 고지서가 잘 보이게 다시 찍어 주세요.")
    if not reading.readable:
        return _fail("UNREADABLE", "고지서 숫자가 잘 안 보여요. 밝은 곳에서 가까이 다시 찍어 주세요.")
    if reading.looks_edited:
        return _fail("EDITED", "편집된 것으로 보이는 고지서는 판독 결과를 신뢰하기 어려워요. 원본 사진을 올려 주세요.")
    if not reading.bill_month or not valid_month(reading.bill_month):
        return _fail("MONTH_UNKNOWN", "고지서의 사용 월을 정확히 읽지 못했어요. 월을 확인해 주세요.")
    if enforce_month_range and not month_in_range(reading.bill_month, today):
        return _fail("MONTH_OUT_OF_RANGE", f"최근 {MAX_MONTHS_BACK}개월 안의 고지서만 인증할 수 있어요.", bill_month=reading.bill_month)

    values, dropped = clean_values(flatten_values(reading))
    if all(values[key] is None for key in (*VALUE_KEYS, "heat_usage")):
        message = "읽은 값이 비정상적이에요. 직접 입력해 주세요." if dropped else "전기·수도·가스·난방 사용량이나 금액을 찾지 못했어요."
        return _fail("NO_VALUES", message, bill_month=reading.bill_month)

    return {
        "readable": True, "code": "OK",
        "message": "고지서에서 값을 읽었어요. 맞는지 확인하고 저장해 주세요.",
        "bill_month": reading.bill_month,
        "values": values,
        "total_krw": reading.total_krw,
        "fingerprint": make_fingerprint(reading.bill_month, values),
        "note": "일부 값이 비정상적이라 제외했어요. 확인해 주세요." if dropped else None,
    }


def read_bill(
    images: list[bytes],
    today: date | None = None,
    analyzer: Callable[[list[bytes], date], BillReading] | None = None,
    enforce_month_range: bool = True,
) -> dict:
    """고지서 판독. 기본은 인증용(월 제한), 분석은 enforce_month_range=False."""
    if not llm_enabled() and analyzer is None:
        raise VerifierUnavailable("DISABLED")
    prepared = prepare_images(images, max_side=MAX_BILL_IMAGE_SIDE)
    today = today or datetime.now(KST).date()
    reading = (analyzer or analyze_bill)(prepared, today)
    return judge_bill(reading, today, enforce_month_range=enforce_month_range)
