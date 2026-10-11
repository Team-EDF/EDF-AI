"""
실데이터(영수증) 기반 프로필 테스트. DB는 쓰지 않는다 (가짜 커서/패치 사용).

실행: pytest tests/test_realdata_profile.py
(pytest가 없으면: python -c "import tests.test_realdata_profile as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]")
"""
import json
from datetime import date
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import profile as profile_route
from app.services import profile_service
from app.services.realdata_service import (
    DATA_MIN_RECEIPTS,
    aggregate_areas,
    get_area_stats,
    is_cafe_merchant,
    parse_record_entries,
)


def _ocr(*items, merchant_main=None, merchant_carbon=None, total_amount=None) -> str:
    data = {"item_results": [
        {"category": {"main_name": main}, "carbon_kg": carbon, "amount_krw": amount}
        for main, carbon, amount in items
    ]}
    if merchant_main:
        data["merchant_category"] = {"main_name": merchant_main}
        data["merchant_carbon_kg"] = merchant_carbon
        data["total_amount_krw"] = total_amount
    return json.dumps(data, ensure_ascii=False)


def _record(merchant, ocr_data, carbon=None, amount=None) -> dict:
    return {"merchant_name": merchant, "ocr_data": ocr_data, "total_carbon_kg": carbon, "total_amount": amount}


# ---------------------------------------------------------------- 파싱

def test_parse_items_from_ocr_data():
    entries = parse_record_entries(_ocr(("식음료", 1.5, 8000), ("쇼핑소비재", 2.0, 5000)))
    assert entries == [
        {"main_name": "식음료", "carbon_kg": 1.5, "amount_krw": 8000.0},
        {"main_name": "쇼핑소비재", "carbon_kg": 2.0, "amount_krw": 5000.0},
    ]


def test_parse_falls_back_to_merchant_category_when_no_items():
    raw = _ocr(merchant_main="교통", merchant_carbon=6.5, total_amount=4000)
    assert parse_record_entries(raw) == [{"main_name": "교통", "carbon_kg": 6.5, "amount_krw": 4000.0}]


def test_parse_merchant_fallback_uses_record_totals_when_missing():
    raw = json.dumps({"merchant_category": {"main_name": "식음료"}}, ensure_ascii=False)
    assert parse_record_entries(raw, total_carbon_kg=3.0, total_amount=9000) == [
        {"main_name": "식음료", "carbon_kg": 3.0, "amount_krw": 9000.0}
    ]


def test_parse_bad_input_gives_empty_list():
    for bad in (None, "", "not json", "[]", json.dumps({"item_results": [{"carbon_kg": 1}]})):
        assert parse_record_entries(bad) == []


# ---------------------------------------------------------------- 카페 판별 / 영역 집계

def test_cafe_merchant_detection():
    for name in ("메가MGC커피 용인기흥점", "스타벅스 강남R점", "Blue Bottle Coffee", "동네카페"):
        assert is_cafe_merchant(name), name
    for name in ("제일식당", "GS25 기흥해링턴가점", "에코마트", "", None):
        assert not is_cafe_merchant(name), name


def test_aggregate_areas_maps_main_categories():
    records = [
        _record("제일식당", _ocr(("식음료", 2.0, 10000))),
        _record("메가MGC커피 용인기흥점", _ocr(("식음료", 0.5, 4500))),
        _record("에코마트", _ocr(("쇼핑소비재", 3.0, 20000), ("식음료", 1.0, 5000))),
        _record("주유소", _ocr(merchant_main="교통", merchant_carbon=6.0, total_amount=30000)),
        _record("버스", _ocr(("대중교통", 0.1, 1500))),
        _record("한전", _ocr(("가정에너지", 9.0, 50000))),   # 프로필 영역이 아니라 무시
    ]
    areas = aggregate_areas(records)
    assert areas["food"] == {"carbon_kg": 3.0, "spend_krw": 15000}
    assert areas["cafe"] == {"carbon_kg": 0.5, "spend_krw": 4500}
    assert areas["shop"] == {"carbon_kg": 3.0, "spend_krw": 20000}
    assert areas["move"] == {"carbon_kg": 6.1, "spend_krw": 31500}


def test_aggregate_empty_is_all_zero():
    areas = aggregate_areas([])
    assert set(areas) == {"move", "food", "cafe", "shop"}
    assert all(v == {"carbon_kg": 0.0, "spend_krw": 0} for v in areas.values())


# ---------------------------------------------------------------- 영수증 조회 (가짜 DB)

class _FakeCursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        raise AssertionError("넘겨 준 conn을 서비스가 닫으면 안 된다")


def _rows(n):
    return [("제일식당", _ocr(("식음료", 1.0, 10000)), 1.0, 10000)] * n


def test_get_area_stats_threshold_and_query_params():
    conn = _FakeConn(_rows(DATA_MIN_RECEIPTS - 1))
    stats = get_area_stats(7, conn=conn, today=date(2026, 10, 4))
    assert stats["receipts"] == DATA_MIN_RECEIPTS - 1 and stats["enough"] is False

    conn = _FakeConn(_rows(DATA_MIN_RECEIPTS))
    stats = get_area_stats(7, conn=conn, today=date(2026, 10, 4))
    assert stats["enough"] is True and stats["window_days"] == 30
    assert stats["areas"]["food"]["carbon_kg"] == float(DATA_MIN_RECEIPTS)

    sql, params = conn.cursor_obj.executed[0]
    assert "user_id = %s" in sql and "ocr_status = 'SUCCESS'" in sql   # 사용자 한정 + 확정 영수증만
    assert params == (7, date(2026, 9, 5), date(2026, 10, 4))          # 오늘 포함 최근 30일


# ---------------------------------------------------------------- 프로필 (설문 vs 실데이터)

SURVEY_TARGET = {   # 자가용 10~30만원 / 카페 소액 / 식품 10~30만원 / 쇼핑 5~15만원 (calculate_onboarding_target 결과 모양)
    "estimated_spend_krw": {"transport": 200000, "cafe": 15000, "food": 200000, "shopping": 100000},
    "carbon_breakdown_kg": {"transport": 270.63, "cafe": 2.628, "food": 35.036, "shopping": 25.448},
    "baseline_carbon_kg": 333.742,
    "reduction_rate": 0.15, "target_carbon_kg": 283.681, "message": "survey message",
}
ANSWERS = {"eco_interest": "interested_not_tried", "goal_intent": "serious_reduction"}


def _stats(enough=True, move=0.0, food=0.0, cafe=0.0, shop=0.0, receipts=8):
    def area(carbon):
        return {"carbon_kg": carbon, "spend_krw": int(carbon * 1000)}
    return {"receipts": receipts, "window_days": 30, "min_receipts": 5, "enough": enough,
            "areas": {"move": area(move), "food": area(food), "cafe": area(cafe), "shop": area(shop)}}


def _build(stats=None, user_id=None):
    with mock.patch.object(profile_service, "calculate_onboarding_target", return_value=SURVEY_TARGET), \
         mock.patch.object(profile_service, "get_area_stats", return_value=stats) as stats_mock:
        profile = profile_service.build_profile(dict(ANSWERS), user_id=user_id)
    return profile, stats_mock


def test_without_user_id_behaves_exactly_like_survey():
    profile, stats_mock = _build(user_id=None)
    assert profile["source"] == "survey" and profile["data_info"] is None
    stats_mock.assert_not_called()
    assert profile["baseline_carbon_kg"] == 333.742 and profile["target_carbon_kg"] == 283.681
    assert {a["key"]: a["level"] for a in profile["areas"]} == {"move": 4, "food": 3, "cafe": 2, "shop": 3}


def test_insufficient_receipts_keeps_survey_and_explains_why():
    profile, _ = _build(stats=_stats(enough=False, receipts=2, shop=50), user_id=1)
    assert profile["source"] == "survey"
    assert profile["data_info"] == {"used": False, "receipts_in_window": 2, "window_days": 30,
                                    "min_receipts": 5, "reason": "insufficient_receipts"}
    assert profile["baseline_carbon_kg"] == 333.742     # 영수증 값이 섞이지 않는다


def test_enough_receipts_switches_to_data():
    profile, _ = _build(stats=_stats(move=400.0, food=45.0, cafe=1.0, shop=5.0), user_id=1)
    assert profile["source"] == "data"
    assert profile["data_info"]["used"] is True and profile["data_info"]["reason"] is None
    carbons = {a["key"]: a["carbon_kg"] for a in profile["areas"]}
    assert carbons == {"move": 400.0, "food": 45.0, "cafe": 1.0, "shop": 5.0}
    assert {a["key"]: a["level"] for a in profile["areas"]} == {"move": 5, "food": 4, "cafe": 1, "shop": 2}
    assert profile["baseline_carbon_kg"] == 451.0


def test_data_goal_is_applied_to_data_baseline():
    profile, _ = _build(stats=_stats(move=400.0, food=45.0, cafe=1.0, shop=5.0), user_id=1)
    assert profile["reduction_rate"] == 0.15
    assert profile["target_carbon_kg"] == round(451.0 * 0.85, 3)


def test_move_never_drops_below_survey_estimate():
    # 자가용 연료비는 영수증으로 잘 안 올라온다: 데이터에 이동이 거의 없어도 설문의 자가용 추정은 유지
    profile, _ = _build(stats=_stats(move=0.0, food=10.0, cafe=0.0, shop=10.0), user_id=1)
    move = next(a for a in profile["areas"] if a["key"] == "move")
    assert move["carbon_kg"] == 270.63 and move["level"] == 4
    assert profile["persona"]["axes"][0]["letter"] == "D"     # 드라이버 유지 (뚜벅이로 뒤바뀌지 않는다)
    assert profile["source"] == "data"
    # 다른 영역은 설문이 아니라 데이터 값
    food = next(a for a in profile["areas"] if a["key"] == "food")
    assert food["carbon_kg"] == 10.0


def test_data_move_wins_when_larger_than_survey():
    profile, _ = _build(stats=_stats(move=500.0), user_id=1)
    move = next(a for a in profile["areas"] if a["key"] == "move")
    assert move["carbon_kg"] == 500.0 and move["level"] == 5


def test_persona_attitude_still_comes_from_survey_answers_in_data_mode():
    profile, _ = _build(stats=_stats(move=400.0, food=45.0), user_id=1)
    letters = [a["letter"] for a in profile["persona"]["axes"]]
    assert letters[3] == "A" and profile["persona"]["preferred_difficulty"] == 2


def test_data_type_changes_with_receipts():
    survey_profile, _ = _build(user_id=None)
    data_profile, _ = _build(stats=_stats(move=0.0, food=5.0, cafe=0.0, shop=1.0), user_id=1)
    # 설문은 쇼퍼(shop 레벨3)였지만 실제 쇼핑 영수증이 적으면 미니멀(M)로 바뀐다 (유형이 실제로 바뀐다)
    assert survey_profile["persona"]["axes"][2]["letter"] == "S"
    assert data_profile["persona"]["axes"][2]["letter"] == "M"
    assert survey_profile["persona"]["type_code"] != data_profile["persona"]["type_code"]


# ---------------------------------------------------------------- API

def test_api_passes_user_id_and_returns_data_info():
    fake_profile = {
        "source": "data",
        "data_info": {"used": True, "receipts_in_window": 8, "window_days": 30, "min_receipts": 5, "reason": None},
        "areas": [{"key": k, "label": k, "level": 1, "level_label": "매우 낮음", "carbon_kg": 0.0, "spend_krw": 0}
                  for k in ("move", "food", "cafe", "shop")],
        "persona": {"type_code": "WLME", "type_name": "n", "emoji": "e", "tagline": "t", "description": "d",
                    "axes": [{"axis": "이동", "letter": "W", "label": "뚜벅이", "percent": 90,
                              "opposite_letter": "D", "opposite_label": "드라이버", "opposite_percent": 10}],
                    "preferred_difficulty": 1},
        "focus_area": None, "baseline_carbon_kg": 0.0, "reduction_rate": 0.05, "target_carbon_kg": 0.0, "message": "m",
    }
    app = FastAPI()
    app.include_router(profile_route.router, prefix="/api")
    with mock.patch.object(profile_route, "build_profile", return_value=fake_profile) as build_mock:
        r = TestClient(app).post("/api/profile", json={"user_id": 7, "transport": "car", "goal_intent": "light_start"})
    assert r.status_code == 200
    assert r.json()["source"] == "data" and r.json()["data_info"]["receipts_in_window"] == 8
    answers = build_mock.call_args.args[0]
    assert "user_id" not in answers and "recent_challenge_completions" not in answers and answers["transport"] == "car"  # 설문 답변에 섞이지 않는다
    assert build_mock.call_args.kwargs == {"user_id": 7, "recent_challenge_completions": 0}
