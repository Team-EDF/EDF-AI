"""
가정 에너지(관리비) 탄소 계산과 고지서 판독 규칙 테스트 (Gemini 호출 없음).

실행: pytest tests/test_household.py
"""
import io
from datetime import date
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import household as household_route
from app.services import household_bill_reader as reader
from app.services.challenge_verifier import BadImages, VerifierUnavailable
from app.services.household_bill_reader import (
    BillReading,
    UtilityReading,
    clean_values,
    judge_bill,
    make_fingerprint,
    month_in_range,
    read_bill,
)
from app.services.household_carbon import FACTORS, SPEND_FACTOR_FALLBACK, calculate_household_carbon

TODAY = date(2026, 10, 4)


def _reading(**overrides) -> BillReading:
    base = dict(
        is_utility_bill=True, readable=True, bill_month="2026-09",
        electricity=UtilityReading(usage=320, krw=58000), water=UtilityReading(usage=14, krw=9800),
        gas=UtilityReading(usage=28, krw=31000), heat=UtilityReading(), total_krw=187000,
    )
    base.update(overrides)
    return BillReading(**base)


def _jpeg() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (300, 400), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


# ---------------------------------------------------------------- 탄소 계산

def test_usage_based_carbon_uses_official_factors():
    result = calculate_household_carbon(
        {"electricity_kwh": 300, "water_m3": 10, "gas_m3": 20, "heat_gcal": None}, spend_factors=SPEND_FACTOR_FALLBACK)
    by_key = {item["key"]: item for item in result["items"]}
    assert by_key["electricity"]["carbon_kg"] == round(300 * 0.4173, 2)      # 125.19
    assert by_key["water"]["carbon_kg"] == round(10 * 0.332, 2)               # 3.32
    assert by_key["gas"]["carbon_kg"] == round(20 * 2.176, 2)                 # 43.52
    assert all(by_key[k]["basis"] == "usage" for k in ("electricity", "water", "gas"))
    assert by_key["heat"]["basis"] == "none" and by_key["heat"]["carbon_kg"] == 0
    assert result["total_kg"] == round(125.19 + 3.32 + 43.52, 2) and result["estimated"] is False


def test_spend_based_fallback_is_marked_as_estimate():
    result = calculate_household_carbon({"electricity_krw": 60000}, spend_factors=SPEND_FACTOR_FALLBACK)
    electricity = result["items"][0]
    assert electricity["basis"] == "spend" and electricity["carbon_kg"] == round(60000 * SPEND_FACTOR_FALLBACK["electricity"], 2)
    assert result["estimated"] is True and "추정" in result["note"]


def test_usage_wins_over_spend_when_both_given():
    result = calculate_household_carbon({"electricity_kwh": 100, "electricity_krw": 999999}, spend_factors=SPEND_FACTOR_FALLBACK)
    assert result["items"][0]["basis"] == "usage" and result["items"][0]["carbon_kg"] == round(100 * 0.4173, 2)


def test_zero_negative_and_bad_values_are_ignored():
    result = calculate_household_carbon(
        {"electricity_kwh": 0, "water_m3": -5, "gas_m3": "abc", "heat_gcal": None}, spend_factors=SPEND_FACTOR_FALLBACK)
    assert result["total_kg"] == 0 and all(item["basis"] == "none" for item in result["items"])


def test_all_utilities_have_a_factor_and_unit():
    assert set(FACTORS) == {"electricity", "water", "gas", "heat"}
    assert FACTORS["electricity"]["unit"] == "kWh" and FACTORS["heat"]["unit"] == "Gcal"


# ---------------------------------------------------------------- 월 범위 / 값 정리 / 지문

def test_month_range_is_last_13_months_up_to_current_month():
    assert month_in_range("2026-10", TODAY) and month_in_range("2026-09", TODAY)
    assert month_in_range("2025-09", TODAY)                       # 13개월 전
    assert not month_in_range("2025-08", TODAY)                   # 14개월 전
    assert not month_in_range("2026-11", TODAY)                   # 미래
    assert not month_in_range("2026-9x", TODAY) and not month_in_range(None, TODAY) and not month_in_range("2026", TODAY)


def test_month_range_across_year_boundary():
    jan = date(2026, 1, 15)
    assert month_in_range("2024-12", jan) and not month_in_range("2024-11", jan)


def test_clean_values_drops_negative_and_absurd_numbers():
    cleaned, dropped = clean_values({"electricity_kwh": 320.456, "electricity_krw": 58000.4, "water_m3": -1,
                                     "gas_m3": 99999, "heat_gcal": None, "gas_krw": "x"})
    assert cleaned["electricity_kwh"] == 320.46 and cleaned["electricity_krw"] == 58000
    assert cleaned["water_m3"] is None and cleaned["gas_m3"] is None and cleaned["gas_krw"] is None
    assert set(dropped) == {"water_m3", "gas_m3", "gas_krw"}


def test_fingerprint_is_stable_and_value_sensitive():
    values = {"electricity_kwh": 320.0, "electricity_krw": 58000}
    assert make_fingerprint("2026-09", values) == make_fingerprint("2026-09", dict(values))
    assert make_fingerprint("2026-09", values) != make_fingerprint("2026-08", values)
    assert make_fingerprint("2026-09", values) != make_fingerprint("2026-09", {**values, "electricity_kwh": 321.0})
    assert make_fingerprint("2026-09", {}) is None


# ---------------------------------------------------------------- 고지서 판정

def test_good_bill_is_read_with_values_and_fingerprint():
    result = judge_bill(_reading(), TODAY)
    assert result["readable"] and result["code"] == "OK" and result["bill_month"] == "2026-09"
    assert result["values"]["electricity_kwh"] == 320 and result["values"]["gas_krw"] == 31000
    assert result["values"]["heat_gcal"] is None and result["fingerprint"] and result["total_krw"] == 187000


def test_bill_rejections():
    cases = {
        "NOT_A_BILL": _reading(is_utility_bill=False),
        "UNREADABLE": _reading(readable=False),
        "EDITED": _reading(looks_edited=True),
        "MONTH_UNKNOWN": _reading(bill_month=None),
        "MONTH_OUT_OF_RANGE": _reading(bill_month="2024-01"),
        "NO_VALUES": _reading(electricity=UtilityReading(), water=UtilityReading(), gas=UtilityReading()),
    }
    for code, reading in cases.items():
        result = judge_bill(reading, TODAY)
        assert not result["readable"] and result["code"] == code, (code, result)
        assert result["values"] is None and result["fingerprint"] is None


def test_absurd_values_are_dropped_with_a_note_or_rejected():
    mixed = judge_bill(_reading(gas=UtilityReading(usage=99999, krw=31000)), TODAY)
    assert mixed["readable"] and mixed["values"]["gas_m3"] is None and mixed["values"]["gas_krw"] == 31000 and mixed["note"]
    only_absurd = judge_bill(_reading(electricity=UtilityReading(usage=99999), water=UtilityReading(), gas=UtilityReading()), TODAY)
    assert not only_absurd["readable"] and only_absurd["code"] == "NO_VALUES" and "비정상" in only_absurd["message"]


def test_partial_bill_with_only_electricity_is_fine():
    result = judge_bill(_reading(water=UtilityReading(), gas=UtilityReading()), TODAY)
    assert result["readable"] and result["values"]["water_m3"] is None and result["values"]["electricity_kwh"] == 320


# ---------------------------------------------------------------- 흐름 / API

def test_read_bill_with_fake_analyzer_passes_prepared_images_and_today():
    seen = {}

    def fake(images, today):
        seen.update(count=len(images), today=today)
        return _reading()

    result = read_bill([_jpeg(), _jpeg()], today=TODAY, analyzer=fake)
    assert result["readable"] and seen == {"count": 2, "today": TODAY}


def test_read_bill_errors():
    for bad in ([], [b"nope"]):
        try:
            read_bill(bad, today=TODAY, analyzer=lambda *a: _reading())
        except BadImages:
            continue
        raise AssertionError("BadImages가 나와야 함")
    with mock.patch.dict("os.environ", {"CHALLENGE_VERIFY_ENABLED": "false"}):
        try:
            read_bill([_jpeg()], today=TODAY)
        except VerifierUnavailable:
            pass
        else:
            raise AssertionError("스위치를 끄면 VerifierUnavailable")


def _client():
    app = FastAPI()
    app.include_router(household_route.router, prefix="/api")
    return TestClient(app)


def test_read_bill_endpoint_ok_and_errors():
    with mock.patch.object(reader, "analyze_bill", lambda images, today: _reading(bill_month=today.strftime("%Y-%m"))):
        r = _client().post("/api/household/read-bill", files=[("images", ("a.jpg", _jpeg(), "image/jpeg"))])
    body = r.json()
    assert r.status_code == 200 and body["readable"] and body["values"]["electricity_kwh"] == 320 and body["fingerprint"]

    r = _client().post("/api/household/read-bill", files=[("images", ("a.txt", b"hello", "text/plain"))])
    assert r.status_code == 400
    with mock.patch.object(reader, "analyze_bill", side_effect=VerifierUnavailable("TimeoutError")):
        r = _client().post("/api/household/read-bill", files=[("images", ("a.jpg", _jpeg(), "image/jpeg"))])
    assert r.status_code == 503


def test_carbon_endpoint():
    with mock.patch("app.services.household_carbon.get_spend_factors", return_value=SPEND_FACTOR_FALLBACK):
        r = _client().post("/api/household/carbon", json={"electricity_kwh": 300, "gas_m3": 20, "water_krw": 10000})
    body = r.json()
    assert r.status_code == 200 and body["estimated"] is True
    by_key = {item["key"]: item for item in body["items"]}
    assert by_key["electricity"]["carbon_kg"] == round(300 * 0.4173, 2)
    assert by_key["water"]["basis"] == "spend" and by_key["gas"]["basis"] == "usage"
