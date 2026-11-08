"""
OCR 다단계 fallback 오케스트레이터.

1차: Google Vision API + receipt_parser_service
     → 숫자 교차검증(총액/품목합계/수량) 후 문제가 있으면
2차: Gemini Vision 으로 재추출하고, 두 결과 중 숫자가 맞는 쪽을 채택

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
    "document_type": "receipt" | "order_form" | "other",
    "quality": {"level": "high"|"medium"|"low"|"unreadable", "notes": [str], ...},
    "receipt_fingerprint": str | None,
}
"""

import logging
import re
from datetime import date
from typing import Dict

from app.services import receipt_validation_service as validation
from app.services.gemini_vision_service import (
    extract_receipt_from_gemini_vision,
)
from app.services.ocr_service import extract_text_and_quality
from app.services.receipt_parser_service import parse_receipt_text

# 현재 사용하지 않는 OCR fallback 엔진
# from app.services.gpt_vision_service import (
#     extract_receipt_from_gpt_vision,
# )
# from app.services.clova_ocr_service import (
#     extract_receipt_from_clova,
# )

logger = logging.getLogger(__name__)

# 품목 합계 / 총액 비율이 이 범위를 벗어나면 품목을 믿지 않는다.
# 합계가 총액보다 큰 쪽은 할인·쿠폰 줄이 빠진 경우가 많아 넉넉하게 허용한다.
ITEM_KEEP_MIN_RATIO = 0.6
ITEM_KEEP_MAX_RATIO = 1.6

# 단어 평균 신뢰도가 이보다 낮으면 (그리고 숫자도 안 맞으면) 판독 불가
MIN_WORD_CONFIDENCE = 0.70


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
    "담당자",
    "관리자",
}


# 가맹점명 자리에 자주 잘못 들어오는 제목/안내 문구
_MERCHANT_NOT_NAMES = {
    "영수",
    "판촉",
    "상사주",
    "결제정보",
    "응모기간 5",
    "전체관람가",
}


_SLIP_TITLE_RE = re.compile(
    r"^(IC|MS)?\s*(신용|체크|현금)\s*(구매|승인|카드|영수증)?\s*(매출)?\s*(전표)?\s*(\(?고객용\)?)?$"
    r"|^(카드|매출)\s*(매출)?\s*전표$|^가맹점\s*명?$"
)


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

    # 수량/번호로 시작하거나('1 베이컨…'), 층·호수가 들어 있으면(주소 조각) 상호가 아니다.
    if re.match(r"^\d+\s", name.strip()) or re.search(r"\d+\s*(층|호)", name):
        return True

    if name.strip() in _MERCHANT_NOT_NAMES:
        return True

    # '테이블 먹고 가기', '테이블: 4'처럼 주문 정보 줄은 상호가 아니다.
    if name.strip().startswith("테이블"):
        return True

    # '[신용구매]', 'IC신용승인', '신용카드 매출전표' 같은 전표 제목은 상호가 아니다.
    if _SLIP_TITLE_RE.match(name.strip().strip("[]()<>* ")):
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


_SUM_RANK = {"match": 3, "close": 2, "noitem": 1, "mismatch": 0}


def _rank(parsed: Dict) -> tuple:
    """
    후보 결과 비교용 순위. 숫자가 서로 맞는(품목합계 == 총액) 결과를 우선한다.
    """
    merchant = parsed.get("merchant_name")

    return (
        # 아무것도 못 읽은 빈 결과는 어떤 실제 결과보다도 낮게 본다.
        bool(
            merchant
            or parsed.get("total_amount")
            or parsed.get("items")
        ),
        _SUM_RANK[validation.sum_status(parsed)],
        bool(merchant and not _is_garbage_merchant(merchant)),
        bool(parsed.get("total_amount")),
        bool(parsed.get("payment_date")),
        # 숫자가 같으면 품목명이 깨끗한 쪽을 택한다.
        not validation.has_suspicious_item_names(parsed),
        len(parsed.get("items") or []),
    )


# ── 보정 / 재확인 판단 ─────────────────────────────────────────────────────────

def _refine(parsed: Dict, raw_text: str | None) -> list[str]:
    """총액(공급가액 오인) → 수량 순으로 보정하고, 적용한 보정 목록을 반환한다."""
    notes: list[str] = []

    if not parsed.get("items") and parsed.get("items") is None:
        parsed["items"] = []

    if raw_text and validation.fix_total_amount(parsed, raw_text):
        notes.append("총액을 공급가액+부가세로 보정")

    if validation.drop_address_items(parsed, raw_text):
        notes.append("주소 조각이 품목으로 읽힌 항목 제거")

    if validation.reconcile_quantities(parsed):
        notes.append("수량 반영해 품목 금액 보정")

    return notes


def _has_item_table(raw_text: str | None) -> bool:
    compact = re.sub(r"\s+", "", raw_text or "")

    return "단가" in compact and "수량" in compact


def _needs_second_opinion(parsed: Dict, raw_text: str | None) -> bool:
    """규칙 파서 결과를 Gemini로 다시 확인해야 하는지."""
    if _is_poor_quality(parsed):
        return True

    if (
        not parsed.get("merchant_name")
        or not parsed.get("total_amount")
        or not parsed.get("payment_date")
    ):
        return True

    status = validation.sum_status(parsed)

    if status == "mismatch":
        return True

    # 품목이 있어 보이는데(표 머리글 또는 '단가×수량=금액' 줄) 하나도 못 읽은 경우.
    # '품목 없음'은 규칙 파서 혼자 결론 내지 않고 Gemini와 교차 확인한다.
    if status == "noitem" and (
        _has_item_table(raw_text) or validation.has_item_line(raw_text)
    ):
        return True

    # 합계는 맞지만 품목명이 깨져 읽힌 경우 (분류가 틀어진다)
    if validation.has_suspicious_item_names(parsed):
        return True

    return False


def _finalize(
    best: Dict,
    raw_text: str | None,
    ocr_stats: Dict | None,
    notes: list[str],
    gemini_doc_type: str | None,
    gemini_legible: bool | None,
) -> Dict:
    """품질 등급과 중복 지문을 붙여 최종 결과를 만든다."""
    total = best.get("total_amount")

    # 문서 유형: 결제 내역이 없는 서류는 영수증이 아니다.
    doc_type = validation.classify_document(raw_text)

    if gemini_doc_type in ("order_form", "other"):
        doc_type = gemini_doc_type

    best["document_type"] = doc_type

    # 품목 합계가 총액과 어긋나는 경우:
    #  - 총액보다 약간 크면 할인/쿠폰 줄이 빠진 것이라 품목은 맞게 읽은 것 → 유지
    #  - 총액보다 훨씬 작거나(품목 대부분 누락) 훨씬 크면(엉뚱한 줄) 믿지 않고
    #    가맹점 기준 추정으로 넘긴다.
    status = validation.sum_status(best)

    if status == "mismatch" and total:
        ratio = validation.item_sum(best) / total

        if ratio < ITEM_KEEP_MIN_RATIO or ratio > ITEM_KEEP_MAX_RATIO:
            best["items"] = []
            notes.append("품목 금액이 총액과 맞지 않아 가맹점 기준으로 추정")
            status = "noitem"
        elif ratio > 1:
            notes.append("품목 합계가 총액보다 큼 (할인 반영 안 됨)")

    word_mean = (ocr_stats or {}).get("word_mean")
    reason = None

    if doc_type != "receipt":
        level = "unreadable"
        reason = "NOT_A_RECEIPT"
    elif (total is not None and total < 0) or validation.is_refund(raw_text):
        # 환불/취소 영수증은 소비 기록으로 저장하지 않는다.
        level = "unreadable"
        reason = "REFUND"
        notes.append("환불·취소 영수증")
    elif not total:
        level = "unreadable"
        reason = "UNREADABLE"
        notes.append("총액을 읽지 못함")
    elif gemini_legible is False and status not in ("match", "close"):
        level = "unreadable"
        reason = "UNREADABLE"
        notes.append("글자가 흐려 숫자를 확인하지 못함")
    elif word_mean is not None and word_mean < MIN_WORD_CONFIDENCE:
        # 글자가 심하게 흐리면 숫자가 우연히 맞아 보여도 믿지 않는다.
        level = "unreadable"
        reason = "UNREADABLE"
        notes.append("글자 인식 신뢰도가 낮음")
    elif status in ("match", "close"):
        level = "high"
    elif best.get("merchant_name") and best.get("payment_date"):
        level = "medium"
    else:
        level = "low"

    best["quality"] = {
        "level": level,
        "reason": reason,
        "notes": notes,
        "item_sum_status": status,
        "ocr_word_mean": word_mean,
    }

    best["receipt_fingerprint"] = validation.make_receipt_fingerprint(
        best,
        raw_text,
    )

    return best


# ── 메인 오케스트레이터 ────────────────────────────────────────────────────────

def extract_receipt_with_fallback(
    image_bytes: bytes,
) -> Dict:
    """
    Google Vision → (숫자 교차검증) → Gemini Vision 순서로 영수증을 읽는다.

    1차 결과가 숫자 검증을 통과하면 그대로 쓰고, 통과하지 못하면 Gemini로
    다시 읽어 두 결과 중 숫자가 더 잘 맞는 쪽을 채택한다.
    """
    raw_text: str | None = None
    ocr_stats: Dict | None = None
    notes: list[str] = []

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
        raw_text, ocr_stats = extract_text_and_quality(image_bytes)

        ocr_text_length = len(
            raw_text.replace(" ", "").replace("\n", "")
        )

        if ocr_text_length >= 20:
            # 주문서처럼 결제 내역이 없는 서류는 더 읽지 않고 바로 거른다.
            if validation.classify_document(raw_text) != "receipt":
                logger.info("[OCR] 영수증이 아닌 서류로 판정")

                parsed = parse_receipt_text(raw_text)
                parsed["ocr_engine"] = "google_vision"
                parsed["raw_text"] = raw_text

                return _finalize(
                    parsed, raw_text, ocr_stats, notes, None, None
                )

            parsed = parse_receipt_text(raw_text)
            parsed["ocr_engine"] = "google_vision"
            parsed["raw_text"] = raw_text
            notes.extend(_refine(parsed, raw_text))

            if _rank(parsed) > _rank(best):
                best = parsed

            if not _needs_second_opinion(parsed, raw_text):
                logger.info(
                    "[OCR] 1차 Google Vision 성공"
                )
                return _finalize(
                    parsed, raw_text, ocr_stats, notes, None, None
                )

            logger.info(
                "[OCR] 1차 Google Vision 숫자 검증 미통과 "
                "→ Gemini 재확인"
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
    gemini_doc_type = None
    gemini_legible = None

    try:
        gemini = extract_receipt_from_gemini_vision(
            image_bytes
        )

        gemini["ocr_engine"] = "gemini_vision"
        gemini["raw_text"] = raw_text
        gemini_doc_type = gemini.pop("document_type", None)
        gemini_legible = gemini.pop("legible", None)

        # Gemini가 품목 없는 카드 전표라고 보면, 규칙 파서도 품목이 없었던 경우에 한해
        # Gemini가 지어낸 품목(합계와 같은 금액 한 줄 등)을 믿지 않는다.
        if gemini_doc_type == "card_slip" and not best.get("items"):
            gemini["items"] = []

        gemini_notes = _refine(gemini, raw_text)

        # 두 엔진이 같은 총액을 읽었으면 신뢰도가 높다.
        if (
            best.get("total_amount")
            and best["total_amount"] == gemini.get("total_amount")
        ):
            gemini_notes.append("두 엔진의 총액이 일치")

        if _rank(gemini) > _rank(best):
            best = gemini
            notes = gemini_notes
        elif best is not gemini and gemini_notes:
            notes.extend(
                n for n in gemini_notes if n == "두 엔진의 총액이 일치"
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
    # except Exception as error:
    #     logger.warning(
    #         f"[OCR] 3차 GPT Vision 실패: {error}"
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
    # except Exception as error:
    #     logger.warning(
    #         f"[OCR] 4차 Clova 실패: {error}"
    #     )

    return _finalize(
        best,
        raw_text,
        ocr_stats,
        notes,
        gemini_doc_type,
        gemini_legible,
    )
