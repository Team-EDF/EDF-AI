import json
from typing import Dict

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()


def build_feedback_input(parsed_receipt: Dict) -> str:
    items = parsed_receipt.get("items", [])

    item_lines = []

    for item in items:
        item_lines.append(
            f"- 품목명: {item.get('name')} / 금액: {item.get('price')}원"
        )

    return f"""
가맹점명: {parsed_receipt.get("store_name")}
결제일: {parsed_receipt.get("payment_date")}
결제위치: {parsed_receipt.get("payment_location")}
총 결제금액: {parsed_receipt.get("total_amount")}원

구매 품목:
{chr(10).join(item_lines)}
"""


def generate_eco_feedback(parsed_receipt: Dict) -> Dict:

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0.3
    )

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """
너는 친환경 소비 습관 개선 AI다.

사용자의 소비 데이터를 분석해서
친환경 추천 피드백을 제공해라.

반드시 JSON 형식으로 응답해라.
"""
        ),
        (
            "human",
            """
다음 소비 데이터를 분석해줘.

{receipt_data}
"""
        )
    ])

    chain = prompt | llm

    response = chain.invoke({
        "receipt_data": build_feedback_input(parsed_receipt)
    })

    return {
        "feedback": response.content
    }