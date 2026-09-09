-- 피드백 채팅 conversation 지원

CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    title VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

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