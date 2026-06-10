"""
Naver Clova OCR 영수증 도메인 연동 서비스.

Google Vision API + receipt_parser_service 조합 대신,
Clova가 직접 반환하는 구조화된 JSON을 파싱한다.

반환 형식은 parse_receipt_text()와 동일:
  {
    "merchant_name": str | None,
    "payment_location": str | None,
    "payment_date": str | None,   # "YYYY-MM-DD"
    "total_amount": int | None,
    "items": [{"name": str, "price": int}, ...]
  }

필요 환경변수 (.env):
  CLOVA_OCR_API_URL    - Naver APIGW Invoke URL (영수증 도메인)
  CLOVA_OCR_SECRET_KEY - OCR Secret Key
"""

import os
import re
import uuid
import base64
from typing import Optional, List, Dict

import requests
from dotenv import load_dotenv

load_dotenv()

CLOVA_API_URL: str | None = os.getenv("CLOVA_OCR_API_URL")
CLOVA_SECRET_KEY: str | None = os.getenv("CLOVA_OCR_SECRET_KEY")


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _text(node: dict | None) -> str | None:
    """Clova JSON 노드에서 text 필드 안전 추출."""
    if not isinstance(node, dict):
        return None
    return node.get("text") or None


def _parse_amount(raw: str | None) -> int | None:
    """금액 문자열(쉼표·원·공백 포함) → int. 파싱 실패 시 None."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d]", "", raw)
    return int(cleaned) if cleaned else None


def _normalize_date(raw: str | None) -> str | None:
    """Clova 날짜 문자열 → YYYY-MM-DD."""
    if not raw:
        return None
    # 4자리 연도
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    # 2자리 연도 (26-05-03 → 2026-05-03)
    m = re.search(r"(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw)
    if m:
        return f"20{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    return None


# ── Clova API 호출 ────────────────────────────────────────────────────────────

def call_clova_ocr(image_bytes: bytes, image_format: str = "jpeg") -> dict:
    """
    Naver Clova OCR API 호출 → raw response dict 반환.

    Args:
        image_bytes: 영수증 이미지 바이트
        image_format: 이미지 포맷 (jpeg / png / pdf 등)

    Raises:
        RuntimeError: 환경변수 미설정
        requests.HTTPError: API 오류
    """
    if not CLOVA_API_URL or not CLOVA_SECRET_KEY:
        raise RuntimeError(
            "CLOVA_OCR_API_URL 또는 CLOVA_OCR_SECRET_KEY 환경변수가 설정되지 않았습니다."
        )

    payload = {
        "version": "V2",
        "requestId": str(uuid.uuid4()),
        "timestamp": 0,
        "lang": "ko",
        "images": [
            {
                "format": image_format,
                "name": "receipt",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            }
        ],
        "enableTableDetect": False,
    }

    resp = requests.post(
        CLOVA_API_URL,
        json=payload,
        headers={
            "X-OCR-SECRET": CLOVA_SECRET_KEY,
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ── Clova 응답 파싱 ───────────────────────────────────────────────────────────

def parse_clova_receipt(clova_response: dict) -> dict:
    """
    Clova OCR raw response → parse_receipt_text()와 동일한 dict 반환.

    Clova 영수증 도메인 응답 구조:
      images[0].receipt.result
        .storeInfo.name.text          → 가맹점명
        .storeInfo.addresses[0].text  → 주소
        .paymentInfo.date.text        → 결제일
        .totalPrice.price.text        → 총액
        .subResults[].items[].name.text          → 품목명
        .subResults[].items[].price.price.text   → 품목 금액
    """
    _empty = {
        "merchant_name": None,
        "payment_location": None,
        "payment_date": None,
        "total_amount": None,
        "items": [],
    }

    images = clova_response.get("images", [])
    if not images:
        return _empty

    img = images[0]
    if img.get("inferResult") != "SUCCESS":
        return _empty

    result: dict = img.get("receipt", {}).get("result", {})
    if not result:
        return _empty

    # ── 가맹점명 ──────────────────────────────────────────────────────
    store = result.get("storeInfo", {})
    merchant_name = _text(store.get("name"))
    sub_name = _text(store.get("subName"))
    if merchant_name and sub_name:
        merchant_name = f"{merchant_name} {sub_name}"

    # ── 결제 위치 (주소 첫 번째) ──────────────────────────────────────
    addresses = store.get("addresses", [])
    payment_location: str | None = None
    if addresses:
        payment_location = addresses[0].get("text")

    # ── 결제 일자 ─────────────────────────────────────────────────────
    pay_info = result.get("paymentInfo", {})
    payment_date = _normalize_date(_text(pay_info.get("date")))

    # ── 총액 ──────────────────────────────────────────────────────────
    total_amount = _parse_amount(
        _text(result.get("totalPrice", {}).get("price"))
    )

    # ── 품목 ──────────────────────────────────────────────────────────
    items: List[Dict] = []
    for sub in result.get("subResults", []):
        for item in sub.get("items", []):
            name = _text(item.get("name"))
            price = _parse_amount(
                _text(item.get("price", {}).get("price"))
            )
            if name and price and price > 0:
                items.append({"name": name.strip(), "price": price})

    return {
        "merchant_name": merchant_name,
        "payment_location": payment_location,
        "payment_date": payment_date,
        "total_amount": total_amount,
        "items": items,
    }


# ── 공개 진입점 ───────────────────────────────────────────────────────────────

def extract_receipt_from_clova(image_bytes: bytes) -> dict:
    """
    이미지 bytes → Clova OCR 호출 → 파싱된 영수증 dict 반환.
    parse_receipt_text()와 동일한 반환 형식이므로 routes/ocr.py에서 교체 사용 가능.
    """
    raw = call_clova_ocr(image_bytes)
    return parse_clova_receipt(raw)
