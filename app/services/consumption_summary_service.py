from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.consumption_record import ConsumptionRecord


def get_user_consumption_summary(db: Session, user_id: int):
    records = (
        db.query(ConsumptionRecord)
        .filter(ConsumptionRecord.user_id == user_id)
        .order_by(ConsumptionRecord.created_at.asc())
        .all()
    )

    if not records:
        return {
            "user_id": user_id,
            "summary_type": "initial",
            "message": "아직 집계 가능한 탄소배출량 데이터가 부족합니다.",
            "total_amount": 0,
            "total_carbon_emission": 0,
            "category_summary": {},
            "items": [],
        }

    total_amount = (
        db.query(func.sum(ConsumptionRecord.amount))
        .filter(ConsumptionRecord.user_id == user_id)
        .scalar()
    ) or 0

    total_carbon_emission = (
        db.query(func.sum(ConsumptionRecord.carbon_emission))
        .filter(ConsumptionRecord.user_id == user_id)
        .scalar()
    ) or 0

    category_rows = (
        db.query(
            ConsumptionRecord.category,
            func.sum(ConsumptionRecord.amount),
            func.sum(ConsumptionRecord.carbon_emission),
        )
        .filter(ConsumptionRecord.user_id == user_id)
        .group_by(ConsumptionRecord.category)
        .all()
    )

    category_summary = {
        category or "미분류": {
            "amount": int(amount or 0),
            "carbon_emission": round(float(carbon_emission or 0), 6),
        }
        for category, amount, carbon_emission in category_rows
    }

    items = [
        {
            "item_name": record.item_name,
            "category": record.category or "미분류",
            "amount": int(record.amount or 0),
            "carbon_emission": round(float(record.carbon_emission or 0), 6),
            "merchant": record.merchant,
        }
        for record in records[-20:]
    ]

    has_carbon_data = any(
        item["carbon_emission"] > 0
        for item in items
    )

    return {
        "user_id": user_id,
        "summary_type": "carbon_based" if has_carbon_data else "initial_carbon",
        "message": (
            "사용자 소비 데이터를 탄소배출량 기준으로 집계했습니다."
            if has_carbon_data
            else "소비 기록은 있으나 아직 탄소배출량이 계산된 데이터가 부족합니다."
        ),
        "total_amount": int(total_amount or 0),
        "total_carbon_emission": round(float(total_carbon_emission or 0), 6),
        "category_summary": category_summary,
        "items": items,
    }