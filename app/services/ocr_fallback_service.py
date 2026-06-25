"""
OCR 다단계 fallback 오케스트레이터.

파싱 품질이 불충분하거나 엔진 호출 실패 시 순서대로 fallback:
  1차: Google Vision API + receipt_parser_service  (기본, 무료 1000장/월)
  2차: Gemini Vision                               (파싱 품질 불량 시)
  3차: GPT-4o Vision                               (Gemini 호출 실패 시)
  4차: Naver Clova OCR                             (GPT 호출 실패 시)

반환 형식 (parse_receipt_text()와 동일 + 추가 필드):
  {
    "merchant_name": str | None,
    "payment_location": str | None,
    "payment_date": str | None,
    "total_amount": int | None,
    "items": [{"name": str, "price": int}],
    "ocr_engine": str,       # 최종 사용된 엔진명
    "raw_text": str | None,  # Google Vision 원문 (1차에서만 존재)
  }
"""

import re
import logging
from datetime import date
from typing import Dict

from app.services.ocr_service import extract_text_from_image
from app.services.receipt_parser_service import parse_receipt_text
from app.services.gemini_vision_service import extract_receipt_from_gemini_vision
from app.services.gpt_vision_service import extract_receipt_from_gpt_vision
from app.services.clova_ocr_service import extract_receipt_from_clova

logger = logging.getLogger(__name__)


# ── 세부 검증 헬퍼 ─────────────────────────────────────────────────────────────

# 가맹점명 쓰레기 패턴 (OCR이 안내문구·결제정보를 잘못 뽑는 경우)
_MERCHANT_NOISE_KW = {
    "영수증", "receipt", "감사합니다", "이용해", "결제완료", "결제 완료",
    "tax invoice", "invoice", "고객님", "주문번호", "안내", "전화번호",
    "홈페이지", "www.", "http", "사업자번호", "사업자등록", "tel:",
}


def _is_garbage_merchant(name: str) -> bool:
    """
    가맹점명이 쓰레기값인지 판정.

    쓰레기 조건:
      1. 공백 기준 4단어 이상 → 광고/안내 문구일 가능성
      2. 한글·영문 2글자 이상 없음 → 숫자·특수문자만 있는 경우
      3. 알려진 노이즈 키워드 포함
    """
    if not name or not name.strip():
        return True
    # 조건 1: 4단어 이상
    if len(name.split()) >= 4:
        return True
    # 조건 2: 의미있는 문자 없음
    if not re.search(r"[가-힣a-zA-Z]{2,}", name):
        return True
    # 조건 3: 노이즈 키워드
    name_lower = name.lower()
    if any(kw in name_lower for kw in _MERCHANT_NOISE_KW):
        return True
    return False


def _is_garbage_amount(amount: int | float) -> bool:
    """
    총액이 비정상인지 판정.

    쓰레기 조건:
      1. 100원 미만 → 실제 결제금액으로 불가
      2. 9,999,999원 초과 → 일반 영수증 범위 초과
    """
    if amount is None:
        return False  # None은 별도 처리
    if amount < 100:
        return True
    if amount > 9_999_999:
        return True
    return False


def _is_garbage_date(date_str: str) -> bool:
    """
    날짜가 비정상인지 판정 (있을 때만 검사, 없으면 패스).

    쓰레기 조건:
      1. 미래 날짜 → OCR 오인식
      2. 2000년 이전 → 현실적으로 불가
    """
    if not date_str:
        return False
    try:
        d = date.fromisoformat(date_str)
        if d > date.today():
            return True
        if d.year < 2000:
            return True
    except (ValueError, TypeError):
        return True
    return False


# ── 품질 판정 ──────────────────────────────────────────────────────────────────

def _is_poor_quality(parsed: Dict) -> bool:
    """
    파싱 결과가 불량인지 판정.

    불량 조건:
      1. 가맹점명 + 총액 모두 None/쓰레기 → 완전 실패
      2. 가맹점명이 쓰레기값 (광고문구, 노이즈 키워드 등)
      3. 총액이 비정상 범위 (100원 미만 or 1000만원 초과)
      4. 날짜가 미래이거나 2000년 이전 (있을 때만)
      5. 품목 합계 < 총액의 40% → 마트 컬럼 레이아웃 누락 의심
    """
    merchant = parsed.get("merchant_name")
    total = parsed.get("total_amount")
    items = parsed.get("items") or []
    payment_date = parsed.get("payment_date")

    # 조건 1: 완전 실패 (둘 다 None)
    if merchant is None and total is None:
        logger.debug("[품질판정] 실패 - merchant/total 모두 None")
        return True

    # 조건 2: 가맹점명 쓰레기값
    if merchant is not None and _is_garbage_merchant(merchant):
        logger.debug(f"[품질판정] 실패 - 쓰레기 가맹점명: '{merchant}'")
        return True

    # 조건 3: 총액 비정상
    if total is not None and _is_garbage_amount(total):
        logger.debug(f"[품질판정] 실패 - 비정상 총액: {total}")
        return True

    # 조건 4: 날짜 비정상 (있을 때만, merchant/total이 정상이면 단독으로 fail하지 않음)
    # → 날짜만 이상한 경우는 데이터 전체를 버리기보단 날짜만 None 처리 권장
    if payment_date and _is_garbage_date(payment_date):
        logger.debug(f"[품질판정] 날짜 비정상 감지: '{payment_date}' → 날짜만 무시 (패스 계속)")
        parsed["payment_date"] = None  # 날짜만 제거, fallback 유발하지 않음

    # 조건 4-b: 결제 위치 레이블 prefix 잔재 정리
    # OCR이 "소재지:"를 "소:"로 잘라내거나, 1~3글자 레이블이 남는 경우 prefix만 제거
    # (가맹점·총액이 정상이므로 fallback 유발하지 않고 조용히 정리)
    location = parsed.get("payment_location")
    if location and re.match(r"^[가-힣a-zA-Z]{1,4}\s*[:：]\s*", location):
        cleaned_loc = re.sub(r"^[가-힣a-zA-Z]{1,4}\s*[:：]\s*", "", location).strip()
        parsed["payment_location"] = cleaned_loc if len(cleaned_loc) >= 5 else None
        logger.debug(f"[품질판정] 위치 prefix 정리: '{location}' → '{parsed['payment_location']}'")

    # 조건 5: 품목 합계 부족 (마트 컬럼 누락)
    if items and total and total > 0:
        item_sum = sum(
            i["price"] for i in items
            if isinstance(i.get("price"), (int, float)) and i["price"] > 0
        )
        if item_sum < total * 0.4:
            logger.debug(f"[품질판정] 실패 - 품목합계({item_sum}) < 총액({total}) × 0.4")
            return True

    return False


def _score(parsed: Dict) -> int:
    """파싱 결과 품질 점수 (높을수록 좋음). 엔진 간 결과 비교에 사용."""
    s = 0
    if parsed.get("merchant_name"):
        s += 3
    if parsed.get("total_amount"):
        s += 3
    if parsed.get("payment_date"):
        s += 1
    s += len(parsed.get("items") or [])
    return s


# ── 메인 오케스트레이터 ────────────────────────────────────────────────────────

def extract_receipt_with_fallback(image_bytes: bytes) -> Dict:
    """
    다단계 fallback으로 영수증 파싱. ocr.py의 메인 진입점.

    각 단계는 품질 통과 시 즉시 반환, 실패/불량 시 다음 단계로 넘어감.
    모든 단계 실패 시 지금까지 가장 점수 높은 결과 반환.
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
        ocr_text_length = len(raw_text.replace(" ", "").replace("\n", ""))

        if ocr_text_length >= 20:
            parsed = parse_receipt_text(raw_text)
            parsed["ocr_engine"] = "google_vision"
            parsed["raw_text"] = raw_text

            if _score(parsed) > _score(best):
                best = parsed

            if not _is_poor_quality(parsed):
                logger.info("[OCR] 1차 Google Vision 성공")
                return parsed

            logger.info("[OCR] 1차 Google Vision 품질 불량 → Gemini fallback")
        else:
            logger.info("[OCR] 1차 Google Vision 텍스트 부족 → Gemini fallback")

    except Exception as e:
        logger.warning(f"[OCR] 1차 Google Vision 실패: {e}")

    # ── 2차: Gemini Vision ─────────────────────────────────────────────────
    try:
        gemini = extract_receipt_from_gemini_vision(image_bytes)
        gemini["ocr_engine"] = "gemini_vision"
        gemini["raw_text"] = raw_text

        if _score(gemini) > _score(best):
            best = gemini

        if not _is_poor_quality(gemini):
            logger.info("[OCR] 2차 Gemini Vision 성공")
            return gemini

        logger.info("[OCR] 2차 Gemini Vision 품질 불량 → GPT fallback")

    except Exception as e:
        logger.warning(f"[OCR] 2차 Gemini Vision 실패: {e} → GPT fallback")

    # ── 3차: GPT-4o Vision ─────────────────────────────────────────────────
    try:
        gpt = extract_receipt_from_gpt_vision(image_bytes)
        gpt["ocr_engine"] = "gpt_vision"
        gpt["raw_text"] = raw_text

        if _score(gpt) > _score(best):
            best = gpt

        if not _is_poor_quality(gpt):
            logger.info("[OCR] 3차 GPT Vision 성공")
            return gpt

        logger.info("[OCR] 3차 GPT Vision 품질 불량 → Clova fallback")

    except Exception as e:
        logger.warning(f"[OCR] 3차 GPT Vision 실패: {e} → Clova fallback")

    # ── 4차: Naver Clova ───────────────────────────────────────────────────
    try:
        clova = extract_receipt_from_clova(image_bytes)
        clova["ocr_engine"] = "clova"
        clova["raw_text"] = raw_text

        if _score(clova) > _score(best):
            best = clova

        logger.info("[OCR] 4차 Clova 결과 반환")
        return best

    except Exception as e:
        logger.warning(f"[OCR] 4차 Clova 실패: {e}")

    # ── 전체 실패 → 최선 결과 반환 ────────────────────────────────────────
    logger.error("[OCR] 모든 OCR 엔진 실패 → 최선 결과 반환")
    return best
