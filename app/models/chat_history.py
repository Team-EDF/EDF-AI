from sqlalchemy import Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.sql import func

from app.database import Base


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)

    user_message = Column(Text, nullable=False)
    consumption_summary = Column(JSON, nullable=False)
    ai_response = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())