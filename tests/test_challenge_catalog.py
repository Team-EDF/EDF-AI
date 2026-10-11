"""
챌린지 카탈로그 로더 테스트 (DB 불필요).

실행: pytest tests/test_challenge_catalog.py
(pytest가 없으면: python -c "import tests.test_challenge_catalog as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]")
"""
import copy
import json

from app.services.challenge_catalog import (
    AREAS,
    CATALOG_PATH,
    DIFFICULTIES,
    _validate,
    get_challenge,
    list_challenges,
    load_catalog,
)


def _raw() -> dict:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _expect_error(raw: dict, keyword: str) -> None:
    try:
        _validate(raw)
    except ValueError as e:
        assert keyword in str(e), f"에러 메시지에 {keyword!r}가 없음: {e}"
        return
    raise AssertionError(f"ValueError가 발생해야 하는데 통과함 (기대 키워드: {keyword!r})")


# ---------------------------------------------------------------- 실제 카탈로그

def test_catalog_loads_and_has_fifteen():
    catalog = load_catalog()
    assert catalog["catalog_version"]
    assert len(catalog["challenges"]) == 15


def test_every_area_and_difficulty_slot_filled_once():
    slots = [(c["area"], c["difficulty"]) for c in load_catalog()["challenges"]]
    assert sorted(slots) == sorted((a, d) for a in AREAS for d in DIFFICULTIES)


def test_ids_unique_and_follow_naming():
    ids = [c["challenge_id"] for c in load_catalog()["challenges"]]
    assert len(set(ids)) == 15
    for c in load_catalog()["challenges"]:
        assert c["challenge_id"] == f"{c['area'].upper()}_{c['difficulty']}"


def test_points_increase_with_difficulty_in_each_area():
    for area in AREAS:
        points = [c["points"] for c in list_challenges(area=area)]
        assert points == sorted(points) and len(set(points)) == 3, f"{area}: {points}"


def test_auto_verification_only_for_move_and_pays_more_than_self():
    for c in load_catalog()["challenges"]:
        if c["verification"] == "AUTO_TRANSIT":
            assert c["area"] == "move"
    # 같은 난이도라면 자동 인증(이동)이 자율 체크보다 포인트가 높다
    for level in DIFFICULTIES:
        move = next(c for c in list_challenges(area="move") if c["difficulty"] == level)
        for other in list_challenges(difficulty=level):
            if other["area"] != "move":
                assert move["points"] > other["points"], f"난이도 {level}: 자동 인증이 자율보다 높아야 함"


def test_self_has_daily_limit_and_auto_does_not():
    for c in load_catalog()["challenges"]:
        if c["verification"] == "SELF":
            assert c["daily_check_limit"] == 1
        else:
            assert c["daily_check_limit"] is None


def test_saving_only_where_there_is_a_basis():
    for c in load_catalog()["challenges"]:
        if c["est_saving_kg"] is None:
            assert c["saving_basis"] is None
        else:
            assert c["est_saving_kg"] > 0 and c["saving_basis"]
    with_saving = {c["area"] for c in load_catalog()["challenges"] if c["est_saving_kg"] is not None}
    assert with_saving == {"move", "shop"}  # 지출 기반으로 계산 가능한 영역만


def test_move_saving_scales_with_count():
    move = {c["difficulty"]: c for c in list_challenges(area="move")}
    base = move[1]["est_saving_kg"]
    assert abs(move[2]["est_saving_kg"] - base * 2) < 0.01
    assert abs(move[3]["est_saving_kg"] - base * 3) < 0.01


def test_get_and_list_helpers():
    assert get_challenge("MOVE_2")["title"] == "대중교통 주 2회 이용하기"
    assert get_challenge("NOPE_9") is None
    assert len(list_challenges()) == 15
    assert len(list_challenges(area="shop")) == 3
    assert len(list_challenges(difficulty=1)) == 5
    assert [c["challenge_id"] for c in list_challenges(area="cafe", difficulty=3)] == ["CAFE_3"]


# ---------------------------------------------------------------- 잘못된 JSON은 거부

def test_validate_accepts_real_catalog():
    _validate(_raw())


def test_validate_rejects_missing_field():
    raw = _raw()
    del raw["challenges"][0]["points"]
    _expect_error(raw, "필수 항목 누락")


def test_validate_rejects_duplicate_id():
    raw = _raw()
    raw["challenges"][1]["challenge_id"] = raw["challenges"][0]["challenge_id"]
    _expect_error(raw, "중복")


def test_validate_rejects_bad_values():
    raw = _raw(); raw["challenges"][0]["difficulty"] = 4
    _expect_error(raw, "difficulty")
    raw = _raw(); raw["challenges"][0]["points"] = 0
    _expect_error(raw, "points")
    raw = _raw(); raw["challenges"][0]["area"] = "work"
    _expect_error(raw, "영역")
    raw = _raw(); raw["challenges"][0]["verification"] = "PHOTO"
    _expect_error(raw, "인증방식")
    raw = _raw(); raw["challenges"][0]["target_count"] = -1
    _expect_error(raw, "target_count")


def test_validate_rejects_saving_without_basis():
    raw = _raw()
    raw["challenges"][0]["saving_basis"] = None
    _expect_error(raw, "saving_basis")


def test_validate_rejects_missing_slot():
    raw = _raw()
    raw["challenges"].pop()  # 15개 -> 14개
    _expect_error(raw, "15개")
    raw = copy.deepcopy(_raw())
    raw["challenges"][-1]["difficulty"] = 1  # LIFE_3 칸을 LIFE_1과 겹치게
    _expect_error(raw, "이미 있습니다")
