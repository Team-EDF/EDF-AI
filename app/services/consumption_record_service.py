from sqlalchemy.orm import Session

from app.models.consumption_record import ConsumptionRecord
from app.services.classifier import MerchantClassifier
from app.services.carbon import calculate_carbon


classifier = MerchantClassifier()


FALLBACK_CATEGORY_MAP = {
    "콜드브루": "무알콜음료",
    "아메리카노": "무알콜음료",
    "라떼": "무알콜음료",
    "커피": "무알콜음료",
    "우유": "우유",
    "양파": "채소",
    "무": "채소",
    "깻잎": "채소",
    "브로커리": "채소",
    "브로콜리": "채소",
    "장아찌": "혼합요리",
}


SKIP_KEYWORDS = [
    "판매총액",
    "받을 금액",
    "신용",
    "부 가 세",
    "부가세",
    "합계",
    "결제금액",
]


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return default


def _should_skip_item(name: str) -> bool:
    return any(keyword in name for keyword in SKIP_KEYWORDS)


def _fallback_category(name: str) -> str | None:
    for keyword, category in FALLBACK_CATEGORY_MAP.items():
        if keyword in name:
            return category
    return None


def save_consumption_records(
    db: Session,
    user_id: int,
    parsed_result: dict,
    ocr_id: int | None = None
):
    print("SAVE_CONSUMPTION_RECORDS 실행")

    saved_records = []

    items = parsed_result.get("items", [])
    merchant = parsed_result.get("merchant") or parsed_result.get("merchant_name")
    payment_location = parsed_result.get("payment_location")

    for item in items:
        name = item.get("name") or item.get("item_name")
        amount = _safe_int(item.get("price") or item.get("amount") or 0)

        if not name or amount <= 0:
            continue

        name = str(name).strip()

        if _should_skip_item(name):
            print("SKIP ITEM:", name)
            continue

        category_name = item.get("category")
        carbon_emission = None

        try:
            classify_result = classifier.classify_from_item(name)
            print("ITEM:", name, amount)
            print("CLASSIFY RESULT:", classify_result)

            if classify_result:
                category_name = classify_result.get("category_name") or category_name
                co2eq_krw = classify_result.get("co2eq_KRW")
                carbon_emission = calculate_carbon(co2eq_krw, amount)

        except Exception as e:
            print(f"Category classify error: {e}")

        if not category_name:
            category_name = _fallback_category(name)

        if category_name and carbon_emission is None:
            try:
                classify_result = classifier.classify_from_item(category_name)
                print("FALLBACK CLASSIFY RESULT:", classify_result)

                if classify_result:
                    co2eq_krw = classify_result.get("co2eq_KRW")
                    carbon_emission = calculate_carbon(co2eq_krw, amount)

            except Exception as e:
                print(f"Fallback classify error: {e}")

        if not category_name and merchant:
            try:
                classify_result = classifier.classify_from_merchant(
                    merchant_name=merchant,
                    payment_location=payment_location
                )
                print("MERCHANT CLASSIFY RESULT:", classify_result)

                if classify_result:
                    category_name = classify_result.get("category_name")
                    co2eq_krw = classify_result.get("co2eq_KRW")
                    carbon_emission = calculate_carbon(co2eq_krw, amount)

            except Exception as merchant_error:
                print(f"Merchant classify error: {merchant_error}")

        print("FINAL CATEGORY:", category_name)
        print("FINAL CARBON:", carbon_emission)

        record = ConsumptionRecord(
            user_id=user_id,
            item_name=name,
            category=category_name or "미분류",
            amount=amount,
            carbon_emission=carbon_emission or 0,
            merchant=merchant,
            source_ocr_id=ocr_id
        )

        db.add(record)
        saved_records.append(record)

    db.commit()

    for record in saved_records:
        db.refresh(record)

    return saved_records