import re
from typing import Dict, List, Optional

from app.services.category_classifier_service import classify_category


def clean_ocr_text(raw_text: str) -> str:
    lines = raw_text.splitlines()
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        line = re.sub(r"\s+", " ", line)

        if line:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def normalize_item_name(name: str) -> str:
    name = name.replace("G)", "")
    name = name.replace("ㄴ", "")
    name = name.replace("->", "")
    name = name.replace("(", " ")
    name = name.replace(")", " ")
    name = name.strip()

    name = re.sub(r"[^가-힣a-zA-Z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name)

    return name.strip()


def extract_amounts(line: str) -> List[int]:
    numbers = re.findall(r"\d{1,3}(?:,\d{3})+|\d+", line)
    result = []

    for number in numbers:
        value = int(number.replace(",", ""))

        if value >= 100:
            result.append(value)

    return result


def is_noise_line(line: str) -> bool:
    noise_keywords = [
        "부가세", "과세", "면세", "승인", "카드", "현금",
        "사업자", "전화", "영수증", "주문번호", "매장",
        "대표", "주소", "고객", "포인트", "교환", "환불",
        "공급가", "vat", "tax", "할인", "잔액", "거스름돈",
        "일시불"
    ]

    lower_line = line.lower()

    return any(keyword.lower() in lower_line for keyword in noise_keywords)


def is_option_line(line: str) -> bool:
    option_keywords = [
        "컵", "뚜껑", "빨대", "봉투", "캐리어",
        "포장", "옵션", "추가", "무료", "증정", "쿠폰"
    ]

    normalized = normalize_item_name(line)

    if any(keyword in normalized for keyword in option_keywords):
        return True

    amounts = extract_amounts(line)

    if amounts and all(amount < 100 for amount in amounts):
        return True

    return False


def extract_total_amount(text: str) -> Optional[int]:
    """
    결제금액을 가장 우선으로 추출.
    합계 라인은 OCR 오류가 많으므로 후순위로 처리.
    """
    lines = text.splitlines()

    priority_keywords = ["결제금액", "받을금액", "총금액", "총액"]
    secondary_keywords = ["합계"]

    for keywords in [priority_keywords, secondary_keywords]:
        for i, line in enumerate(lines):
            if any(keyword in line for keyword in keywords):
                amounts = extract_amounts(line)

                if amounts:
                    return amounts[-1]

                # 다음 1~2줄에서 금액 탐색
                for j in range(i + 1, min(i + 3, len(lines))):
                    next_amounts = extract_amounts(lines[j])

                    if next_amounts:
                        return next_amounts[-1]

    return None


def is_valid_item_name(name: str) -> bool:
    if not name:
        return False

    if len(name) < 2:
        return False

    if re.fullmatch(r"\d+", name):
        return False

    if name in ["합계", "결제금액"]:
        return False

    return True


def choose_item_price(amounts: List[int], total_amount: Optional[int]) -> Optional[int]:
    """
    OCR이 6,500 16,500처럼 잘못 붙인 경우를 보정.
    상품 가격 후보 중 total_amount보다 작거나 같은 값만 사용.
    여러 개면 가장 작은 금액을 우선 사용.
    """
    if not amounts:
        return None

    candidates = amounts

    if total_amount:
        candidates = [amount for amount in amounts if amount <= total_amount]

    if not candidates:
        return None

    return min(candidates)


def extract_items(text: str, total_amount: Optional[int]) -> List[Dict]:
    lines = text.splitlines()
    items = []

    current_item_name = None

    for line in lines:
        if "합계" in line or "결제금액" in line:
            current_item_name = None
            continue

        if is_noise_line(line):
            continue

        if is_option_line(line):
            continue

        amounts = extract_amounts(line)

        if not amounts:
            item_name = normalize_item_name(line)

            if is_valid_item_name(item_name):
                current_item_name = item_name

            continue

        item_name_part = re.sub(r"\d{1,3}(?:,\d{3})+|\d+", "", line)
        item_name = normalize_item_name(item_name_part)

        if is_valid_item_name(item_name):
            final_name = item_name
        elif current_item_name:
            final_name = current_item_name
        else:
            continue

        price = choose_item_price(amounts, total_amount)

        if price is None or price <= 0:
            continue

        category = classify_category(final_name)

        items.append({
            "name": final_name,
            "price": price,
            "category": category
        })

        current_item_name = None

    return items


def parse_receipt_text(raw_text: str) -> Dict:
    cleaned_text = clean_ocr_text(raw_text)
    total_amount = extract_total_amount(cleaned_text)
    items = extract_items(cleaned_text, total_amount)

    return {
        "total_amount": total_amount,
        "items": items,
        "raw_text": raw_text,
        "cleaned_text": cleaned_text
    }