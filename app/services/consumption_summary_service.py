from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.consumption_record import ConsumptionRecord


def get_user_consumption_summary(db: Session, user_id: int):
    records = (
        db.query(ConsumptionRecord)
        .filter(ConsumptionRecord.user_id == user_id)
        .all()
    )

    if not records:
        return {
            "user_id": user_id,
            "summary_type": "initial",
            "message": "아직 집계 가능한 소비 데이터가 부족합니다.",
            "total_amount": 0,
            "category_summary": {},
            "items": []
        }

    total_amount = (
        db.query(func.sum(ConsumptionRecord.amount))
        .filter(ConsumptionRecord.user_id == user_id)
        .scalar()
    ) or 0

    category_rows = (
        db.query(
            ConsumptionRecord.category,
            func.sum(ConsumptionRecord.amount)
        )
        .filter(ConsumptionRecord.user_id == user_id)
        .group_by(ConsumptionRecord.category)
        .all()
    )

    category_summary = {
        category or "미분류": int(amount)
        for category, amount in category_rows
    }

    items = [
        {
            "item_name": record.item_name,
            "category": record.category,
            "amount": record.amount,
            "merchant": record.merchant
        }
        for record in records[-20:]
    ]

    return {
        "user_id": user_id,
        "summary_type": "consumption_based",
        "message": "사용자 소비 데이터를 기반으로 집계했습니다.",
        "total_amount": int(total_amount),
        "category_summary": category_summary,
        "items": items
    }