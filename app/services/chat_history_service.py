from psycopg2.extras import Json


def save_chat_history(
    conn,
    user_message: str,
    consumption_summary: dict,
    ai_response: str,
    record_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """
    피드백 채팅 내역을 chat_history 테이블에 저장한다.

    반환값:
        생성된 chat_history.id
    """

    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_history
                (
                    user_id,
                    record_id,
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
                    %s
                )
            RETURNING id;
            """,
            (
                user_id,
                record_id,
                user_message,
                Json(consumption_summary),
                ai_response,
            ),
        )

        row = cursor.fetchone()

        if not row:
            raise RuntimeError(
                "chat_history 저장 후 chat_id를 반환받지 못했습니다."
            )

        chat_id = row[0]

    conn.commit()

    return chat_id