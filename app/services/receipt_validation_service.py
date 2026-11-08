"""
영수증 추출 결과 검증/보정.

OCR 결과를 "믿을 수 있는지" 숫자로 교차검증한다. 글자가 흐리거나 번지면
금액 한두 자리가 틀어지는데, 이는 아래 검증 중 하나에서 어긋나므로
별도의 흐림 판정 없이도 자연스럽게 걸러진다.

  - 품목 합계 == 총액
  - 총액 == 공급가액 + 부가세
  - 수량이 2 이상인 줄은 단가가 아니라 줄 금액(단가×수량)으로 보정
  - 결제 정보가 없는 서류(주문서 등)는 영수증이 아님
  - 같은 영수증을 다시 올렸는지 판별하는 지문(fingerprint)
"""

import hashlib
import re
from typing import Dict, List, Optional

from app.services.receipt_parser_service import extract_amounts

# 합계 비교 허용 오차 (할인/반올림)
SUM_CLOSE_RATIO = 0.02

# 영수증이 아닌 서류의 제목 키워드 (공백 제거 후 비교)
_NON_RECEIPT_TITLES = (
    "주문서",
    "견적서",
    "거래명세서",
    "발주서",
    "주문접수",
)

# 결제가 이루어졌음을 보여주는 단어 (공백 제거 후 비교)
_PAYMENT_EVIDENCE = (
    "승인번호",
    "카드번호",
    "신용카드",
    "현금영수증",
    "부가세",
    "결제금액",
    "받은금액",
    "받을금액",
    "공급가액",
    "과세물품",
    "매출전표",
    "영수증",
)

_DATE_IN_TEXT_RE = re.compile(
    r"(20\d{2}|\d{2})\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}"
)
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*:\s*([0-5]\d)(?!\d)")


def _all_amounts(raw_text: str) -> List[int]:
    amounts: List[int] = []

    for line in (raw_text or "").splitlines():
        amounts.extend(extract_amounts(line))

    return amounts


def item_sum(parsed: Dict) -> int:
    return sum(
        int(item["price"])
        for item in (parsed.get("items") or [])
        if isinstance(item.get("price"), (int, float))
        and item["price"] > 0
    )


def sum_status(parsed: Dict) -> str:
    """
    품목 합계와 총액의 일치 상태.

    match    : 정확히 일치
    close    : 2% 이내 (반올림/소액 할인)
    mismatch : 불일치
    noitem   : 품목 없음 (카드 매출전표 등)
    """
    items = parsed.get("items") or []
    total = parsed.get("total_amount")

    if not items:
        return "noitem"

    if not total:
        return "mismatch"

    diff = abs(item_sum(parsed) - total)

    if diff == 0:
        return "match"

    if diff <= max(1, total * SUM_CLOSE_RATIO):
        return "close"

    return "mismatch"


# ── 보정 ──────────────────────────────────────────────────────────────────────

def fix_total_amount(parsed: Dict, raw_text: str) -> bool:
    """
    카드 매출전표에서 총액을 공급가액으로 잘못 잡은 경우를 바로잡는다.

    총액 T 에 대해 부가세(T의 약 10%)와 T+부가세가 모두 같은 영수증에
    적혀 있으면, T는 공급가액이고 실제 총액은 T+부가세이다.
    """
    total = parsed.get("total_amount")

    # 부가세(예: 182원)가 총액으로 잡힌 경우도 있어 총액 하한은 두지 않는다.
    # 대신 아래에서 공급가액이 1,000원 이상이고 합계까지 영수증에 있어야만 보정한다.
    if not total:
        return False

    amounts = sorted(set(_all_amounts(raw_text)))

    # (공급가액 supply, 부가세 vat, 합계 grand) 조합을 찾는다.
    # 파서가 잡은 총액이 공급가액이나 부가세 중 하나면 합계로 바로잡는다.
    for supply in amounts:
        if supply < 1000:
            continue

        for vat in amounts:
            if abs(vat - supply * 0.1) > 1.5:
                continue

            for grand in amounts:
                if (
                    grand > supply
                    and abs(grand - (supply + vat)) <= 1
                    and total in (supply, vat)
                ):
                    parsed["total_amount"] = grand
                    return True

    return False


# 품목명 자리에 영수증 표 머리글·합계 줄이 잘못 들어온 경우의 단어
_NOISE_NAME_WORDS = (
    "금액", "합계", "수량", "단가", "판매", "받을", "받은",
    "공급", "부가", "거스름", "할인", "포인트",
)

# 그 자체로는 품목을 알 수 없는 일반 명칭 ('입장권 성인', '일반' 등)
_GENERIC_NAMES = {"일반", "성인", "소인", "대인", "기본", "청소년", "어린이"}


def is_suspicious_item_name(name: Optional[str]) -> bool:
    """
    OCR이 품목명을 제대로 못 읽었을 가능성이 큰 이름인지 판정한다.

    합계가 우연히 맞아도 품목명이 깨져 있으면 분류가 틀리므로,
    이런 이름이 있으면 Gemini로 다시 읽어 비교한다.
    """
    text = (name or "").strip()

    if not text:
        return True

    if any(word in text for word in _NOISE_NAME_WORDS):
        return True

    # '2스쿱'처럼 수량 + 단위만 남은 이름 ('3분카레' 같은 정상 이름은 제외)
    if re.fullmatch(r"\d+\s*(스쿱|개|병|캔|잔|팩|봉|조각|인분|장|판|컵|세트)", text):
        return True

    # 한글이 하나도 없고 영문 단어도 3글자 이상이 없으면 의미 있는 이름이 아니다.
    if not re.search(r"[가-힣]", text) and not re.search(r"[A-Za-z]{3,}", text):
        return True

    # 'h2 일반'처럼 앞의 영문+숫자 찌꺼기를 떼면 일반 명칭만 남는 이름
    stripped = re.sub(r"^[A-Za-z]{1,2}\d*\s*", "", text).strip()

    return stripped in _GENERIC_NAMES


def has_suspicious_item_names(parsed: Dict) -> bool:
    return any(
        is_suspicious_item_name(item.get("name"))
        for item in (parsed.get("items") or [])
    )


_ADDRESS_LINE_RE = re.compile(
    r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
    r"|[가-힣]{2,}(시|도|구|군)\s?[가-힣]+(시|구|군|읍|면|동|로|길)"
    r"|[가-힣](로|길)\d+"
)


def drop_address_items(parsed: Dict, raw_text: Optional[str]) -> int:
    """
    흐린 영수증에서 주소 조각('영덕동' 등)이 품목으로 들어온 경우를 제거한다.

    '우동', '소보로'처럼 이름만 보면 주소와 구분되지 않으므로, 품목명이
    영수증의 주소 줄 안에 실제로 들어 있고 품목명 자체는 한글 3글자 이하의
    짧은 토큰일 때만 제거한다. 제거한 품목 수를 반환한다.
    """
    items = parsed.get("items") or []

    address_lines = [
        line
        for line in (raw_text or "").splitlines()
        if _ADDRESS_LINE_RE.search(line)
    ]
    address_text = " ".join(address_lines)

    def is_address_fragment(name: str) -> bool:
        name = name.strip().strip("()")

        return (
            2 <= len(name) <= 4
            and bool(re.fullmatch(r"[가-힣]+", name))
            and name in address_text
        )

    kept = [
        item
        for item in items
        if not is_address_fragment(item.get("name") or "")
    ]

    removed = len(items) - len(kept)

    if removed:
        parsed["items"] = kept

    return removed


def reconcile_quantities(
    parsed: Dict,
    max_qty: int = 9,
) -> bool:
    """
    수량이 2 이상인 줄은 영수증의 '단가' 열이 아니라 '금액'(단가×수량)이
    품목 가격이어야 한다. 총액과 정확히 맞아떨어지는 수량 조합이 하나뿐일 때만
    보정한다 (애매하면 건드리지 않는다).
    """
    items = parsed.get("items") or []
    total = parsed.get("total_amount")

    if not items or not total or len(items) > 8:
        return False

    prices = [int(item["price"]) for item in items]

    if sum(prices) >= total:
        return False

    suffix_min = [0] * (len(prices) + 1)

    for k in range(len(prices) - 1, -1, -1):
        suffix_min[k] = suffix_min[k + 1] + prices[k]

    solutions: List[tuple] = []

    def search(index: int, remaining: int, picked: List[int]) -> None:
        if len(solutions) > 20:
            return

        if index == len(prices):
            if remaining == 0:
                solutions.append(tuple(picked))
            return

        for qty in range(1, max_qty + 1):
            cost = prices[index] * qty

            if cost + suffix_min[index + 1] > remaining:
                break

            search(index + 1, remaining - cost, picked + [qty])

    search(0, total, [])

    if not solutions:
        return False

    def cost_key(combo: tuple) -> tuple:
        return (sum(1 for qty in combo if qty > 1), sum(combo))

    solutions.sort(key=cost_key)

    if len(solutions) > 1 and cost_key(solutions[0]) == cost_key(solutions[1]):
        return False

    for item, qty in zip(items, solutions[0]):
        if qty > 1:
            item["price"] = int(item["price"]) * qty
            item["quantity"] = qty

    return True


# ── 품목 줄 감지 ─────────────────────────────────────────────────────────────

_AMOUNT = r"\d{1,3}(?:,\d{3})+|\d+"
_ITEM_ONE_LINE_RE = re.compile(
    rf"(?<![\d,])({_AMOUNT})\s+(\d{{1,3}})\s+({_AMOUNT})(?![\d,])"
)
_ITEM_UNIT_QTY_RE = re.compile(rf"^({_AMOUNT})\s+(\d{{1,3}})$")
_ITEM_AMOUNT_RE = re.compile(rf"^({_AMOUNT})$")

# 합계·결제 정보 줄은 품목 줄이 아니다.
_NOT_ITEM_LINE_WORDS = (
    "부가세", "과세", "면세", "공급", "합계", "승인", "카드", "포인트",
    "할부", "거래", "잔액", "받을", "받은", "결제", "총액", "적립",
)


def _to_int(text: str) -> int:
    return int(text.replace(",", ""))


def has_item_line(raw_text: Optional[str]) -> bool:
    """
    영수증에 '단가 × 수량 = 금액'이 맞아떨어지는 품목 줄이 있는지 본다.

    '단가', '수량' 같은 표 머리글이 없는 영수증(예: 프랜차이즈 카페)도
    `5,400 1 5,400` 처럼 곱셈이 성립하는 줄로 품목을 알아챌 수 있다.
    곱셈이 맞아야 하므로 카드 승인번호·사업자번호 같은 숫자열은 거의 걸리지 않는다.
    """
    lines = [
        line.strip()
        for line in (raw_text or "").splitlines()
        if line.strip()
    ]

    for index, line in enumerate(lines):
        if any(word in line for word in _NOT_ITEM_LINE_WORDS):
            continue

        # 한 줄: '5,400 1 5,400'
        for match in _ITEM_ONE_LINE_RE.finditer(line):
            unit, qty, amount = (_to_int(g) for g in match.groups())

            if amount >= 100 and qty >= 1 and unit * qty == amount:
                return True

        # 두 줄로 쪼개진 경우: '12,000 1' 다음 줄 '12,000'
        unit_qty = _ITEM_UNIT_QTY_RE.match(line)

        if unit_qty and index + 1 < len(lines):
            amount_line = _ITEM_AMOUNT_RE.match(lines[index + 1])

            if amount_line:
                unit, qty = _to_int(unit_qty.group(1)), int(unit_qty.group(2))
                amount = _to_int(amount_line.group(1))

                if amount >= 100 and qty >= 1 and unit * qty == amount:
                    return True

    return False


# ── 문서 유형 ─────────────────────────────────────────────────────────────────

def classify_document(raw_text: Optional[str]) -> str:
    """
    receipt    : 결제 영수증 / 카드 매출전표
    order_form : 주문서·견적서처럼 결제 내역이 없는 서류
    """
    compact = re.sub(r"\s+", "", raw_text or "")

    if not compact:
        return "receipt"

    if any(title in compact for title in _NON_RECEIPT_TITLES):
        evidence = sum(
            1 for word in _PAYMENT_EVIDENCE if word in compact
        )

        if evidence <= 1:
            return "order_form"

    return "receipt"


_NEGATIVE_AMOUNT_RE = re.compile(r"(?<![\d)])-\s?\d[\d,]{2,}")
_REFUND_WORD_RE = re.compile(r"반품|취소")
# 정상 영수증 하단의 안내문('반품시 영수증 지참' 등)은 환불 표시가 아니다.
_REFUND_NOTICE_RE = re.compile(r"반품\s*(시|안내|불가|및)|취소\s*(불가|시|및)|교환|환불")


def is_refund(raw_text: Optional[str]) -> bool:
    """
    환불/반품/취소 영수증인지 판정한다.

    영수증 앞부분(헤더)에 '반품'·'취소'가 있고, 금액이 마이너스인 줄이 3개 이상이면
    환불이다. 할인 줄 하나만 마이너스인 정상 영수증과 구분하기 위해 둘 다 요구한다.
    """
    lines = [
        line.strip()
        for line in (raw_text or "").splitlines()
        if line.strip()
    ]

    header = lines[:15]

    has_refund_word = any(
        _REFUND_WORD_RE.search(line) and not _REFUND_NOTICE_RE.search(line)
        for line in header
    )

    if not has_refund_word:
        return False

    negatives = sum(
        len(_NEGATIVE_AMOUNT_RE.findall(line)) for line in lines
    )

    return negatives >= 3


# ── 중복 영수증 지문 ─────────────────────────────────────────────────────────

def make_receipt_fingerprint(
    parsed: Dict,
    raw_text: Optional[str],
) -> Optional[str]:
    """
    같은 영수증을 다시 찍어 올려도 같은 값이 나오도록 '결제일 + 결제시각 + 총액'
    으로 지문을 만든다. 결제시각을 못 읽었으면 가맹점명 앞 4글자로 대신한다.
    날짜나 총액이 없으면 None (판별 불가).
    """
    total = parsed.get("total_amount")
    payment_date = parsed.get("payment_date")

    if not total or not payment_date:
        return None

    text = raw_text or ""
    time_part = None

    date_match = _DATE_IN_TEXT_RE.search(text)
    search_from = date_match.end() if date_match else 0
    time_match = _TIME_RE.search(text, search_from) or _TIME_RE.search(text)

    if time_match:
        time_part = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"

    if time_part is None:
        merchant = re.sub(
            r"[^0-9A-Za-z가-힣]",
            "",
            parsed.get("merchant_name") or "",
        )
        time_part = f"m:{merchant[:4]}"

    key = f"{payment_date}|{time_part}|{int(total)}"

    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
