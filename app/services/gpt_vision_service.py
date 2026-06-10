"""
GPT-4o Vision 영수증 추출 서비스.
Gemini Vision 호출 실패 시 3차 fallback으로 사용.

필요 환경변수 (.env):
  OPENAI_API_KEY
"""

import os
import json
import base64
from typing import Dict

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

GPT_MODEL = "gpt-4o-mini"  # 비용 절감용; 정확도 우선이면 "gpt-4o"로 변경

_RECEIPT_PROMPT = """이 영수증 이미지에서 다음 정보를 추출해서 JSON 형식으로만 반환해줘. 없으면 null.

{
  "merchant_name": "가맹점명",
  "payment_date": "YYYY-MM-DD",
  "total_amount": 숫자(원, 정수),
  "payment_location": "주소",
  "items": [{"name": "품목명", "price": 숫자(정수)}]
}

JSON만 반환. 마크다운 코드블록 없이."""


def extract_receipt_from_gpt_vision(image_bytes: bytes) -> Dict:
    """GPT-4o Vision으로 영수증 구조 추출. parse_receipt_text()와 동일 형식 반환."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": _RECEIPT_PROMPT,
                    },
                ],
            }
        ],
        max_tokens=1000,
    )

    text = response.choices[0].message.content.strip()
    # 마크다운 코드블록 제거
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    raw = json.loads(text)

    total = raw.get("total_amount")
    if isinstance(total, str):
        total = int(total.replace(",", "").replace("원", "").strip()) if total else None

    items = []
    for item in (raw.get("items") or []):
        price = item.get("price")
        if isinstance(price, str):
            price = int(price.replace(",", "").replace("원", "").strip()) if price else None
        name = (item.get("name") or "").strip()
        if name and price and price > 0:
            items.append({"name": name, "price": price})

    return {
        "merchant_name": raw.get("merchant_name"),
        "payment_date": raw.get("payment_date"),
        "total_amount": total,
        "payment_location": raw.get("payment_location"),
        "items": items,
    }
