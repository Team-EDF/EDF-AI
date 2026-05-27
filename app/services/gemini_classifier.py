import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

from app.database.connection import get_db_connection

load_dotenv()

GEMINI_MODEL = "gemini-2.5-flash-lite"

# 가맹점명 분류용 시스템 프롬프트 (main_category 선택)
_MERCHANT_SYSTEM = (
    "당신은 소비 카테고리 분류기입니다. "
    "주어진 카테고리 목록 중 가장 적합한 카테고리 이름 하나만 답하세요. "
    "반드시 목록에 있는 카테고리 이름만, 설명 없이 출력하세요."
)

# 세부 품목명 분류용 시스템 프롬프트 (middle_category 선택)
_ITEM_SYSTEM = (
    "당신은 소비 세부 카테고리 분류기입니다. "
    "주어진 세부 카테고리 목록 중 가장 적합한 카테고리 이름 하나만 답하세요. "
    "반드시 목록에 있는 카테고리 이름만, 설명 없이 출력하세요."
)


class GeminiClassifier:
    _client: genai.Client | None = None
    _main_categories: list[str] | None = None
    _middle_categories: list[str] | None = None

    @classmethod
    def _get_client(cls) -> genai.Client:
        if cls._client is None:
            cls._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        return cls._client

    @classmethod
    def _load_main_categories(cls) -> list[str]:
        """main_category 이름 목록을 DB에서 1회 로드 후 캐싱"""
        if cls._main_categories is None:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT main_name FROM main_category ORDER BY main_category_id;"
            )
            cls._main_categories = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
        return cls._main_categories

    @classmethod
    def _load_middle_categories(cls) -> list[str]:
        """middle_category 이름 목록을 DB에서 1회 로드 후 캐싱"""
        if cls._middle_categories is None:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT middle_name FROM middle_category ORDER BY middle_name;"
            )
            cls._middle_categories = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
        return cls._middle_categories

    def _ask(self, system_prompt: str, user_prompt: str) -> str | None:
        """Gemini API 호출 후 응답 텍스트 반환. 실패 시 None."""
        try:
            response = self._get_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0,
                    max_output_tokens=30,
                ),
            )
            return response.text.strip() if response.text else None
        except Exception as e:
            print(f"[GeminiClassifier] API 오류: {e}")
            return None

    def _match(self, raw: str | None, candidates: list[str]) -> str | None:
        """Gemini 응답을 후보 목록과 매칭. 정확 일치 우선, 부분 포함 차선."""
        if not raw:
            return None
        cleaned = raw.strip().splitlines()[0].strip()
        if cleaned in candidates:
            return cleaned
        for c in candidates:
            if c in cleaned:
                return c
        return None

    def get_merchant_category_name(self, merchant_name: str) -> str | None:
        """가맹점명 → main_category 이름 반환. 매칭 실패 시 None."""
        categories = self._load_main_categories()
        categories_str = "\n".join(f"- {c}" for c in categories)
        user_prompt = (
            f"가맹점명: {merchant_name}\n\n"
            f"카테고리 목록:\n{categories_str}\n\n"
            "위 카테고리 중 하나만 선택하세요."
        )
        raw = self._ask(_MERCHANT_SYSTEM, user_prompt)
        return self._match(raw, categories)

    def get_item_category_name(self, item_name: str) -> str | None:
        """세부 품목명 → middle_category 이름 반환. 매칭 실패 시 None."""
        categories = self._load_middle_categories()
        categories_str = "\n".join(f"- {c}" for c in categories)
        user_prompt = (
            f"품목명: {item_name}\n\n"
            f"세부 카테고리 목록:\n{categories_str}\n\n"
            "위 카테고리 중 하나만 선택하세요."
        )
        raw = self._ask(_ITEM_SYSTEM, user_prompt)
        return self._match(raw, categories)
