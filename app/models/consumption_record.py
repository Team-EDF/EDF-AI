from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime

from app.database import Base


class ConsumptionRecord(Base):
    __tablename__ = "consumption_records"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, nullable=False, index=True)

    item_name = Column(String, nullable=False)
    category = Column(String, nullable=True)

    amount = Column(Integer, nullable=False, default=0)
    carbon_emission = Column(Float, nullable=True)

    merchant = Column(String, nullable=True)
    source_ocr_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)