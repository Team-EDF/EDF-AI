import os
import json
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv
from typing import Dict

load_dotenv()

GEMINI_MODEL = "gemini-2.5-flash"
_PROJECT_ID = os.getenv("GEMINI_PROJECT_ID", "gen-lang-client-0224879870")
_LOCATION   = os.getenv("GEMINI_LOCATION", "us-central1")

# 일시적 오류(429/5xx)일 때 재시도 횟수와 대기 시간(초)
_RETRY_DELAYS = (2, 5)

# 프롬프트를 바꾸면 올려서, 일괄 테스트 캐시가 옛 응답을 재사용하지 않게 한다.
PROMPT_VERSION = 2

_RECEIPT_PROMPT = """이 이미지는 영수증 또는 카드 매출전표 사진입니다. 다음 정보를 JSON 형식으로만 반환해줘. 모르면 null.

{
  "document_type": "receipt | card_slip | order_form | other",
  "legible": true 또는 false,
  "merchant_name": "가맹점(상호)명",
  "payment_date": "YYYY-MM-DD",
  "total_amount": 숫자(원, 정수),
  "payment_location": "주소",
  "items": [{"name": "품목명", "quantity": 숫자(정수, 모르면 1), "amount": 숫자(정수)}]
}

규칙:
- document_type: 품목이 나열된 영수증은 receipt, 카드 승인 정보만 있는 매출전표는 card_slip, 주문서·견적서·교환권처럼 결제 내역이 없는 서류는 order_form, 영수증이 아닌 사진은 other.
- legible: 글자가 흐리거나 번지거나 잘려서 금액을 확실하게 읽을 수 없으면 false.
- total_amount: 실제 결제한 합계 금액(공급가액+부가세). 공급가액만이 아니다.
- items.amount: 그 줄의 최종 금액(단가 × 수량). 단가가 아니다. 예: 단가 9,000 수량 2 이면 amount는 18000.
- 봉사료, 부가세, 합계, 카드 승인 정보, 포인트, 봉투·할인 줄은 품목이 아니다.
- 읽을 수 없는 글자를 추측해서 만들지 않는다.

JSON만 반환. 마크다운 없이."""


def _to_int(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = str(value).replace(",", "").replace("원", "").strip()
    return int(cleaned) if cleaned.lstrip("-").isdigit() else None


def _generate(image_bytes: bytes) -> str:
    client = genai.Client(vertexai=True, project=_PROJECT_ID, location=_LOCATION)

    last_error: Exception | None = None

    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    _RECEIPT_PROMPT,
                ],
            )
            return response.text.strip()
        except Exception as error:
            last_error = error
            message = str(error)
            transient = any(
                code in message
                for code in ("429", "500", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE")
            )

            if not transient or attempt >= len(_RETRY_DELAYS):
                break

            time.sleep(_RETRY_DELAYS[attempt])

    raise last_error


def extract_receipt_from_gemini_vision(image_bytes: bytes) -> Dict:
    """Gemini Vision으로 영수증 구조 추출. parse_receipt_text()와 동일 형식 반환."""
    text = _generate(image_bytes)

    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    raw = json.loads(text)

    total = _to_int(raw.get("total_amount"))

    items = []
    for item in (raw.get("items") or []):
        # 줄 금액(amount)을 우선 사용하고, 옛 응답 형식(price)도 허용한다.
        price = _to_int(item.get("amount"))
        if price is None:
            price = _to_int(item.get("price"))
        name = (item.get("name") or "").strip()
        if name and price and price > 0:
            entry = {"name": name, "price": price}
            quantity = _to_int(item.get("quantity"))
            if quantity and quantity > 1:
                entry["quantity"] = quantity
            items.append(entry)

    return {
        "merchant_name": raw.get("merchant_name"),
        "payment_date": raw.get("payment_date"),
        "total_amount": total,
        "payment_location": raw.get("payment_location"),
        "items": items,
        "document_type": raw.get("document_type"),
        "legible": raw.get("legible"),
    }
