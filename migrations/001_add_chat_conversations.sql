-- 피드백 채팅 conversation 지원

CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    title VARCHAR(200),
    chat_type VARCHAR(20) NOT NULL DEFAULT 'RECEIPT',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 기존 DB에도 chat_type 컬럼 추가
ALTER TABLE chat_conversations
ADD COLUMN IF NOT EXISTS chat_type VARCHAR(20)
NOT NULL DEFAULT 'RECEIPT';

-- 기존 대화방은 영수증 상담방으로 유지
UPDATE chat_conversations
SET chat_type = 'RECEIPT'
WHERE chat_type IS NULL;

-- 지원하는 상담 유형 제한
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_chat_conversations_chat_type'
          AND conrelid = 'chat_conversations'::regclass
    ) THEN
        ALTER TABLE chat_conversations
        ADD CONSTRAINT chk_chat_conversations_chat_type
        CHECK (chat_type IN ('RECEIPT', 'HOUSEHOLD'));
    END IF;
END
$$;

ALTER TABLE chat_history
ADD COLUMN IF NOT EXISTS conversation_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_chat_history_conversation'
    ) THEN
        ALTER TABLE chat_history
        ADD CONSTRAINT fk_chat_history_conversation
        FOREIGN KEY (conversation_id)
        REFERENCES chat_conversations(conversation_id)
        ON DELETE CASCADE;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_chat_history_conversation_id
ON chat_history(conversation_id);