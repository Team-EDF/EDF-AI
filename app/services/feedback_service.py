import os
from google import genai
from app.services.rag_service import get_rag_context

_PROJECT_ID = os.getenv("GEMINI_PROJECT_ID", "gen-lang-client-0224879870")
_LOCATION   = os.getenv("GEMINI_LOCATION", "us-central1")

client = genai.Client(vertexai=True, project=_PROJECT_ID, location=_LOCATION)

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
    사용자 질문: {user_message}
    """

    rag_context = get_rag_context(query)

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

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    return response.text