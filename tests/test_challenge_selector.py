"""
챌린지 선택 규칙 테스트 (DB 불필요).

실행: pytest tests/test_challenge_selector.py
(pytest가 없으면: python -c "import tests.test_challenge_selector as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]")
"""
import itertools

from app.services.challenge_catalog import get_challenge
from app.services.challenge_selector import select_challenges


def _profile(move=1, food=1, cafe=1, shop=1, difficulty=2) -> dict:
    return {
        "areas": [
            {"key": "move", "level": move},
            {"key": "food", "level": food},
            {"key": "cafe", "level": cafe},
            {"key": "shop", "level": shop},
        ],
        "persona": {"preferred_difficulty": difficulty},
    }


def _ids(result: list[dict]) -> list[str]:
    return [c["challenge_id"] for c in result]


def test_example_profile_picks_top_areas_with_lowered_difficulty():
    # 이동 4, 식품 3, 쇼핑 3, 카페 2, 선호 난이도 2
    result = select_challenges(_profile(move=4, food=3, cafe=2, shop=3, difficulty=2))
    # 첫 슬롯은 선호 난이도(2), 나머지는 한 단계 낮춰 1. 식품/쇼핑 동점은 식품이 먼저.
    assert _ids(result) == ["MOVE_2", "FOOD_1", "SHOP_1"]
    assert [c["selection"]["slot"] for c in result] == [1, 2, 3]
    assert result[0]["selection"]["role"] == "top_area"
    assert result[1]["selection"]["role"] == "next_area"


def test_difficulty_pattern_for_each_preference():
    for base, expected in ((1, [1, 1, 1]), (2, [2, 1, 1]), (3, [3, 2, 2])):
        result = select_challenges(_profile(move=5, food=4, shop=3, difficulty=base))
        assert [c["difficulty"] for c in result] == expected, f"선호 난이도 {base}"


def test_same_input_gives_same_output():
    profile = _profile(move=3, food=2, cafe=4, shop=2, difficulty=3)
    assert _ids(select_challenges(profile)) == _ids(select_challenges(profile))


def test_light_areas_are_replaced_by_life_aux():
    # 줄일 여지(레벨 2 이상)가 있는 영역이 2개뿐이면 세 번째는 생활 영역
    result = select_challenges(_profile(move=4, food=2, cafe=1, shop=1, difficulty=2))
    assert _ids(result) == ["MOVE_2", "FOOD_1", "LIFE_1"]
    assert result[2]["selection"]["role"] == "aux_life"


def test_all_light_profile_starts_with_life():
    result = select_challenges(_profile(difficulty=1))
    assert [c["area"] for c in result] == ["life", "food", "shop"]
    result = select_challenges(_profile(difficulty=3))
    assert result[0]["challenge_id"] == "LIFE_3"


def test_transit_user_is_not_asked_to_take_transit_first():
    # 이동 레벨 2(대중교통 이용자)는 이미 하고 있는 행동이라 이동 챌린지를 먼저 권하지 않는다
    result = select_challenges(_profile(move=2, food=3, cafe=2, shop=3, difficulty=2))
    assert _ids(result) == ["FOOD_2", "SHOP_1", "CAFE_1"]
    # 자가용 중심(레벨 3)이 되면 이동 챌린지가 후보에 오른다
    result = select_challenges(_profile(move=3, food=3, cafe=2, shop=3, difficulty=2))
    assert result[0]["area"] == "move"


def test_transit_user_gets_move_only_after_everything_else():
    # 줄일 영역이 하나도 없으면 생활 -> 나머지 순이고, 이동은 가장 마지막 후보라 3개 안에 들지 않는다
    result = select_challenges(_profile(move=2, food=1, cafe=1, shop=1, difficulty=1))
    assert [c["area"] for c in result] == ["life", "food", "shop"]
    # 다른 영역이 전부 제외돼서 후보가 모자랄 때만 이동이 나온다
    exclude = [f"{a}_{d}" for a in ("FOOD", "SHOP", "CAFE", "LIFE") for d in (1, 2, 3)]
    result = select_challenges(_profile(move=2, food=1, cafe=1, shop=1, difficulty=1), exclude_ids=exclude)
    assert _ids(result) == ["MOVE_1"]


def test_life_never_shown_when_three_active_areas_exist():
    result = select_challenges(_profile(move=3, food=3, cafe=3, shop=3))
    assert "life" not in [c["area"] for c in result]


def test_one_challenge_per_area_and_always_three():
    # 모든 레벨 조합(5^4) x 선호 난이도 3가지에서 불변 조건을 확인
    for move, food, cafe, shop in itertools.product(range(1, 6), repeat=4):
        for base in (1, 2, 3):
            result = select_challenges(_profile(move, food, cafe, shop, base))
            areas = [c["area"] for c in result]
            assert len(result) == 3
            assert len(set(areas)) == 3, f"영역 중복: {areas}"
            assert all(1 <= c["difficulty"] <= 3 for c in result)
            assert all(get_challenge(c["challenge_id"]) for c in result)


def test_exclude_replaces_with_nearest_easier_difficulty_first():
    # MOVE_2가 제외되면 같은 거리(1)인 MOVE_1과 MOVE_3 중 쉬운 쪽(MOVE_1)
    result = select_challenges(_profile(move=4, food=3, shop=3, difficulty=2), exclude_ids=["MOVE_2"])
    assert result[0]["challenge_id"] == "MOVE_1"


def test_exclude_whole_area_moves_to_next_area():
    result = select_challenges(
        _profile(move=4, food=3, shop=3, difficulty=2),
        exclude_ids=["MOVE_1", "MOVE_2", "MOVE_3"],
    )
    assert "move" not in [c["area"] for c in result]
    assert len(result) == 3
    assert result[0]["area"] == "food"  # 이동이 빠지면 다음 순위인 식품이 첫 슬롯이 되고 선호 난이도를 받는다
    assert result[0]["difficulty"] == 2


def test_exclude_never_returns_excluded_ids():
    exclude = ["MOVE_1", "FOOD_1", "SHOP_1", "LIFE_1", "CAFE_1"]
    result = select_challenges(_profile(move=4, food=3, shop=3, difficulty=1), exclude_ids=exclude)
    assert not set(_ids(result)) & set(exclude)
    assert len(result) == 3


def test_returns_fewer_only_when_catalog_is_exhausted():
    all_ids = [f"{a}_{d}" for a in ("MOVE", "CAFE", "FOOD", "SHOP", "LIFE") for d in (1, 2, 3)]
    # 4개 영역을 통째로 제외하면 남은 영역 1개 분량만 나온다
    exclude = [i for i in all_ids if not i.startswith("LIFE")]
    result = select_challenges(_profile(move=4), exclude_ids=exclude)
    assert _ids(result) == ["LIFE_2"]  # 첫 슬롯 선호 난이도 2에 가장 가까운 것
    assert select_challenges(_profile(move=4), exclude_ids=all_ids) == []


def test_preferred_difficulty_missing_or_invalid():
    profile = _profile(move=4, food=3, shop=3)
    del profile["persona"]
    assert select_challenges(profile)[0]["difficulty"] == 1
    for bad in (None, "2", 0, 9, -1):
        profile = _profile(move=4, food=3, shop=3, difficulty=bad)
        result = select_challenges(profile)
        assert all(1 <= c["difficulty"] <= 3 for c in result)
    assert select_challenges(_profile(move=4, difficulty=9))[0]["difficulty"] == 3  # 3으로 맞춤


def test_empty_profile_does_not_crash():
    result = select_challenges({})
    assert len(result) == 3
    assert result[0]["area"] == "life"


def test_selection_info_has_level_and_target():
    result = select_challenges(_profile(move=4, food=3, shop=3, difficulty=2))
    assert result[0]["selection"]["area_level"] == 4
    assert result[0]["selection"]["target_difficulty"] == 2
    assert result[1]["selection"]["target_difficulty"] == 1
