import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    """PostgreSQL DB 연결 객체 반환 (스크립트/직접 호출 전용)."""
    # [원래 코드 복원]
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
    )

    # [변경된 코드(주석 처리됨)]
    # db_host = os.getenv("AI_DB_HOST") or os.getenv("DB_HOST") or "localhost"
    # db_port = os.getenv("AI_DB_PORT") or os.getenv("DB_PORT") or "5432"
    # db_name = os.getenv("AI_DB_NAME") or os.getenv("DB_NAME") or "edf_ai"
    # db_user = os.getenv("AI_DB_USER") or os.getenv("DB_USER") or os.getenv("DB_USERNAME") or "kyujin"
    # db_pass = os.getenv("AI_DB_PASS") or os.getenv("DB_PASS") or os.getenv("DB_PASSWORD") or "password"
    #
    # return psycopg2.connect(
    #     host=db_host,
    #     port=db_port,
    #     database=db_name,
    #     user=db_user,
    #     password=db_pass,
    # )


def get_db():
    """
    FastAPI Depends 전용 DB 커넥션 제공자.
    요청 진입점에서 딱 한 번 연결을 맺고, 요청 종료 시 자동으로 닫아
    하위 서비스 레이어들이 동일 커넥션을 공유하도록 한다.
    """
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()