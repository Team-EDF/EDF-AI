from google import genai
from app.services.rag_service import search_rag

client = genai.Client()


def generate_feedback(user_message: str, consumption_summary: dict):
    total_carbon_kg = consumption_summary.get("total_carbon_kg")
    category_carbon_summary = consumption_summary.get("category_carbon_summary")
    category_amount_summary = consumption_summary.get("category_amount_summary")
    items = consumption_summary.get("items", [])

    query = f"""
    사용자 질문: {user_message}
    총 탄소배출량: {consumption_summary.get("total_carbon_emission")}
    카테고리별 탄소배출량: {consumption_summary.get("category_summary")}
    소비 품목: {consumption_summary.get("items")}
    """
    try:
        print("[RAG 검색] 시작")

        rag_result = search_rag(query)
        rag_context = rag_result["context"]
        rag_sources = rag_result["sources"]

        print("====== RAG 검색 결과 ======")
        print(rag_context[:500])
        print("=========================")
        print(f"[RAG 검색] source 개수: {len(rag_sources)}")

    except Exception as e:
        print("[RAG 오류]", repr(e))
        rag_context = "RAG 참고자료를 불러오지 못했습니다."
        rag_sources = []

    prompt = f"""
너는 소비 기반 탄소 발자국 플랫폼의 친환경 피드백 AI다.

아래 데이터는 사용자의 소비 데이터를 탄소배출량 기준으로 집계한 정보다.

[사용자 탄소배출량 요약]
총 탄소배출량: {total_carbon_kg} kgCO2e
카테고리별 탄소배출량: {category_carbon_summary}
카테고리별 소비금액: {category_amount_summary}
품목별 정보: {items}

[RAG 참고자료]
{rag_context}

[사용자 질문]
{user_message}

답변 조건:
- 반드시 탄소배출량을 기준으로 우선순위를 판단한다.
- 소비금액 기준이 아니라 category_carbon_summary 기준으로 가장 높은 카테고리를 먼저 분석한다.
- total_carbon_kg가 없으면 '초기 탄소 피드백'이라고 표현한다.
- 근거 없는 탄소 절감 수치는 만들지 않는다.
- RAG 참고자료에 없는 구체적인 수치를 만들어내지 않는다.
- 사용자가 실천 가능한 행동을 2~3개 추천한다.
- 식품 소비가 높으면 육류 소비 줄이기, 채식 대체, 음식물 쓰레기 감소를 고려한다.
- 카페/음료 소비가 높으면 텀블러, 다회용컵, 매장 이용을 고려한다.
- 교통 소비가 높으면 대중교통, 도보, 자전거 이용을 고려한다.
- 답변 마지막에 참고한 문서를 간단히 언급한다.
- 말투는 친절하지만 간결하게 한다.

답변:
"""
    try:
        print("[Gemini 호출] feedback 생성 시작")

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        print("[Gemini 호출] feedback 생성 완료")

        return {
            "answer": response.text,
            "rag_sources": rag_sources
        }

    except Exception as e:
        print("[Gemini 오류]", repr(e))

        fallback_answer = make_rule_based_feedback(
            total_carbon_kg=total_carbon_kg,
            category_carbon_summary=category_carbon_summary,
            items=items
        )

        return {
            "answer": fallback_answer,
            "rag_sources": rag_sources
        }


def make_rule_based_feedback(total_carbon_kg, category_carbon_summary, items):
    if not category_carbon_summary:
        return (
            "초기 탄소 피드백입니다.\n\n"
            "아직 카테고리별 탄소배출량 데이터가 부족합니다. "
            "영수증 데이터가 더 쌓이면 어떤 소비 영역에서 탄소배출이 큰지 더 정확히 분석할 수 있습니다."
        )

    top_category = max(category_carbon_summary, key=category_carbon_summary.get)
    top_carbon = category_carbon_summary[top_category]

    feedback = f"초기 탄소 피드백입니다.\n\n"
    feedback += f"현재 집계된 총 탄소배출량은 약 {total_carbon_kg} kgCO2e입니다.\n"
    feedback += f"가장 탄소배출 비중이 높은 카테고리는 '{top_category}'이며, 약 {top_carbon} kgCO2e로 나타났습니다.\n\n"

    if "식품" in top_category:
        feedback += (
            "식품 소비에서는 필요한 만큼만 구매하고 음식물 쓰레기를 줄이는 것이 중요합니다. "
            "또한 육류 중심 식단을 일부 식물성 식품으로 대체하는 방식도 탄소 절감에 도움이 될 수 있습니다."
        )   
    elif "카페" in top_category or "음료" in top_category:
        feedback += (
            "카페/음료 소비에서는 텀블러나 다회용컵을 사용하고, 일회용 컵 사용을 줄이는 습관을 추천합니다."
        )
    elif "교통" in top_category:
        feedback += (
            "교통 소비에서는 가까운 거리는 도보나 자전거를 이용하고, 가능한 경우 대중교통 이용 비중을 높이는 것을 추천합니다."
        )
    else:
        feedback += (
            "탄소배출량이 높은 카테고리의 소비 빈도를 점검하고, 대체 가능한 저탄소 소비 방식을 선택해보는 것을 추천합니다."
        )

    return feedback