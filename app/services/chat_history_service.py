from psycopg2.extras import Json


def create_conversation(
    conn,
    user_id: int,
    title: str | None = None,
) -> int:
    """
    새로운 피드백 채팅방을 생성한다.
    """

    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_conversations
                (
                    user_id,
                    title
                )
            VALUES
                (
                    %s,
                    %s
                )
            RETURNING conversation_id;
            """,
            (
                user_id,
                title,
            ),
        )

        row = cursor.fetchone()

        if not row:
            raise RuntimeError(
                "대화방 생성 후 conversation_id를 "
                "반환받지 못했습니다."
            )

        conversation_id = row[0]

    conn.commit()

    return conversation_id


def validate_conversation(
    conn,
    conversation_id: int,
    user_id: int,
) -> bool:
    """
    해당 대화방이 실제로 사용자의 대화방인지 확인한다.
    """

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT conversation_id
            FROM chat_conversations
            WHERE conversation_id = %s
              AND user_id = %s
            LIMIT 1;
            """,
            (
                conversation_id,
                user_id,
            ),
        )

        return cursor.fetchone() is not None


def get_recent_chat_history(
    conn,
    conversation_id: int,
    user_id: int,
    limit: int = 6,
) -> list[dict]:
    """
    해당 대화방의 최근 대화를 가져온다.

    DB에서는 최신순으로 limit개를 가져온 뒤,
    Gemini에는 과거 → 최신 순서로 전달한다.
    """

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id,
                user_message,
                ai_response,
                created_at
            FROM chat_history
            WHERE conversation_id = %s
              AND user_id = %s
            ORDER BY id DESC
            LIMIT %s;
            """,
            (
                conversation_id,
                user_id,
                limit,
            ),
        )

        rows = cursor.fetchall()

    rows.reverse()

    return [
        {
            "chat_id": row[0],
            "user_message": row[1],
            "ai_response": row[2],
            "created_at": (
                row[3].isoformat()
                if row[3] is not None
                else None
            ),
        }
        for row in rows
    ]


def save_chat_history(
    conn,
    user_message: str,
    consumption_summary: dict,
    ai_response: str,
    record_id: int | None = None,
    user_id: int | None = None,
    conversation_id: int | None = None,
) -> int:
    """
    사용자 질문과 AI 응답을 chat_history에 저장한다.
    """

    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_history
                (
                    user_id,
                    record_id,
                    conversation_id,
                    user_message,
                    consumption_summary,
                    ai_response
                )
            VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
            RETURNING id;
            """,
            (
                user_id,
                record_id,
                conversation_id,
                user_message,
                Json(consumption_summary),
                ai_response,
            ),
        )

        row = cursor.fetchone()

        if not row:
            raise RuntimeError(
                "chat_history 저장 후 chat_id를 "
                "반환받지 못했습니다."
            )

        chat_id = row[0]

        # 대화방의 마지막 활동 시간 갱신
        if conversation_id is not None:
            cursor.execute(
                """
                UPDATE chat_conversations
                SET updated_at = NOW()
                WHERE conversation_id = %s;
                """,
                (conversation_id,),
            )

    conn.commit()

    return chat_id