"""
OCR 다단계 fallback 오케스트레이터.

파싱 품질이 불충분하거나 엔진 호출 실패 시 순서대로 fallback:
  1차: Google Vision API + receipt_parser_service
  2차: Gemini Vision

현재 미사용:
  3차: GPT-4o Vision
  4차: Naver Clova OCR

반환 형식:
{
    "merchant_name": str | None,
    "payment_location": str | None,
    "payment_date": str | None,
    "total_amount": int | None,
    "items": [{"name": str, "price": int}],
    "ocr_engine": str,
    "raw_text": str | None,
}
"""

import logging
import re
from datetime import date
from typing import Dict

from app.services.gemini_vision_service import (
    extract_receipt_from_gemini_vision,
)
from app.services.ocr_service import extract_text_from_image
from app.services.receipt_parser_service import parse_receipt_text

# 현재 사용하지 않는 OCR fallback 엔진
# from app.services.gpt_vision_service import (
#     extract_receipt_from_gpt_vision,
# )
# from app.services.clova_ocr_service import (
#     extract_receipt_from_clova,
# )

logger = logging.getLogger(__name__)


# ── 세부 검증 헬퍼 ─────────────────────────────────────────────────────────────

_MERCHANT_NOISE_KW = {
    "영수증",
    "receipt",
    "감사합니다",
    "이용해",
    "결제완료",
    "결제 완료",
    "tax invoice",
    "invoice",
    "고객님",
    "주문번호",
    "안내",
    "전화번호",
    "홈페이지",
    "www.",
    "http",
    "사업자번호",
    "사업자등록",
    "tel:",
}


def _is_garbage_merchant(name: str) -> bool:
    """
    가맹점명이 쓰레기값인지 판정.

    쓰레기 조건:
      1. 공백 기준 4단어 이상
      2. 한글·영문 2글자 이상 없음
      3. 알려진 노이즈 키워드 포함
    """
    if not name or not name.strip():
        return True

    if len(name.split()) >= 4:
        return True

    if not re.search(r"[가-힣a-zA-Z]{2,}", name):
        return True

    name_lower = name.lower()

    if any(keyword in name_lower for keyword in _MERCHANT_NOISE_KW):
        return True

    return False


def _is_garbage_amount(amount: int | float | None) -> bool:
    """
    총액이 비정상인지 판정.

    쓰레기 조건:
      1. 100원 미만
      2. 9,999,999원 초과
    """
    if amount is None:
        return False

    if amount < 100:
        return True

    if amount > 9_999_999:
        return True

    return False


def _is_garbage_date(date_str: str | None) -> bool:
    """
    날짜가 비정상인지 판정.

    쓰레기 조건:
      1. 미래 날짜
      2. 2000년 이전
      3. 날짜 형식 오류
    """
    if not date_str:
        return False

    try:
        parsed_date = date.fromisoformat(date_str)

        if parsed_date > date.today():
            return True

        if parsed_date.year < 2000:
            return True

    except (ValueError, TypeError):
        return True

    return False


# ── 품질 판정 ──────────────────────────────────────────────────────────────────

def _is_poor_quality(parsed: Dict) -> bool:
    """
    파싱 결과가 불량인지 판정.

    불량 조건:
      1. 가맹점명과 총액이 모두 없음
      2. 가맹점명이 쓰레기값
      3. 총액이 비정상 범위
      4. 날짜가 미래이거나 2000년 이전
      5. 품목 합계가 총액의 40% 미만
    """
    merchant = parsed.get("merchant_name")
    total = parsed.get("total_amount")
    items = parsed.get("items") or []
    payment_date = parsed.get("payment_date")

    if merchant is None and total is None:
        logger.debug(
            "[품질판정] 실패 - merchant/total 모두 None"
        )
        return True

    if merchant is not None and _is_garbage_merchant(merchant):
        logger.debug(
            f"[품질판정] 실패 - 쓰레기 가맹점명: '{merchant}'"
        )
        return True

    if total is not None and _is_garbage_amount(total):
        logger.debug(
            f"[품질판정] 실패 - 비정상 총액: {total}"
        )
        return True

    # 날짜만 이상한 경우 전체 결과를 버리지 않고 날짜만 제거
    if payment_date and _is_garbage_date(payment_date):
        logger.debug(
            f"[품질판정] 날짜 비정상 감지: "
            f"'{payment_date}' → 날짜만 제거"
        )
        parsed["payment_date"] = None

    # 결제 위치 앞에 남은 레이블 제거
    location = parsed.get("payment_location")

    if location and re.match(
        r"^[가-힣a-zA-Z]{1,4}\s*[:：]\s*",
        location,
    ):
        cleaned_location = re.sub(
            r"^[가-힣a-zA-Z]{1,4}\s*[:：]\s*",
            "",
            location,
        ).strip()

        parsed["payment_location"] = (
            cleaned_location
            if len(cleaned_location) >= 5
            else None
        )

        logger.debug(
            f"[품질판정] 위치 prefix 정리: "
            f"'{location}' → "
            f"'{parsed['payment_location']}'"
        )

    # 품목 합계가 총액에 비해 지나치게 적으면 파싱 불량
    if items and total and total > 0:
        item_sum = sum(
            item["price"]
            for item in items
            if isinstance(
                item.get("price"),
                (int, float),
            )
            and item["price"] > 0
        )

        if item_sum < total * 0.4:
            logger.debug(
                f"[품질판정] 실패 - "
                f"품목합계({item_sum}) "
                f"< 총액({total}) × 0.4"
            )
            return True

    return False


def _score(parsed: Dict) -> int:
    """
    파싱 결과 품질 점수.

    점수가 높을수록 유효한 정보가 많이 추출된 결과다.
    """
    score = 0

    if parsed.get("merchant_name"):
        score += 3

    if parsed.get("total_amount"):
        score += 3

    if parsed.get("payment_date"):
        score += 1

    score += len(parsed.get("items") or [])

    return score


# ── 메인 오케스트레이터 ────────────────────────────────────────────────────────

def extract_receipt_with_fallback(
    image_bytes: bytes,
) -> Dict:
    """
    Google Vision → Gemini Vision 순서로 영수증을 파싱한다.

    각 단계는 품질 기준을 통과하면 즉시 반환한다.
    두 엔진 모두 실패하거나 품질이 낮으면,
    지금까지 얻은 결과 중 점수가 가장 높은 결과를 반환한다.
    """
    raw_text: str | None = None

    best: Dict = {
        "merchant_name": None,
        "payment_date": None,
        "total_amount": None,
        "payment_location": None,
        "items": [],
        "ocr_engine": "none",
        "raw_text": None,
    }

    # ── 1차: Google Vision + 커스텀 파서 ──────────────────────────────────
    try:
        raw_text = extract_text_from_image(image_bytes)

        ocr_text_length = len(
            raw_text.replace(" ", "").replace("\n", "")
        )

        if ocr_text_length >= 20:
            parsed = parse_receipt_text(raw_text)
            parsed["ocr_engine"] = "google_vision"
            parsed["raw_text"] = raw_text

            if _score(parsed) > _score(best):
                best = parsed

            if not _is_poor_quality(parsed):
                logger.info(
                    "[OCR] 1차 Google Vision 성공"
                )
                return parsed

            logger.info(
                "[OCR] 1차 Google Vision 품질 불량 "
                "→ Gemini fallback"
            )

        else:
            logger.info(
                "[OCR] 1차 Google Vision 텍스트 부족 "
                "→ Gemini fallback"
            )

    except Exception as error:
        logger.warning(
            f"[OCR] 1차 Google Vision 실패: {error}"
        )

    # ── 2차: Gemini Vision ─────────────────────────────────────────────────
    try:
        gemini = extract_receipt_from_gemini_vision(
            image_bytes
        )

        gemini["ocr_engine"] = "gemini_vision"
        gemini["raw_text"] = raw_text

        if _score(gemini) > _score(best):
            best = gemini

        if not _is_poor_quality(gemini):
            logger.info(
                "[OCR] 2차 Gemini Vision 성공"
            )
            return gemini

        logger.info(
            "[OCR] 2차 Gemini Vision 품질 불량 "
            "→ 최선 결과 반환"
        )

    except Exception as error:
        logger.warning(
            f"[OCR] 2차 Gemini Vision 실패: {error} "
            "→ 최선 결과 반환"
        )

    # ── 3차: GPT-4o Vision (현재 미사용) ─────────────────────────────────
    #
    # try:
    #     gpt = extract_receipt_from_gpt_vision(
    #         image_bytes
    #     )
    #
    #     gpt["ocr_engine"] = "gpt_vision"
    #     gpt["raw_text"] = raw_text
    #
    #     if _score(gpt) > _score(best):
    #         best = gpt
    #
    #     if not _is_poor_quality(gpt):
    #         logger.info(
    #             "[OCR] 3차 GPT Vision 성공"
    #         )
    #         return gpt
    #
    #     logger.info(
    #         "[OCR] 3차 GPT Vision 품질 불량 "
    #         "→ Clova fallback"
    #     )
    #
    # except Exception as error:
    #     logger.warning(
    #         f"[OCR] 3차 GPT Vision 실패: {error} "
    #         "→ Clova fallback"
    #     )

    # ── 4차: Naver Clova OCR (현재 미사용) ────────────────────────────────
    #
    # try:
    #     clova = extract_receipt_from_clova(
    #         image_bytes
    #     )
    #
    #     clova["ocr_engine"] = "clova"
    #     clova["raw_text"] = raw_text
    #
    #     if _score(clova) > _score(best):
    #         best = clova
    #
    #     logger.info(
    #         "[OCR] 4차 Clova 결과 반환"
    #     )
    #     return best
    #
    # except Exception as error:
    #     logger.warning(
    #         f"[OCR] 4차 Clova 실패: {error}"
    #     )

    # ── 사용 가능한 OCR 엔진 실패 → 최선 결과 반환 ───────────────────────
    logger.error(
        "[OCR] Google Vision / Gemini Vision 처리 실패 "
        "→ 최선 결과 반환"
    )

    return best