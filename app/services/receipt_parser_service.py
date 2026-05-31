import re
from typing import Dict, List, Optional


def clean_ocr_text(raw_text: str) -> str:
    lines = raw_text.splitlines()
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        line = re.sub(r"\s+", " ", line)

        if line:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def normalize_text(value: str) -> str:
    value = value.replace("G)", "")
    value = value.replace("ㄴ", "")
    value = value.replace("->", "")
    value = value.replace("(", " ")
    value = value.replace(")", " ")
    value = re.sub(r"[^가-힣a-zA-Z0-9\s.,/-]", "", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_item_name(name: str) -> str:
    name = normalize_text(name)
    name = re.sub(r"[^가-힣a-zA-Z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name)

    return name.strip()


def extract_amounts(line: str) -> List[int]:
    numbers = re.findall(r"\d{1,3}(?:,\d{3})+|\d+", line)
    amounts = []

    for number in numbers:
        value = int(number.replace(",", ""))

        if value >= 100:
            amounts.append(value)

    return amounts


def extract_store_name(text: str) -> Optional[str]:
    lines = text.splitlines()

    ignore_keywords = [
        "사업자", "대표", "주소", "전화", "tel",
        "결제", "승인", "카드", "합계", "부가세",
        "영수증", "주문번호", "매장번호"
    ]

    for line in lines[:8]:
        cleaned = normalize_item_name(line)

        if not cleaned:
            continue

        if any(keyword.lower() in cleaned.lower() for keyword in ignore_keywords):
            continue

        if re.search(r"\d{3,}", cleaned):
            continue

        if len(cleaned) >= 2:
            return cleaned

    return None


def extract_payment_date(text: str) -> Optional[str]:
    patterns = [
        r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})",
        r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

    return None


def extract_payment_location(text: str) -> Optional[str]:
    lines = text.splitlines()

    location_keywords = ["주소", "addr", "address", "매장", "지점"]

    for line in lines:
        cleaned = normalize_text(line)

        if not cleaned:
            continue

        if any(keyword.lower() in cleaned.lower() for keyword in location_keywords):
            cleaned = re.sub(r"주소[:：]?", "", cleaned)
            cleaned = re.sub(r"매장[:：]?", "", cleaned)
            cleaned = re.sub(r"지점[:：]?", "", cleaned)
            cleaned = cleaned.strip()

            if len(cleaned) >= 3:
                return cleaned

    address_patterns = [
        r"[가-힣]+시\s*[가-힣]+구\s*[가-힣0-9\s.-]+",
        r"[가-힣]+도\s*[가-힣]+시\s*[가-힣0-9\s.-]+",
        r"[가-힣]+구\s*[가-힣0-9\s.-]+로\s*\d+",
        r"[가-힣]+로\s*\d+",
        r"[가-힣]+길\s*\d+",
    ]

    for line in lines:
        cleaned = normalize_text(line)

        for pattern in address_patterns:
            match = re.search(pattern, cleaned)

            if match:
                return match.group().strip()

    return None


def is_noise_line(line: str) -> bool:
    noise_keywords = [
        "부가세", "과세", "면세", "승인", "카드", "현금",
        "사업자", "전화", "tel", "영수증", "주문번호", "매장",
        "대표", "주소", "고객", "포인트", "교환", "환불",
        "공급가", "vat", "tax", "할인", "잔액", "거스름돈",
        "일시불", "결제일", "거래일", "판매일"
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
    lines = text.splitlines()

    priority_keywords = ["결제금액", "받을금액", "총금액", "총액"]
    secondary_keywords = ["합계"]

    for keywords in [priority_keywords, secondary_keywords]:
        for i, line in enumerate(lines):
            if any(keyword in line for keyword in keywords):
                amounts = extract_amounts(line)

                if amounts:
                    return amounts[-1]

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

    invalid_keywords = [
        "합계", "결제금액", "부가세", "승인", "카드",
        "주소", "대표", "사업자", "전화", "영수증"
    ]

    if any(keyword in name for keyword in invalid_keywords):
        return False

    return True


def choose_item_price(amounts: List[int], total_amount: Optional[int]) -> Optional[int]:
    if not amounts:
        return None

    candidates = amounts

    if total_amount:
        candidates = [
            amount
            for amount in amounts
            if amount <= total_amount
        ]

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

        items.append({
            "name": final_name,
            "price": price
        })

        current_item_name = None

    return items


def parse_receipt_text(raw_text: str) -> Dict:
    cleaned_text = clean_ocr_text(raw_text)
    total_amount = extract_total_amount(cleaned_text)

    return {
        "store_name": extract_store_name(cleaned_text),
        "payment_date": extract_payment_date(cleaned_text),
        "payment_location": extract_payment_location(cleaned_text),
        "total_amount": total_amount,
        "items": extract_items(cleaned_text, total_amount),
        "raw_text": raw_text,
        "cleaned_text": cleaned_text
    }