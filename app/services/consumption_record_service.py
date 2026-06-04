from sqlalchemy.orm import Session

from app.models.consumption_record import ConsumptionRecord


def save_consumption_records(
    db: Session,
    user_id: int,
    parsed_result: dict,
    ocr_id: int | None = None
):
    saved_records = []

    items = parsed_result.get("items", [])
    merchant = parsed_result.get("merchant")

    for item in items:
        name = item.get("name") or item.get("item_name")
        amount = item.get("price") or item.get("amount") or 0
        category = item.get("category")

        if not name:
            continue

        record = ConsumptionRecord(
            user_id=user_id,
            item_name=name,
            category=category,
            amount=int(amount),
            merchant=merchant,
            source_ocr_id=ocr_id
        )

        db.add(record)
        saved_records.append(record)

    db.commit()

    for record in saved_records:
        db.refresh(record)

    return saved_records