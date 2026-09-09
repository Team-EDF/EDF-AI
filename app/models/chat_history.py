from sqlalchemy import Column, Integer, BigInteger, Text, DateTime, JSON
from sqlalchemy.sql import func

from app.database import Base


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)

    # [수정] 원래 ForeignKey("consumption_records.record_id")였으나,
    # consumption_records는 백엔드(Hibernate)가 소유/생성하는 테이블이라
    # 이 서비스의 SQLAlchemy Base.metadata에는 그 테이블 정의가 없다.
    # create_all()이 FK 대상 테이블/컬럼을 자기 메타데이터 안에서
    # 찾지 못해 앱이 시작조차 못 하는 문제(NoReferencedTableError)가
    # 있어서, 서비스 경계를 넘는 FK 제약 없이 일반 컬럼으로 둔다.
    record_id = Column(
        BigInteger,
        nullable=True,
        index=True,
    )

    user_message = Column(Text, nullable=False)
    consumption_summary = Column(JSON, nullable=False)
    ai_response = Column(Text, nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )