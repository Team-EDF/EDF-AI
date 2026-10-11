"""
온보딩 설문 -> 추천 탄소 절감 목표 계산 로직 (프로토타입).

설문 화면/저장은 백엔드가 담당하고, AI는 답변을 받아서 예상 탄소배출량(baseline)과
추천 목표(target)를 계산하는 부분만 담당한다.

계산 방식: 실제 영수증 처리와 동일한 공식(app/services/carbon.py)을 그대로 쓴다.
    지출액(원) x co2eq_KRW(카테고리 탄소계수) = 탄소배출량(kg CO2e)
문항으로 "빈도"를 묻는 대신 "지출 구간"을 묻고, 그 구간의 대표값(중간값)을
DB에 실제 저장된 main_category.avg_middle_category_carbon과 곱해서 예상치를 낸다.
이렇게 하면 설문 기반 추정치와 실제 영수증 기반 계산이 같은 방법론을 쓰게 된다.

아직 백엔드가 실제 설문 문항의 필드 이름/응답값을 확정하지 않아서, 아래
answers 딕셔너리의 키(transport, cafe_drink 등)와 옵션 문자열은 잠정 이름이다.
백엔드가 실제 문항을 확정하면 이 이름들만 맞춰서 조정하면 된다.
"""
from app.database.connection import get_db_connection
from app.services.carbon import calculate_carbon

# 1번 문항(이동수단)에 따라 분기되는 후속 질문(transport_spend)의 구간 대표값.
# "자가용" 선택 시 유류비/주차비 등, "대중교통" 선택 시 교통비 지출 구간을 별도로 묻는다.
# 두 수단은 지출 규모 자체가 달라서 구간을 다르게 잡았다. 도보/자전거는 후속 질문 없이 지출 0원.
CAR_SPEND_KRW = {
    "under_100k": 50_000,
    "100k_to_300k": 200_000,
    "over_300k": 350_000,
}
PUBLIC_TRANSIT_SPEND_KRW = {
    "under_30k": 15_000,
    "30k_to_70k": 50_000,
    "over_70k": 90_000,
}
# 수단에 따라 배출량 계산에 쓸 카테고리가 갈린다 (교통 vs 대중교통).
TRANSPORT_CATEGORY = {
    "car": "교통",
    "public_transit": "대중교통",
    "walk_bike": "대중교통",  # 지출 0원이라 어떤 카테고리를 곱해도 결과는 0
}

# 카페·음료 한 달 지출 구간의 대표값(중간값, 원)
CAFE_SPEND_KRW = {
    "none": 0,
    "under_30k": 15_000,
    "over_30k": 45_000,
}

# 한 달 식품(식재료+외식) 지출 구간의 대표값
FOOD_SPEND_KRW = {
    "under_100k": 50_000,
    "100k_to_300k": 200_000,
    "over_300k": 350_000,
}

# 온오프라인 쇼핑 한 달 지출 구간의 대표값
SHOPPING_SPEND_KRW = {
    "under_50k": 25_000,
    "50k_to_150k": 100_000,
    "over_150k": 200_000,
}

# 카페·음료, 식품 지출은 둘 다 "식음료" 카테고리 탄소계수를 쓴다.
FOOD_CATEGORY = "식음료"
SHOPPING_CATEGORY = "쇼핑소비재"

# 마무리 문항(이번 달 각오)에 따른 감축률.
# baseline(예상 배출량)에서 이 비율만큼 줄인 값을 최종 목표로 잡는다.
# "just_looking"은 목표를 아예 잡지 않고 baseline만 보여준다.
GOAL_INTENT_REDUCTION_RATE = {
    "light_start": 0.05,
    "serious_reduction": 0.15,
    "just_looking": None,
}

GOAL_INTENT_MESSAGES = {
    "light_start": "가볍게 시작해봐요! 작은 습관부터 하나씩 바꿔봐요.",
    "serious_reduction": "확실하게 줄여볼 준비가 되셨네요! 적극적으로 도전해봐요.",
    "just_looking": "가볍게 둘러보는 것부터 시작해봐요! 언제든 목표를 올릴 수 있어요.",
}


def _get_category_carbon_factor(main_name: str, conn=None) -> float:
    """main_category.avg_middle_category_carbon(kg CO2e / KRW)을 조회한다."""
    own_conn = conn is None
    if own_conn:
        conn = get_db_connection()

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT avg_middle_category_carbon
            FROM main_category
            WHERE main_name = %s
            LIMIT 1;
            """,
            (main_name,),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        if own_conn:
            conn.close()

    if not row or row[0] is None:
        return 0.0
    return float(row[0])


def calculate_onboarding_target(answers: dict, conn=None) -> dict:
    """
    설문 답변(지출 구간 기반)을 받아 예상 배출량(baseline)과 추천 목표를 계산한다.

    answers 예시:
    {
        "transport": "public_transit",
        "cafe_drink": "under_30k",
        "food": "100k_to_300k",
        "shopping": "50k_to_150k",
        "eco_interest": "interested_not_tried",
        "goal_intent": "serious_reduction",
    }
    """
    own_conn = conn is None
    if own_conn:
        conn = get_db_connection()

    try:
        transport = answers.get("transport")
        transport_spend_answer = answers.get("transport_spend")
        if transport == "car":
            transport_spend = CAR_SPEND_KRW.get(transport_spend_answer, 0)
        elif transport == "public_transit":
            transport_spend = PUBLIC_TRANSIT_SPEND_KRW.get(transport_spend_answer, 0)
        else:
            transport_spend = 0
        transport_category = TRANSPORT_CATEGORY.get(transport, "대중교통")
        transport_factor = _get_category_carbon_factor(transport_category, conn)
        transport_carbon_kg = calculate_carbon(transport_factor, transport_spend) or 0.0

        # 카페·음료와 식품은 같은 "식음료" 계수를 쓰지만, 프로필 막대(카페/식품)가 따로라서 분리해서 계산한다.
        cafe_spend = CAFE_SPEND_KRW.get(answers.get("cafe_drink"), 0)
        food_spend = FOOD_SPEND_KRW.get(answers.get("food"), 0)
        food_factor = _get_category_carbon_factor(FOOD_CATEGORY, conn)
        cafe_carbon_kg = calculate_carbon(food_factor, cafe_spend) or 0.0
        food_carbon_kg = calculate_carbon(food_factor, food_spend) or 0.0

        shopping_spend = SHOPPING_SPEND_KRW.get(answers.get("shopping"), 0)
        shopping_factor = _get_category_carbon_factor(SHOPPING_CATEGORY, conn)
        shopping_carbon_kg = calculate_carbon(shopping_factor, shopping_spend) or 0.0
    finally:
        if own_conn:
            conn.close()

    breakdown = {
        "transport": round(transport_carbon_kg, 3),
        "cafe": round(cafe_carbon_kg, 3),
        "food": round(food_carbon_kg, 3),
        "shopping": round(shopping_carbon_kg, 3),
    }
    baseline_carbon_kg = round(sum(breakdown.values()), 3)
    spend_krw = {
        "transport": transport_spend,
        "cafe": cafe_spend,
        "food": food_spend,
        "shopping": shopping_spend,
    }

    return {
        "estimated_spend_krw": spend_krw,
        "carbon_breakdown_kg": breakdown,
        "baseline_carbon_kg": baseline_carbon_kg,
        **build_goal(baseline_carbon_kg, answers.get("goal_intent")),
    }


def build_goal(baseline_carbon_kg: float, goal_intent: str | None) -> dict:
    """
    기준 배출량(baseline)과 "이번 달 각오"로 감축률/목표/안내 문구를 만든다.
    설문 기반과 실데이터 기반 프로필이 같은 규칙을 쓰도록 따로 뺀 함수다.
    """
    reduction_rate = GOAL_INTENT_REDUCTION_RATE.get(goal_intent, 0.05)

    if reduction_rate is None:  # "일단 구경만"은 목표를 잡지 않는다
        return {
            "reduction_rate": None,
            "target_carbon_kg": None,
            "message": GOAL_INTENT_MESSAGES["just_looking"],
        }

    return {
        "reduction_rate": reduction_rate,
        "target_carbon_kg": round(baseline_carbon_kg * (1 - reduction_rate), 3),
        "message": GOAL_INTENT_MESSAGES.get(goal_intent, GOAL_INTENT_MESSAGES["light_start"]),
    }
