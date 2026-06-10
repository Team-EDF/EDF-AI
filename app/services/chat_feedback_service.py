from google import genai
from app.services.rag_service import search_rag_context

client = genai.Client()


def generate_chat_feedback(message: str, receipt_data: dict) -> str:
    query = f"""
    사용자 질문: {message}
    소비 카테고리: {receipt_data.get("category")}
    품목: {receipt_data.get("items")}
    탄소배출량: {receipt_data.get("carbon_emission")}
    """

    rag_context = search_rag_context(query)

    prompt = f"""
    너는 소비 기반 탄소 발자국 플랫폼의 실시간 상담형 피드백 AI다.

    [사용자 소비 데이터]
    {receipt_data}

    [참고 문서]
    {rag_context}

    [사용자 질문]
    {message}

    답변 조건:
    1. 소비 데이터가 적으면 월별 패턴이라고 단정하지 말고 '현재 영수증 기준'이라고 말한다.
    2. 참고 문서에 없는 탄소 절감 수치는 임의로 만들지 않는다.
    3. 사용자가 바로 실천 가능한 행동을 2~3개 추천한다.
    4. 카페 소비면 텀블러, 다회용컵, 매장 이용, 일회용컵 줄이기를 우선 추천한다.
    5. 답변은 상담하듯이 자연스럽게 작성한다.
    6. 너무 길게 쓰지 않는다.

    답변:
    """

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text