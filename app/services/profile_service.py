"""
Green Profile 생성 로직 (프로토타입).

설문 답변 -> 영역별 탄소 수준(5단계) + "그린 유형"(4축 16유형)을 규칙으로 계산한다.
LLM을 쓰지 않는 순수 규칙 기반이라 같은 답변이면 항상 같은 결과가 나온다.

레벨은 "영역별 월 탄소배출량(kg CO2e)"에 기준선을 둔다. 설문 추정값(onboarding_service)과
영수증 실데이터(2단계)가 같은 단위(kg/월)라서, 같은 기준선을 그대로 재사용할 수 있다.
그린 유형의 앞 세 축(이동/식탁/소비)도 같은 값에서 나오므로, 실데이터가 쌓이면 유형이 실제로 바뀐다.

주의: 아래 기준선(LEVEL_CUTS, TABLE_FEAST_THRESHOLD_KG)은 비교할 전국 평균 같은 외부 통계 없이
설문 선택지 구간에 맞춰 정한 잠정값이다. 실제 배포 전 팀 검토가 필요하다.
"""
import math

from app.services.onboarding_service import build_goal, calculate_onboarding_target
from app.services.realdata_service import get_area_stats

LEVEL_LABELS = {
    1: "매우 낮음",
    2: "낮음",
    3: "보통",
    4: "높음",
    5: "매우 높음",
}

# 프로필에 보여줄 영역 순서와 이름. (onboarding_service의 breakdown 키 -> 프로필 영역 키)
AREAS = [
    {"key": "move", "label": "이동", "source_key": "transport"},
    {"key": "food", "label": "식품", "source_key": "food"},
    {"key": "cafe", "label": "카페", "source_key": "cafe"},
    {"key": "shop", "label": "쇼핑", "source_key": "shopping"},
]

# 영역별 레벨 기준선: 월 탄소량(kg)이 각 값 "이하"면 해당 레벨, 마지막 값을 넘으면 5레벨.
# 예) move: 1kg 이하=1, 10kg 이하=2, 100kg 이하=3, 300kg 이하=4, 초과=5
LEVEL_CUTS = {
    "move": [1, 10, 100, 300],   # 도보/대중교통 소액=1, 대중교통=2, 자가용 소액=3, 자가용 중간=4, 자가용 다액=5
    "food": [5, 15, 40, 70],     # 식품 지출 10만원 이하=2, 10~30만원=3, 30만원 이상=4
    "cafe": [1, 3, 6, 10],       # 거의 없음=1, 3만원 이하=2, 3만원 이상=4
    "shop": [3, 10, 30, 60],     # 5만원 이하=2, 5~15만원=3, 15만원 이상=4
}

# ---------------------------------------------------------------------------
# 그린 유형: 4개 축(각 두 극) 조합 = 16유형. 코드는 축 글자 4개를 이어 붙인다 (예: "WLMA").
#   이동 : W 뚜벅이(도보·대중교통 중심)  / D 드라이버(자가용 중심)
#   식탁 : L 가벼운 식탁                 / F 풍성한 식탁
#   소비 : M 미니멀                      / S 쇼퍼
#   태도 : A 실행가                      / E 탐색가
# ---------------------------------------------------------------------------
AXES = [
    {"axis": "이동", "poles": {"W": "뚜벅이", "D": "드라이버"}},
    {"axis": "식탁", "poles": {"L": "가벼운 식탁", "F": "풍성한 식탁"}},
    {"axis": "소비", "poles": {"M": "미니멀", "S": "쇼퍼"}},
    {"axis": "태도", "poles": {"A": "실행가", "E": "탐색가"}},
]

MOVE_DRIVER_MIN_LEVEL = 3          # 이동 레벨이 이 값 이상이면 드라이버(D)
TABLE_FEAST_THRESHOLD_KG = 40      # 식품+카페 월 탄소량이 이 값 이상이면 풍성한 식탁(F)
SHOP_SHOPPER_MIN_LEVEL = 3         # 쇼핑 레벨이 이 값 이상이면 쇼퍼(S)

GREEN_TYPES = {
    "WLMA": {
        "type_name": "맑은 이슬 새싹", "emoji": "🌱",
        "tagline": "가장 가볍게 걷는 새싹",
        "description": "걷거나 대중교통으로 이동하고, 식탁도 소비도 가볍게 유지하는 분이에요. 실천에도 적극적이니 지금처럼 이어가며 한 단계 높은 챌린지에 도전해 보세요.",
    },
    "WLME": {
        "type_name": "바람 타는 민들레", "emoji": "🌼",
        "tagline": "가볍게 떠돌며 둘러보는 민들레",
        "description": "이동도 식탁도 소비도 이미 가벼운 편이에요. 실천은 아직 지켜보는 단계라, 부담 없는 챌린지로 가볍게 시작해 보세요.",
    },
    "WLSA": {
        "type_name": "알뜰한 네잎클로버", "emoji": "🍀",
        "tagline": "쇼핑해도 알뜰하게 챙기는 클로버",
        "description": "이동과 식탁은 가벼운데 쇼핑이 눈에 띄는 편이에요. 실천 의지가 있으니 쇼핑 챌린지로 소비 습관을 다듬어 보세요.",
    },
    "WLSE": {
        "type_name": "꽃잎 흩날리는 벚나무", "emoji": "🌸",
        "tagline": "쇼핑 구경이 즐거운 벚나무",
        "description": "이동과 식탁은 가벼운데 쇼핑이 눈에 띄어요. 구매 한 번 미루기 같은 쉬운 챌린지부터 시작해 보세요.",
    },
    "WFMA": {
        "type_name": "텃밭의 든든한 토마토", "emoji": "🍅",
        "tagline": "든든히 먹고 가볍게 걷는 토마토",
        "description": "이동과 소비는 가벼운데 식탁이 풍성한 편이에요. 실천 의지가 있으니 저탄소 식품 챌린지로 식탁을 가볍게 바꿔 보세요.",
    },
    "WFME": {
        "type_name": "느긋한 고구마", "emoji": "🍠",
        "tagline": "천천히 익어가는 고구마",
        "description": "이동과 소비는 가벼운데 식탁이 풍성해요. 서두르지 않아도 되니 저탄소 식품 한 끼부터 가볍게 시도해 보세요.",
    },
    "WFSA": {
        "type_name": "곳간 가득한 도토리", "emoji": "🌰",
        "tagline": "든든히 먹고 알뜰히 모으는 도토리",
        "description": "식탁과 쇼핑이 풍성한 편이지만 실천 의지가 높아요. 식품과 쇼핑 중 하나를 골라 집중하면 변화가 크게 보일 거예요.",
    },
    "WFSE": {
        "type_name": "향긋한 허브 정원", "emoji": "🌿",
        "tagline": "풍성하게 즐기는 허브",
        "description": "식탁과 쇼핑이 풍성한 편이에요. 이동은 가벼우니 가장 쉬운 챌린지부터 하나씩 늘려 가요.",
    },
    "DLMA": {
        "type_name": "곧게 뻗는 대나무", "emoji": "🎋",
        "tagline": "빠르게 달리지만 군더더기 없는 대나무",
        "description": "자가용 이동이 눈에 띄고 식탁과 소비는 가벼워요. 실천 의지가 있으니 대중교통 챌린지로 가장 큰 변화를 만들 수 있어요.",
    },
    "DLME": {
        "type_name": "산들거리는 갈대", "emoji": "🌾",
        "tagline": "바람 따라 움직이는 갈대",
        "description": "이동에서 자가용 비중이 있는 편이에요. 식탁과 소비는 가벼우니 대중교통 1회 같은 쉬운 챌린지로 시작해 보세요.",
    },
    "DLSA": {
        "type_name": "쭉쭉 자라는 전나무", "emoji": "🌲",
        "tagline": "바쁘게 달리고 알뜰히 사는 전나무",
        "description": "자가용 이동과 쇼핑이 눈에 띄어요. 실천 의지가 높으니 이동과 쇼핑 챌린지를 번갈아 도전해 보세요.",
    },
    "DLSE": {
        "type_name": "바람결 버드나무", "emoji": "🍃",
        "tagline": "여기저기 다니며 구경하는 버드나무",
        "description": "자가용 이동과 쇼핑이 눈에 띄어요. 가장 쉬운 챌린지부터 하나씩 시작해 보세요.",
    },
    "DFMA": {
        "type_name": "묵직한 참나무", "emoji": "🌳",
        "tagline": "발자국은 크지만 의지는 더 큰 참나무",
        "description": "자가용 이동과 풍성한 식탁이 눈에 띄어요. 실천 의지가 높으니 이동 챌린지로 큰 변화를 노려 보세요.",
    },
    "DFME": {
        "type_name": "느긋한 선인장", "emoji": "🌵",
        "tagline": "여유롭게 지켜보는 선인장",
        "description": "자가용 이동과 풍성한 식탁이 눈에 띄지만 소비는 가벼워요. 이번 달은 둘러보는 단계니 쉬운 챌린지부터 가볍게 해 보세요.",
    },
    "DFSA": {
        "type_name": "활짝 핀 해바라기", "emoji": "🌻",
        "tagline": "많이 쓰는 만큼 크게 바꿀 수 있는 해바라기",
        "description": "이동, 식탁, 쇼핑 모두 활발해요. 그만큼 줄일 여지가 커서, 실천 의지를 살리면 가장 큰 변화를 만들 수 있어요.",
    },
    "DFSE": {
        "type_name": "무럭무럭 몬스테라", "emoji": "🪴",
        "tagline": "크게 자랄 가능성이 큰 몬스테라",
        "description": "활동 범위가 넓고 풍성한 생활을 해요. 부담 없는 챌린지 하나부터 시작하면 변화의 폭이 클 거예요.",
    },
}

VALID_ECO_INTERESTS = ("tried", "interested_not_tried", "not_interested")
VALID_GOAL_INTENTS = ("light_start", "serious_reduction", "just_looking")

# 챌린지 선호 난이도(1~3): (친환경 제품 경험·관심, 이번 달 각오) 조합별. 챌린지 선택 규칙에서 쓴다.
# 관심 없음/관심 있음을 나눠서, 같은 각오라도 경험이 있을수록 높은 난이도를 권한다.
PREFERRED_DIFFICULTY = {
    ("tried", "serious_reduction"): 3,
    ("tried", "light_start"): 2,
    ("tried", "just_looking"): 1,
    ("interested_not_tried", "serious_reduction"): 2,
    ("interested_not_tried", "light_start"): 1,
    ("interested_not_tried", "just_looking"): 1,
    ("not_interested", "serious_reduction"): 2,
    ("not_interested", "light_start"): 1,
    ("not_interested", "just_looking"): 1,
}
DEFAULT_PREFERRED_DIFFICULTY = 1  # 답변이 비었거나 알 수 없는 값이면 가장 쉬운 쪽

# 먼저 시작할 영역을 고를 때 레벨이 같으면 이 순서로 우선한다.
FOCUS_AREA_ORDER = ["move", "food", "shop", "cafe"]


def get_area_level(area_key: str, carbon_kg: float) -> int:
    """영역의 월 탄소량(kg)을 1~5 레벨로 바꾼다."""
    for level, cut in enumerate(LEVEL_CUTS[area_key], start=1):
        if carbon_kg <= cut:
            return level
    return 5


# get_axis_percents가 돌려주는 값이 어느 글자 쪽 비율인지 (AXES 순서와 같다).
PERCENT_LETTERS = ["D", "F", "S", "A"]

# 축 비율(%) 계산용. 기준선에서 멀수록 한쪽에 가까워지되, 5~95%로 제한해 100%/0%는 보여주지 않는다.
AXIS_PERCENT_MIN = 5
AXIS_PERCENT_MAX = 95
AXIS_PERCENT_STEEPNESS = 2.5       # 기준선에서 10배 멀어질 때 비율이 얼마나 가파르게 변하는지(로그 스케일)

# 태도 축은 연속값이 없어서 (친환경 경험·관심, 이번 달 각오) 답변 조합별 "실행가(A) 비율"을 고정값으로 둔다.
# 실행가(A)로 판정되는 조합은 모두 50 초과, 탐색가(E)로 판정되는 조합은 모두 50 이하여야 한다.
ACTION_PERCENT = {
    ("tried", "serious_reduction"): 90,
    ("interested_not_tried", "serious_reduction"): 85,
    ("not_interested", "serious_reduction"): 70,
    ("tried", "light_start"): 75,
    ("interested_not_tried", "light_start"): 65,
    ("not_interested", "light_start"): 35,
    ("tried", "just_looking"): 40,
    ("interested_not_tried", "just_looking"): 25,
    ("not_interested", "just_looking"): 10,
}
UNKNOWN_ACTION_PERCENT = 50        # 답변이 비었거나 알 수 없으면 판단 근거가 없어 반반

# 설문은 처음 한 번만 하므로, 태도 축은 실제 행동(최근 30일 챌린지 완료)으로도 올라갈 수 있다.
# 완료가 이 횟수 이상이면 설문 답과 상관없이 실행가(A)이고, 비율은 최소 ACTIVE_ACTION_PERCENT 이상이다. (내려가지는 않는다)
ACTIVE_COMPLETIONS_MIN = 2
ACTIVE_ACTION_PERCENT = 70


def _upper_pole_percent(value_kg: float, threshold_kg: float, floor_kg: float) -> int:
    """기준선(threshold) 대비 value의 로그 비율로 "기준선 위쪽 극(D/F/S)"에 가까운 정도(5~95%)를 구한다."""
    ratio = math.log10(max(value_kg, floor_kg) / threshold_kg)
    share = 1 / (1 + math.exp(-AXIS_PERCENT_STEEPNESS * ratio))
    return max(AXIS_PERCENT_MIN, min(AXIS_PERCENT_MAX, round(share * 100)))


def _axis_threshold_kg(area_key: str, min_level: int) -> float:
    """"레벨이 min_level 이상"이 되는 월 탄소량 경계(kg). 해당 영역 기준선 중 min_level-1번째 값."""
    return float(LEVEL_CUTS[area_key][min_level - 2])


def _action_percent(eco_interest: str | None, goal_intent: str | None, recent_completions: int) -> int:
    """태도 축의 실행가(A) 비율: 설문 답 조합별 고정값, 최근 챌린지를 꾸준히 완료했으면 최소 ACTIVE_ACTION_PERCENT."""
    base = ACTION_PERCENT.get((eco_interest, goal_intent), UNKNOWN_ACTION_PERCENT)
    if recent_completions >= ACTIVE_COMPLETIONS_MIN:
        return max(base, ACTIVE_ACTION_PERCENT)
    return base


def get_axis_percents(
    carbons: dict[str, float],
    eco_interest: str | None,
    goal_intent: str | None,
    recent_completions: int = 0,
) -> list[int]:
    """
    축별 비율을 [이동 D%, 식탁 F%, 소비 S%, 태도 A%] 순서로 돌려준다 (각 값 = 그 글자 쪽에 가까운 정도).
    기준선을 넘어 D/F/S로 판정되거나 실행가(A)로 판정되면 값이 50 초과, 아니면 50 이하라서
    코드 글자 판정(classify_*_axis)과 같은 방향이다.
    """
    return [
        _upper_pole_percent(carbons["move"], _axis_threshold_kg("move", MOVE_DRIVER_MIN_LEVEL), 0.1),
        _upper_pole_percent(carbons["food"] + carbons["cafe"], TABLE_FEAST_THRESHOLD_KG, 1.0),
        _upper_pole_percent(carbons["shop"], _axis_threshold_kg("shop", SHOP_SHOPPER_MIN_LEVEL), 0.5),
        _action_percent(eco_interest, goal_intent, recent_completions),
    ]


def classify_move_axis(move_level: int) -> str:
    """이동 축: 자가용 중심이면 D(드라이버), 아니면 W(뚜벅이)."""
    return "D" if move_level >= MOVE_DRIVER_MIN_LEVEL else "W"


def classify_table_axis(food_carbon_kg: float, cafe_carbon_kg: float) -> str:
    """식탁 축: 식품+카페 월 탄소량이 기준 이상이면 F(풍성한 식탁), 아니면 L(가벼운 식탁)."""
    return "F" if food_carbon_kg + cafe_carbon_kg >= TABLE_FEAST_THRESHOLD_KG else "L"


def classify_shop_axis(shop_level: int) -> str:
    """소비 축: 쇼핑 레벨이 기준 이상이면 S(쇼퍼), 아니면 M(미니멀)."""
    return "S" if shop_level >= SHOP_SHOPPER_MIN_LEVEL else "M"


def classify_attitude_axis(
    eco_interest: str | None,
    goal_intent: str | None,
    recent_completions: int = 0,
) -> str:
    """
    태도 축: 실천 의지가 있으면 A(실행가), 아니면 E(탐색가).
    - 확실히 줄여보기 -> A
    - 가볍게 시작 -> 친환경 경험·관심이 있으면 A, 관심 없음이면 E
    - 일단 구경만 -> E
    - 답변이 비었거나 알 수 없는 값 -> 판단 근거가 없으니 E
    - 단, 최근 30일에 챌린지를 ACTIVE_COMPLETIONS_MIN번 이상 완료했으면 설문 답과 상관없이 A
    """
    if recent_completions >= ACTIVE_COMPLETIONS_MIN:
        return "A"
    if eco_interest not in VALID_ECO_INTERESTS or goal_intent not in VALID_GOAL_INTENTS:
        return "E"
    if goal_intent == "serious_reduction":
        return "A"
    if goal_intent == "light_start" and eco_interest != "not_interested":
        return "A"
    return "E"


def get_preferred_difficulty(eco_interest: str | None, goal_intent: str | None) -> int:
    """친환경 경험·관심과 이번 달 각오로 선호 챌린지 난이도(1~3)를 정한다."""
    return PREFERRED_DIFFICULTY.get((eco_interest, goal_intent), DEFAULT_PREFERRED_DIFFICULTY)


def get_focus_area(levels: dict[str, int]) -> str | None:
    """레벨이 가장 높은 영역(같으면 FOCUS_AREA_ORDER 순)을 먼저 시작할 영역으로 고른다. 전부 1레벨이면 None."""
    best = max(FOCUS_AREA_ORDER, key=lambda key: (levels[key], -FOCUS_AREA_ORDER.index(key)))
    return best if levels[best] > 1 else None


def build_green_type(
    levels: dict[str, int],
    carbons: dict[str, float],
    eco_interest: str | None,
    goal_intent: str | None,
    recent_completions: int = 0,
) -> dict:
    """영역별 레벨·탄소량과 태도 답변으로 그린 유형(4축 코드 + 이름·설명)을 만든다."""
    letters = [
        classify_move_axis(levels["move"]),
        classify_table_axis(carbons["food"], carbons["cafe"]),
        classify_shop_axis(levels["shop"]),
        classify_attitude_axis(eco_interest, goal_intent, recent_completions),
    ]
    code = "".join(letters)
    info = GREEN_TYPES[code]
    upper_percents = get_axis_percents(carbons, eco_interest, goal_intent, recent_completions)
    axes = []
    for axis, letter, upper_letter, upper_percent in zip(AXES, letters, PERCENT_LETTERS, upper_percents):
        # 선택된 글자 쪽 비율(percent)과 반대쪽 비율(opposite_percent). 합은 항상 100.
        percent = max(50, upper_percent if letter == upper_letter else 100 - upper_percent)
        opposite_letter = next(pole for pole in axis["poles"] if pole != letter)
        axes.append({
            "axis": axis["axis"],
            "letter": letter,
            "label": axis["poles"][letter],
            "percent": percent,
            "opposite_letter": opposite_letter,
            "opposite_label": axis["poles"][opposite_letter],
            "opposite_percent": 100 - percent,
        })
    return {
        "type_code": code,
        "type_name": info["type_name"],
        "emoji": info["emoji"],
        "tagline": info["tagline"],
        "description": info["description"],
        "axes": axes,
        "preferred_difficulty": get_preferred_difficulty(eco_interest, goal_intent),
    }


def _survey_area_values(target: dict) -> dict[str, dict]:
    """설문 추정값(calculate_onboarding_target 결과)을 프로필 영역 키별 {carbon_kg, spend_krw}로 바꾼다."""
    return {
        area["key"]: {
            "carbon_kg": target["carbon_breakdown_kg"][area["source_key"]],
            "spend_krw": target["estimated_spend_krw"][area["source_key"]],
        }
        for area in AREAS
    }


def _merge_with_data(survey_values: dict[str, dict], data_areas: dict[str, dict]) -> dict[str, dict]:
    """
    실데이터 영역값을 쓰되, 이동은 설문 추정값보다 낮아지지 않게 한다.
    자가용 연료비는 영수증으로 잘 안 올라와서 데이터만 보면 자가용 사용자가 뚜벅이로 바뀌어 버린다.
    (이동에 영수증이 많이 올라와서 설문보다 크면 데이터 값을 쓴다.)
    """
    merged = {key: dict(value) for key, value in data_areas.items()}
    if survey_values["move"]["carbon_kg"] > merged["move"]["carbon_kg"]:
        merged["move"] = dict(survey_values["move"])
    return merged


def build_profile(
    answers: dict,
    conn=None,
    user_id: int | None = None,
    recent_challenge_completions: int = 0,
) -> dict:
    """
    Green Profile(영역별 레벨 + 그린 유형 + 목표)을 만든다.

    - 기본은 설문 답변으로 계산한다 (source="survey").
    - user_id가 있고 최근 30일 확정 영수증이 충분하면(realdata_service.DATA_MIN_RECEIPTS건 이상)
      실제 소비 데이터로 계산한다 (source="data"). 모자라면 설문으로 계산하고 이유를 data_info에 남긴다.
      성향(실행가/탐색가, 선호 난이도)은 설문 답변(eco_interest, goal_intent)에서 가져오되, 설문은 처음 한 번만 하므로
      최근 30일 챌린지 완료 수(recent_challenge_completions)가 ACTIVE_COMPLETIONS_MIN 이상이면 실행가(A)로 올린다.
    """
    survey = calculate_onboarding_target(answers, conn=conn)
    area_values = _survey_area_values(survey)
    baseline_carbon_kg = survey["baseline_carbon_kg"]
    source = "survey"
    data_info = None

    if user_id is not None:
        stats = get_area_stats(user_id, conn=conn)
        data_info = {
            "used": stats["enough"],
            "receipts_in_window": stats["receipts"],
            "window_days": stats["window_days"],
            "min_receipts": stats["min_receipts"],
            "reason": None if stats["enough"] else "insufficient_receipts",
        }
        if stats["enough"]:
            source = "data"
            area_values = _merge_with_data(area_values, stats["areas"])
            baseline_carbon_kg = round(sum(v["carbon_kg"] for v in area_values.values()), 3)

    areas = []
    levels: dict[str, int] = {}
    carbons: dict[str, float] = {}
    for area in AREAS:
        carbon_kg = area_values[area["key"]]["carbon_kg"]
        level = get_area_level(area["key"], carbon_kg)
        levels[area["key"]] = level
        carbons[area["key"]] = carbon_kg
        areas.append({
            "key": area["key"],
            "label": area["label"],
            "level": level,
            "level_label": LEVEL_LABELS[level],
            "carbon_kg": carbon_kg,
            "spend_krw": area_values[area["key"]]["spend_krw"],
        })

    goal = build_goal(baseline_carbon_kg, answers.get("goal_intent"))
    return {
        "source": source,
        "areas": areas,
        "persona": build_green_type(
            levels, carbons, answers.get("eco_interest"), answers.get("goal_intent"), recent_challenge_completions
        ),
        "focus_area": get_focus_area(levels),
        "baseline_carbon_kg": baseline_carbon_kg,
        "reduction_rate": goal["reduction_rate"],
        "target_carbon_kg": goal["target_carbon_kg"],
        "message": goal["message"],
        "data_info": data_info,
    }
