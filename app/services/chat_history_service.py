from sqlalchemy.orm import Session

from app.models.chat_history import ChatHistory


def save_chat_history(
    db: Session,
    user_message: str,
    consumption_summary: dict,
    ai_response: str,
    user_id: int | None = None
):
    chat = ChatHistory(
        user_id=user_id,
        user_message=user_message,
        consumption_summary=consumption_summary,
        ai_response=ai_response
    )

    db.add(chat)
    db.commit()
    db.refresh(chat)

    return chat