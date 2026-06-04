from google import genai
from app.services.rag_service import get_rag_context

client = genai.Client()


def generate_feedback(user_message: str, consumption_summary: dict):
    query = f"""
    소비 카테고리: {consumption_summary.get("category")}
    소비 품목: {consumption_summary.get("items")}
    사용자 질문: {user_message}
    """

    rag_context = get_rag_context(query)

    prompt = f"""
    너는 소비 기반 탄소 발자국 플랫폼의 친환경 피드백 AI다.

    [사용자 소비 정보]
    {consumption_summary}

    [RAG 참고자료]
    {rag_context}

    [사용자 질문]
    {user_message}

    조건:
    - 소비 데이터가 적으면 '초기 피드백'이라고 표현한다.
    - 근거 없는 탄소 절감 수치는 만들지 않는다.
    - 사용자가 실천 가능한 행동을 2~3개 추천한다.
    - 카페 소비라면 텀블러, 다회용컵, 매장 이용 등을 우선 고려한다.
    - 말투는 친절하지만 간결하게 한다.

    답변:
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    return response.text