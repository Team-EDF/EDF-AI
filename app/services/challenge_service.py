"""
AI 챌린지 자동 생성 로직 (프로토타입, v2 - 절충안).

트렌드 집계(실제 데이터) -> LLM이 구체적인 행동을 자유롭게 제안, 3단계로 구성한다.
아직 백엔드에 챌린지 저장용 테이블/API가 없어서, 이 모듈은 호출할 때마다
그 자리에서 계산만 하는 프로토타입이다.

v1(고정 카탈로그에서 LLM이 골라 쓰는 방식) 대비 바뀐 점:
- 챌린지 "행동 문구"는 더 이상 고정 목록에서 고르지 않고 LLM이 자유롭게 작성한다.
  카테고리별 IDEA_HINTS는 방향을 잡아주는 참고용일 뿐, 반드시 그대로 쓸 필요는 없다.
- 다만 "숫자(예상 절감량 kg)"는 여전히 LLM이 지어내지 않는다.
  이번 달 실제 배출량(get_category_trend, category_stats/integrated_stats 기반) x
  카테고리별 감축률(CATEGORY_REDUCTION_RATE, 잠정 가정치)로 계산한 값을 프롬프트에
  못박아 넣고, LLM한테는 "이 숫자를 그대로 문장에 써라"고만 시킨다.
  -> 행동 제안은 다양해지되, 숫자의 재현성/신뢰성은 그대로 유지된다.

주의: category_stats/integrated_stats는 백엔드(Hibernate)가 소유한 실제 운영 스키마다.
AI 자체 SQLAlchemy 모델(app/models/consumption_record.py, consumption_summary_service.py)은
이 테이블들과 컬럼 구조가 달라 실제 데이터와 맞지 않으므로 여기서는 쓰지 않고,
다른 서비스(classifier.py 등)와 동일하게 psycopg2로 직접 조회한다.
"""
from datetime import date

from google import genai

from app.database.connection import get_db_connection

client = genai.Client()


# 카테고리별 감축률(잠정 가정치). 이번 달 실제 배출량에 곱해서 "예상 절감량"을 계산한다.
# 습관을 바꾸기 쉬운 카테고리(쇼핑, 식음료)는 높게, 구조적으로 바꾸기 어려운 카테고리
# (교통수단 자체를 바꿔야 하는 교통 등)는 낮게 잡았다. 실제 배포 전 팀 검토 필요.
CATEGORY_REDUCTION_RATE = {
    "식음료": 0.15,
    "쇼핑소비재": 0.15,
    "가정에너지": 0.10,
    "여가문화": 0.10,
    "대중교통": 0.05,
    "교통": 0.10,
    "건강의료": 0.05,
    "기타": 0.10,
}
DEFAULT_REDUCTION_RATE = 0.10

# LLM에게 방향을 잡아주는 참고용 아이디어 힌트. 고정 카탈로그와 달리 LLM이
# 이 중 하나를 그대로 쓸 필요는 없고, 사용자 상황에 맞게 자유롭게 응용/변형해도 된다.
IDEA_HINTS = {
    "식음료": ["텀블러·다회용기 사용", "외식 대신 집밥", "채식 한 끼 늘리기", "배달 줄이기"],
    "쇼핑소비재": ["중고 거래 활용", "불필요한 구매 줄이기", "장바구니 사용"],
    "가정에너지": ["대기전력 차단", "냉난방 온도 1도 조절", "빨래 모아서 하기"],
    "여가문화": ["실내 온도 관리", "도보로 이동 가능한 여가 활동 선택"],
    "대중교통": ["도보·자전거 병행", "환승 최적화"],
    "교통": ["대중교통으로 전환", "불필요한 이동 줄이기", "카풀"],
    "건강의료": ["도보로 이동 가능한 거리는 걷기"],
    "기타": ["이번 달 지출 내역 점검"],
}


def _get_month_start(base: date, months_ago: int = 0) -> date:
    """base 기준 몇 개월 전 1일을 반환한다."""
    year = base.year
    month = base.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def get_category_trend(user_id: int, conn=None) -> list[dict]:
    """
    이번 달/지난 달 카테고리별 탄소 배출량을 비교해, 이번 달 배출량 기준
    내림차순으로 반환한다. (배출량이 제일 많은 카테고리가 0번 인덱스)
    """
    own_conn = conn is None
    if own_conn:
        conn = get_db_connection()

    today = date.today()
    this_month = _get_month_start(today, 0)
    last_month = _get_month_start(today, 1)

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT ist.period_start, cs.category_name, cs.category_carbon
            FROM category_stats cs
            JOIN integrated_stats ist ON cs.stat_id = ist.stat_id
            WHERE ist.user_id = %s
              AND ist.period_type = 'MONTHLY'
              AND ist.period_start IN (%s, %s);
            """,
            (user_id, this_month, last_month),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        if own_conn:
            conn.close()

    this_month_carbon: dict[str, float] = {}
    last_month_carbon: dict[str, float] = {}

    for period_start, category_name, category_carbon in rows:
        bucket = this_month_carbon if period_start == this_month else last_month_carbon
        bucket[category_name] = bucket.get(category_name, 0.0) + float(category_carbon or 0)

    trend = []
    for category_name, carbon in this_month_carbon.items():
        previous = last_month_carbon.get(category_name, 0.0)
        change_rate = ((carbon - previous) / previous * 100) if previous > 0 else None
        trend.append({
            "category_name": category_name,
            "this_month_carbon_kg": round(carbon, 3),
            "last_month_carbon_kg": round(previous, 3),
            "change_rate_percent": round(change_rate, 1) if change_rate is not None else None,
        })

    trend.sort(key=lambda x: x["this_month_carbon_kg"], reverse=True)
    return trend


def _build_prompt(category_stat: dict, expected_saving_kg: float) -> str:
    change_text = (
        f"지난달 대비 {category_stat['change_rate_percent']}% 변화"
        if category_stat["change_rate_percent"] is not None
        else "지난달 데이터 없음 (이번 달 첫 기록)"
    )

    hints = IDEA_HINTS.get(category_stat["category_name"], IDEA_HINTS["기타"])
    hint_text = ", ".join(hints)

    return f"""
너는 소비 기반 탄소 발자국 플랫폼의 챌린지 추천 AI다.

[사용자의 이번 달 소비 데이터]
카테고리: {category_stat['category_name']}
이번 달 배출량: {category_stat['this_month_carbon_kg']}kg CO2e
{change_text}

[참고 아이디어 (그대로 안 써도 됨, 사용자 상황에 맞게 자유롭게 응용/변형 가능)]
{hint_text}

답변 조건:
1. 이 카테고리에서 실천할 수 있는 구체적인 챌린지를 하나 만들어라. 참고 아이디어를 그대로 써도 되고,
   더 적합하다고 판단되면 비슷한 결의 다른 행동을 직접 제안해도 된다.
2. 목표(예: "이번 주 3회")처럼 실천 가능한 구체적인 횟수/기준을 스스로 정해서 포함해라.
3. 절대 예상 절감량 숫자를 새로 만들어내지 마라. 절감량은 반드시 아래 값을 그대로 써라:
   예상 절감량 = {expected_saving_kg}kg CO2e
4. 사용자의 실제 배출량/변화율을 언급하며 동기부여되는 자연스러운 문장 2~4줄로 작성해라.
5. 마지막 줄에 "예상 절감량 약 {expected_saving_kg}kg CO2e예요." 형식으로 명시해라(숫자를 바꾸지 마라).

답변:
""".strip()


def generate_challenge(user_id: int, conn=None) -> dict:
    """
    사용자의 이번 달 소비 트렌드를 분석해 챌린지 1개를 생성한다.

    행동 문구는 LLM이 자유롭게 작성하지만, 예상 절감량 숫자는 실제 이번 달
    배출량 x 카테고리별 감축률(CATEGORY_REDUCTION_RATE)로 미리 계산해서
    프롬프트에 고정값으로 넣는다 (LLM이 숫자를 지어내지 못하게 하기 위함).

    아직 DB 저장/조회 API는 없는 프로토타입이며, 호출할 때마다 새로 계산한다.
    """
    trend = get_category_trend(user_id, conn=conn)
    if not trend:
        return {
            "user_id": user_id,
            "status": "insufficient_data",
            "message": "아직 챌린지를 추천할 만큼 소비 데이터가 쌓이지 않았습니다.",
        }

    top_category = trend[0]
    reduction_rate = CATEGORY_REDUCTION_RATE.get(top_category["category_name"], DEFAULT_REDUCTION_RATE)
    expected_saving_kg = round(top_category["this_month_carbon_kg"] * reduction_rate, 3)

    prompt = _build_prompt(top_category, expected_saving_kg)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    return {
        "user_id": user_id,
        "status": "ok",
        "category_name": top_category["category_name"],
        "this_month_carbon_kg": top_category["this_month_carbon_kg"],
        "change_rate_percent": top_category["change_rate_percent"],
        "reduction_rate": reduction_rate,
        "expected_saving_kg": expected_saving_kg,
        "message": response.text,
    }
