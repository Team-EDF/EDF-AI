from sqlalchemy import text
from sqlalchemy.engine import Engine


def run_db_migrations(engine: Engine) -> None:
    """
    GreenStep AI 서버 시작 시 필요한 DB 스키마 변경을 자동 적용한다.

    모든 SQL은 여러 번 실행해도 안전하도록 작성한다.
    이미 적용된 DB에서는 기존 테이블/컬럼/FK/인덱스를 그대로 유지하고,
    아직 적용되지 않은 항목만 생성한다.
    """

    print("[DB Migration] Checking database schema...")

    with engine.begin() as connection:
        # ------------------------------------------------------------
        # 1. 피드백 채팅 conversation 테이블 생성
        # ------------------------------------------------------------
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_conversations (
                    conversation_id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    title VARCHAR(200),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )

        # ------------------------------------------------------------
        # 2. 기존 chat_history에 conversation_id 컬럼 추가
        # ------------------------------------------------------------
        connection.execute(
            text(
                """
                ALTER TABLE chat_history
                ADD COLUMN IF NOT EXISTS conversation_id BIGINT
                """
            )
        )

        # ------------------------------------------------------------
        # 3. chat_history -> chat_conversations FK 생성
        #
        # constraint 이름뿐 아니라 실제 대상 테이블까지 확인하여
        # 다른 테이블의 동명 constraint 때문에 누락되지 않도록 한다.
        # ------------------------------------------------------------
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint c
                        JOIN pg_class t
                            ON t.oid = c.conrelid
                        JOIN pg_namespace n
                            ON n.oid = t.relnamespace
                        WHERE c.conname = 'fk_chat_history_conversation'
                          AND t.relname = 'chat_history'
                          AND n.nspname = 'public'
                    ) THEN
                        ALTER TABLE public.chat_history
                        ADD CONSTRAINT fk_chat_history_conversation
                        FOREIGN KEY (conversation_id)
                        REFERENCES public.chat_conversations(conversation_id)
                        ON DELETE CASCADE;
                    END IF;
                END
                $$
                """
            )
        )

        # ------------------------------------------------------------
        # 4. conversation_id 조회용 인덱스 생성
        # ------------------------------------------------------------
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_history_conversation_id
                ON public.chat_history(conversation_id)
                """
            )
        )

    print("[DB Migration] Database migrations completed successfully.")
