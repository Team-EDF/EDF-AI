import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv
from typing import Dict

load_dotenv()

GEMINI_MODEL = "gemini-2.5-flash"
_PROJECT_ID = os.getenv("GEMINI_PROJECT_ID", "gen-lang-client-0224879870")
_LOCATION   = os.getenv("GEMINI_LOCATION", "us-central1")

_RECEIPT_PROMPT = """이 영수증 이미지에서 다음 정보를 추출해서 JSON 형식으로만 반환해줘. 없으면 null.

{
  "merchant_name": "가맹점명",
  "payment_date": "YYYY-MM-DD",
  "total_amount": 숫자(원, 정수),
  "payment_location": "주소",
  "items": [{"name": "품목명", "price": 숫자(정수)}]
}

JSON만 반환. 마크다운 없이."""


def extract_receipt_from_gemini_vision(image_bytes: bytes) -> Dict:
    """Gemini Vision으로 영수증 구조 추출. parse_receipt_text()와 동일 형식 반환."""
    client = genai.Client(vertexai=True, project=_PROJECT_ID, location=_LOCATION)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            _RECEIPT_PROMPT,
        ]
    )

    text = response.text.strip()
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