"""
프로필 레벨 규칙 / 그린 유형(4축 16유형) 규칙 테스트 (DB 불필요).

실행: pytest tests/test_profile_rules.py
(pytest가 없으면: python -c "import tests.test_profile_rules as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]")
"""
import itertools

from app.services.profile_service import (
    AXES,
    GREEN_TYPES,
    LEVEL_CUTS,
    PREFERRED_DIFFICULTY,
    VALID_ECO_INTERESTS,
    VALID_GOAL_INTENTS,
    build_green_type,
    classify_attitude_axis,
    classify_move_axis,
    classify_shop_axis,
    classify_table_axis,
    get_area_level,
    get_focus_area,
    get_preferred_difficulty,
)


# ---------------------------------------------------------------- 영역 레벨

def test_level_boundaries_are_inclusive():
    # 기준선 값 "이하"가 해당 레벨, 넘으면 다음 레벨
    for area, cuts in LEVEL_CUTS.items():
        for level, cut in enumerate(cuts, start=1):
            assert get_area_level(area, cut) == level
            assert get_area_level(area, cut + 0.001) == level + 1
        assert get_area_level(area, cuts[-1] * 100) == 5


def test_level_zero_carbon_is_lowest():
    for area in LEVEL_CUTS:
        assert get_area_level(area, 0.0) == 1


def test_level_cuts_are_strictly_increasing():
    for cuts in LEVEL_CUTS.values():
        assert cuts == sorted(set(cuts))


def test_survey_option_levels():
    # 설문 선택지 대표값으로 계산한 월 탄소량(kg)이 기대한 레벨로 떨어지는지 확인
    assert get_area_level("move", 0.0) == 1        # 도보·자전거
    assert get_area_level("move", 0.978) == 1      # 대중교통 3만원 이하
    assert get_area_level("move", 3.262) == 2      # 대중교통 3~7만원
    assert get_area_level("move", 67.657) == 3     # 자가용 10만원 이하
    assert get_area_level("move", 270.63) == 4     # 자가용 10~30만원
    assert get_area_level("move", 473.602) == 5    # 자가용 30만원 이상
    assert get_area_level("cafe", 0.0) == 1        # 거의 없음
    assert get_area_level("cafe", 2.628) == 2      # 3만원 이하
    assert get_area_level("cafe", 7.883) == 4      # 3만원 이상
    assert get_area_level("food", 8.759) == 2      # 10만원 이하
    assert get_area_level("food", 35.036) == 3     # 10~30만원
    assert get_area_level("food", 61.312) == 4     # 30만원 이상
    assert get_area_level("shop", 6.362) == 2      # 5만원 이하
    assert get_area_level("shop", 25.448) == 3     # 5~15만원
    assert get_area_level("shop", 50.896) == 4     # 15만원 이상


# ---------------------------------------------------------------- 그린 유형 축

def test_move_axis():
    assert classify_move_axis(1) == "W"
    assert classify_move_axis(2) == "W"     # 대중교통
    assert classify_move_axis(3) == "D"     # 자가용 소액부터 드라이버
    assert classify_move_axis(5) == "D"


def test_table_axis_threshold():
    assert classify_table_axis(8.759, 0.0) == "L"        # 식품 10만원 이하
    assert classify_table_axis(35.036, 2.628) == "L"     # 식품 10~30만원 + 카페 소액 = 37.7
    assert classify_table_axis(35.036, 7.883) == "F"     # 식품 10~30만원 + 카페 3만원 이상 = 42.9
    assert classify_table_axis(61.312, 0.0) == "F"       # 식품 30만원 이상
    assert classify_table_axis(40.0, 0.0) == "F"         # 기준선은 이상(>=)이 F


def test_shop_axis():
    assert classify_shop_axis(2) == "M"
    assert classify_shop_axis(3) == "S"


def test_attitude_axis_all_combinations():
    expected = {
        ("tried", "serious_reduction"): "A",
        ("tried", "light_start"): "A",
        ("tried", "just_looking"): "E",
        ("interested_not_tried", "serious_reduction"): "A",
        ("interested_not_tried", "light_start"): "A",
        ("interested_not_tried", "just_looking"): "E",
        ("not_interested", "serious_reduction"): "A",   # 관심은 없지만 줄이겠다는 의지가 확실
        ("not_interested", "light_start"): "E",         # 관심 없음 + 가볍게 = 탐색가
        ("not_interested", "just_looking"): "E",
    }
    for (eco, goal), letter in expected.items():
        assert classify_attitude_axis(eco, goal) == letter


def test_attitude_axis_missing_or_unknown_is_explorer():
    assert classify_attitude_axis(None, None) == "E"
    assert classify_attitude_axis("tried", None) == "E"
    assert classify_attitude_axis("unknown", "serious_reduction") == "E"


def test_interest_and_no_interest_differ_for_light_start():
    # 관심 있음/없음을 나눈 효과: 같은 "가볍게 시작"이라도 태도 축과 난이도가 갈린다
    assert classify_attitude_axis("interested_not_tried", "light_start") == "A"
    assert classify_attitude_axis("not_interested", "light_start") == "E"
    assert get_preferred_difficulty("tried", "light_start") > get_preferred_difficulty("not_interested", "light_start")


# ---------------------------------------------------------------- 16유형 구성

def test_there_are_sixteen_distinct_types():
    codes = {"".join(p) for p in itertools.product(*[tuple(a["poles"]) for a in AXES])}
    assert len(codes) == 16
    assert set(GREEN_TYPES) == codes
    assert len({t["type_name"] for t in GREEN_TYPES.values()}) == 16
    for info in GREEN_TYPES.values():
        assert info["type_name"] and info["emoji"] and info["tagline"] and info["description"]


def test_build_green_type_example():
    levels = {"move": 4, "food": 3, "cafe": 2, "shop": 3}
    carbons = {"move": 270.63, "food": 35.036, "cafe": 2.628, "shop": 25.448}
    result = build_green_type(levels, carbons, "interested_not_tried", "serious_reduction")
    assert result["type_code"] == "DLSA"        # 자가용, 가벼운 식탁(37.7<40), 쇼퍼, 실행가
    assert result["type_name"] == GREEN_TYPES["DLSA"]["type_name"]
    assert [a["letter"] for a in result["axes"]] == ["D", "L", "S", "A"]
    assert result["preferred_difficulty"] == 2


def test_build_green_type_walker_minimal():
    levels = {"move": 1, "food": 2, "cafe": 1, "shop": 2}
    carbons = {"move": 0.0, "food": 8.759, "cafe": 0.0, "shop": 6.362}
    result = build_green_type(levels, carbons, "tried", "light_start")
    assert result["type_code"] == "WLMA"
    assert result["preferred_difficulty"] == 2


def test_every_answer_combination_gives_valid_type():
    # 어떤 레벨/답변 조합이 와도 16유형 중 하나로 정해져야 한다 (None 포함)
    for move, shop in itertools.product((1, 3), (2, 3)):
        for food, cafe in ((8.0, 0.0), (45.0, 0.0)):
            for eco, goal in itertools.product((*VALID_ECO_INTERESTS, None), (*VALID_GOAL_INTENTS, None)):
                levels = {"move": move, "food": 2, "cafe": 1, "shop": shop}
                result = build_green_type(levels, {"food": food, "cafe": cafe}, eco, goal)
                assert result["type_code"] in GREEN_TYPES


# ---------------------------------------------------------------- 난이도 / 시작 영역

def test_preferred_difficulty_table():
    assert len(PREFERRED_DIFFICULTY) == 9
    assert all(v in (1, 2, 3) for v in PREFERRED_DIFFICULTY.values())
    assert get_preferred_difficulty("tried", "serious_reduction") == 3
    assert get_preferred_difficulty("not_interested", "just_looking") == 1
    assert get_preferred_difficulty(None, None) == 1


def test_focus_area_picks_highest_level_with_tie_order():
    assert get_focus_area({"move": 4, "food": 3, "cafe": 2, "shop": 3}) == "move"
    assert get_focus_area({"move": 2, "food": 3, "cafe": 2, "shop": 3}) == "food"   # 동점이면 move>food>shop>cafe
    assert get_focus_area({"move": 1, "food": 2, "cafe": 4, "shop": 2}) == "cafe"
    assert get_focus_area({"move": 1, "food": 1, "cafe": 1, "shop": 1}) is None     # 전부 1레벨이면 없음
