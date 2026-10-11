"""
챌린지 선택 규칙 (순수 규칙, LLM 미사용).

프로필(영역별 레벨 + 선호 난이도)로 카탈로그에서 챌린지 3개를 고른다.
같은 입력이면 항상 같은 결과가 나온다 (난수 없음).

규칙
1) 영역 순서: 줄일 여지가 있는 영역(식품/카페/쇼핑은 레벨 2 이상, 이동은 자가용 중심인 레벨 3 이상)을
   레벨 높은 순으로 (동점이면 이동 > 식품 > 쇼핑 > 카페), 그 다음에 '생활' 영역(프로필 막대가 없는
   보조 영역), 마지막으로 나머지 영역 순.
   -> 이미 가벼운 영역보다 줄일 여지가 있는 영역을 먼저 추천하고, 줄일 영역이 모자랄 때만
      생활 영역을 1개 끼워 넣는다. 이미 대중교통 중심인 사람에게는 이동 챌린지를 먼저 권하지 않는다.
2) 한 영역에서 챌린지 1개씩만 고른다 (영역 중복 없음).
3) 난이도: 첫 번째(가장 줄일 여지가 큰 영역)는 선호 난이도, 나머지는 한 단계 낮춰서(최소 1) 부담을 줄인다.
4) 제외 목록(이미 부여/완료한 챌린지)에 걸리면 같은 영역에서 가장 가까운 난이도로 대체하고
   (같은 거리면 쉬운 쪽), 그 영역이 전부 제외되면 다음 영역으로 넘어간다.
"""
from app.services.challenge_catalog import list_challenges

# 프로필에 막대가 있는 영역 (생활은 설문 데이터가 없어 보조 영역으로 따로 다룬다)
PROFILE_AREAS = ("move", "food", "cafe", "shop")
AUX_AREA = "life"

# 레벨이 같을 때 우선하는 영역 순서 (profile_service.FOCUS_AREA_ORDER와 같은 기준)
AREA_TIE_ORDER = ("move", "food", "shop", "cafe")

SLOT_COUNT = 3
DEFAULT_DIFFICULTY = 1

# 이 레벨 이상이면 "줄일 여지가 있는 영역"으로 본다.
# 이동 챌린지는 대중교통 이용을 늘리는 것이라서, 이미 도보/대중교통 중심인 사람(레벨 2 이하)에게는
# 하던 행동에 포인트만 주는 꼴이 된다. 그래서 자가용 중심(레벨 3 이상)일 때만 후보로 올린다.
MIN_ACTIVE_LEVEL = {"move": 3, "food": 2, "cafe": 2, "shop": 2}


def _clamp_difficulty(value) -> int:
    """선호 난이도를 1~3으로 맞춘다 (없거나 이상한 값이면 가장 쉬운 1)."""
    if not isinstance(value, int):
        return DEFAULT_DIFFICULTY
    return max(1, min(3, value))


def _order_areas(levels: dict[str, int]) -> list[str]:
    """추천 후보 영역을 우선순위 순으로 정렬한다."""
    ranked = sorted(PROFILE_AREAS, key=lambda a: (-levels.get(a, 1), AREA_TIE_ORDER.index(a)))
    active = [a for a in ranked if levels.get(a, 1) >= MIN_ACTIVE_LEVEL[a]]
    light = [a for a in ranked if levels.get(a, 1) < MIN_ACTIVE_LEVEL[a]]
    # 나머지 영역 중 이동은 대중교통 중심인 사람에게 의미가 없으니 맨 끝으로 보낸다
    light.sort(key=lambda a: a == "move")
    return active + [AUX_AREA] + light


def _pick_in_area(area: str, target_difficulty: int, exclude: set[str]) -> dict | None:
    """영역에서 목표 난이도에 가장 가까운(같으면 쉬운 쪽) 챌린지를 제외 목록을 피해 고른다."""
    candidates = [c for c in list_challenges(area=area) if c["challenge_id"] not in exclude]
    if not candidates:
        return None
    return min(candidates, key=lambda c: (abs(c["difficulty"] - target_difficulty), c["difficulty"]))


def _role(area: str, slot: int) -> str:
    """이 챌린지가 왜 뽑혔는지(설명 문구용 구분값)."""
    if area == AUX_AREA:
        return "aux_life"
    return "top_area" if slot == 1 else "next_area"


def select_challenges(profile: dict, exclude_ids: list[str] | None = None, count: int = SLOT_COUNT) -> list[dict]:
    """
    프로필로 챌린지 count개(기본 3)를 고른다.

    profile 예시: {"areas": [{"key": "move", "level": 4}, ...],
                   "persona": {"preferred_difficulty": 2}}
    반환: 카탈로그 항목 + "selection" 정보(slot, role, area_level, target_difficulty)
    """
    exclude = set(exclude_ids or [])
    levels = {a["key"]: a["level"] for a in profile.get("areas", []) if "key" in a and "level" in a}
    base_difficulty = _clamp_difficulty((profile.get("persona") or {}).get("preferred_difficulty"))

    selected: list[dict] = []
    for area in _order_areas(levels):
        if len(selected) >= count:
            break

        slot = len(selected) + 1
        # 첫 슬롯은 선호 난이도, 나머지는 한 단계 낮춰서 시작 부담을 줄인다
        target = base_difficulty if slot == 1 else max(1, base_difficulty - 1)

        challenge = _pick_in_area(area, target, exclude)
        if challenge is None:
            continue  # 그 영역 챌린지가 전부 제외됨 -> 다음 영역으로

        item = dict(challenge)
        item["selection"] = {
            "slot": slot,
            "role": _role(area, slot),
            "area_level": levels.get(area),
            "target_difficulty": target,
        }
        selected.append(item)

    return selected
