import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

# [원래 코드 복원]
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASS = os.getenv("DB_PASS")

# [변경된 코드(주석 처리됨)]
# DATABASE_URL = os.getenv("AI_DATABASE_URL") or os.getenv("DATABASE_URL")
#
# if not DATABASE_URL:
#     DB_HOST = os.getenv("AI_DB_HOST") or os.getenv("DB_HOST") or "localhost"
#     DB_PORT = os.getenv("AI_DB_PORT") or os.getenv("DB_PORT") or "5432"
#     DB_NAME = os.getenv("AI_DB_NAME") or os.getenv("DB_NAME") or "edf_ai"
#     DB_USER = os.getenv("AI_DB_USER") or os.getenv("DB_USER") or os.getenv("DB_USERNAME") or "kyujin"
#     DB_PASS = os.getenv("AI_DB_PASS") or os.getenv("DB_PASS") or os.getenv("DB_PASSWORD") or "password"

    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()