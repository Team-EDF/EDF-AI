import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    """PostgreSQL DB 연결 객체 반환 (스크립트/직접 호출 전용)."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
    )


def get_db():
    """
    FastAPI Depends 전용 DB 커넥션 제공자.
    요청 진입점에서 딱 한 번 연결을 맺고,
    요청 종료 시 자동으로 닫는다.
    """
    conn = get_db_connection()

    try:
        yield conn
    finally:
        conn.close()