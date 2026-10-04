"""
관리비/공과금 고지서 사진 판독 (전기·수도·도시가스·지역난방 사용량과 금액).

Gemini 비전이 사진에서 "보이는 숫자"만 읽고, 이 파일의 규칙이 월 범위·값의 상식적인 범위를 검사한다.
- 개인정보(이름, 주소, 계좌, 고객번호)는 읽지 않도록 하고, 사진은 저장하지 않는다.
- 사진 속 숫자는 앱에서 사용자가 직접 고칠 수 있게 미리 채워 주는 용도다. 고지서로 "인증"된 값(수정하지 않은 값)만
  BE가 절감 포인트 지급 대상으로 인정한다.
- 같은 고지서를 다른 계정에서 또 쓰는 것을 막도록 (월 + 읽은 값) 해시를 지문으로 돌려준다.
"""
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from pydantic import BaseModel, Field

from app.services.challenge_verifier import (
    BadImages,
    VerifierUnavailable,
    _get_client as get_vision_client,
    llm_enabled,
    prepare_images,
)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
MODEL = "gemini-2.5-flash"
MAX_BILL_IMAGE_SIDE = 2000        # 작은 글자가 많은 고지서라 영수증보다 크게 둔다
MAX_MONTHS_BACK = 13              # 최근 13개월 안의 고지서만

# 값의 상식적인 상한 (이 값을 넘으면 잘못 읽은 것으로 보고 버린다)
LIMITS = {
    "electricity_kwh": 3000.0, "electricity_krw": 2_000_000,
    "water_m3": 300.0, "water_krw": 1_000_000,
    "gas_m3": 1000.0, "gas_krw": 2_000_000,
    "heat_gcal": 40.0, "heat_krw": 2_000_000,
}
VALUE_KEYS = tuple(LIMITS)


class UtilityReading(BaseModel):
    usage: Optional[float] = Field(default=None, description="사용량 (전기 kWh, 수도·도시가스 m3, 지역난방 Gcal)")
    krw: Optional[int] = Field(default=None, description="해당 항목 금액(원, 정수)")


class BillReading(BaseModel):
    is_utility_bill: bool = Field(description="아파트 관리비 고지서/명세서 또는 전기·수도·가스·난방 고지서이면 true")
    readable: bool = Field(description="숫자를 읽을 수 있을 만큼 선명하면 true")
    bill_month: Optional[str] = Field(default=None, description="사용(검침) 월 YYYY-MM. 없으면 청구월")
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
4. electricity: 전기 사용량(kWh)과 전기요금(원). 한전 고지서에는 "사용량 kWh"와 "청구금액"이 있다. 아파트 관리비 명세서의 "전기료"/"세대전기료"는 금액이다.
5. water: 수도 사용량(m3 또는 톤)과 수도요금(원). 명세서의 "수도료"/"급수비".
6. gas: 도시가스 사용량(m3)과 가스요금(원). 명세서의 "가스사용료". 난방용 지역난방이 아니라 도시가스만.
7. heat: 지역난방 사용량(Gcal)과 난방비(원). 명세서의 "난방비"/"급탕비"는 지역난방 항목이다. 개별난방(도시가스) 아파트에는 없다.
   공용 부분(공용전기료·공동수도료·공동난방비 등)은 세대 사용량이 아니므로 읽지 않는다. 세대가 쓴 "개별 사용료"만 읽는다.
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
    except Exception as e:  # noqa: BLE001
        logger.warning("고지서 판독 실패: %s: %s", type(e).__name__, str(e)[:150])
        raise VerifierUnavailable(f"{type(e).__name__}") from e


def month_in_range(month: str, today: date) -> bool:
    """YYYY-MM 이 오늘이 속한 달부터 MAX_MONTHS_BACK개월 전까지인지."""
    try:
        year, mon = month.split("-")
        target = date(int(year), int(mon), 1)
    except (ValueError, AttributeError):
        return False
    current = date(today.year, today.month, 1)
    oldest_year = current.year + (current.month - 1 - MAX_MONTHS_BACK) // 12
    oldest_month = (current.month - 1 - MAX_MONTHS_BACK) % 12 + 1
    return date(oldest_year, oldest_month, 1) <= target <= current


def flatten_values(reading: BillReading) -> dict:
    """읽은 값을 BE/DB에서 쓰는 평평한 이름으로 바꾼다."""
    return {
        "electricity_kwh": reading.electricity.usage, "electricity_krw": reading.electricity.krw,
        "water_m3": reading.water.usage, "water_krw": reading.water.krw,
        "gas_m3": reading.gas.usage, "gas_krw": reading.gas.krw,
        "heat_gcal": reading.heat.usage, "heat_krw": reading.heat.krw,
    }


def clean_values(values: dict) -> tuple[dict, list[str]]:
    """음수·상한 초과 값은 잘못 읽은 것으로 보고 버린다. 반환: (정리된 값, 버린 항목 이름들)"""
    cleaned, dropped = {}, []
    for key in VALUE_KEYS:
        value = values.get(key)
        if value is None:
            cleaned[key] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            cleaned[key] = None
            dropped.append(key)
            continue
        if number < 0 or number > LIMITS[key]:
            cleaned[key] = None
            dropped.append(key)
        else:
            cleaned[key] = round(number, 2) if key.endswith(("kwh", "m3", "gcal")) else int(round(number))
    return cleaned, dropped


def make_fingerprint(month: str, values: dict) -> str | None:
    """같은 고지서의 재사용을 막기 위한 지문: 월 + 읽은 값들의 해시. 값이 하나도 없으면 None."""
    parts = [f"{key}={values.get(key)}" for key in VALUE_KEYS if values.get(key) is not None]
    if not parts:
        return None
    return hashlib.sha256((month + "|" + "|".join(parts)).encode("utf-8")).hexdigest()[:40]


def _fail(code: str, message: str, **extra) -> dict:
    return {"readable": False, "code": code, "message": message, "bill_month": None, "values": None,
            "total_krw": None, "fingerprint": None, **extra}


def judge_bill(reading: BillReading, today: date) -> dict:
    """읽은 결과를 규칙으로 검사해 최종 결과를 만든다 (같은 입력이면 같은 결과)."""
    if not reading.is_utility_bill:
        return _fail("NOT_A_BILL", "관리비·전기·수도·가스 고지서가 아닌 것 같아요. 고지서가 잘 보이게 다시 찍어 주세요.")
    if not reading.readable:
        return _fail("UNREADABLE", "고지서 숫자가 잘 안 보여요. 밝은 곳에서 가까이 다시 찍어 주세요.")
    if reading.looks_edited:
        return _fail("EDITED", "편집된 것으로 보이는 고지서는 인증할 수 없어요. 값을 직접 입력해 주세요.")
    if not reading.bill_month:
        return _fail("MONTH_UNKNOWN", "고지서의 사용 월을 읽지 못했어요. 월을 직접 선택해 주세요.")
    if not month_in_range(reading.bill_month, today):
        return _fail("MONTH_OUT_OF_RANGE", f"최근 {MAX_MONTHS_BACK}개월 안의 고지서만 인증할 수 있어요.", bill_month=reading.bill_month)

    values, dropped = clean_values(flatten_values(reading))
    if all(values[key] is None for key in VALUE_KEYS):
        message = "읽은 값이 비정상적이에요. 직접 입력해 주세요." if dropped else "전기·수도·가스·난방 사용량이나 금액을 찾지 못했어요."
        return _fail("NO_VALUES", message, bill_month=reading.bill_month)

    note = None
    if dropped:
        note = "일부 값이 비정상적이라 제외했어요. 확인해 주세요."
    return {
        "readable": True, "code": "OK",
        "message": "고지서에서 값을 읽었어요. 맞는지 확인하고 저장해 주세요.",
        "bill_month": reading.bill_month,
        "values": values,
        "total_krw": reading.total_krw,
        "fingerprint": make_fingerprint(reading.bill_month, values),
        "note": note,
    }


def read_bill(
    images: list[bytes],
    today: date | None = None,
    analyzer: Callable[[list[bytes], date], BillReading] | None = None,
) -> dict:
    """
    고지서 사진을 읽어 값을 돌려준다. analyzer는 테스트에서 가짜 비전을 끼우는 자리다.
    예외: BadImages(사진 문제), VerifierUnavailable(AI 일시 불가/비활성).
    """
    if not llm_enabled() and analyzer is None:
        raise VerifierUnavailable("DISABLED")
    prepared = prepare_images(images, max_side=MAX_BILL_IMAGE_SIDE)
    today = today or datetime.now(KST).date()
    reading = (analyzer or analyze_bill)(prepared, today)
    return judge_bill(reading, today)
