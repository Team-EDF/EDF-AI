import os

from sentence_transformers import SentenceTransformer

from app.database.connection import get_db_connection
from app.services.carbon import CARBON_ROUND_DIGITS
from app.services.category_maps import (
    FAST_TRACK_MAP,
    MAIN_CATEGORY_FORCE_MAP,
    MIDDLE_CATEGORY_FORCE_MAP,
    MERCHANT_KEYWORD_MAP,
)
from app.services.gemini_classifier import GeminiClassifier


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBERT_MODEL_NAME = os.getenv(
    "SBERT_MODEL_PATH",
    os.path.join(BASE_DIR, "services", "greenstep_sbert_v2"),
)
SIMILARITY_THRESHOLD = 0.5


class MerchantClassifier:
    """
    3단계 분류 파이프라인:
    - classify_from_merchant: 가맹점명 기반 분류
    - classify_from_item: 세부 품목명 기반 분류
    """

    _model: SentenceTransformer | None = None
    _gemini: GeminiClassifier = GeminiClassifier()

    @classmethod
    def _get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            modules_file = os.path.join(SBERT_MODEL_NAME, "modules.json")
            if not os.path.isfile(modules_file):
                raise RuntimeError(
                    "SBERT model is not mounted correctly. "
                    f"Expected file: {modules_file}"
                )

            cls._model = SentenceTransformer(
                SBERT_MODEL_NAME,
                local_files_only=True,
                device="cpu",
            )
        return cls._model

    @staticmethod
    def _to_embedding_str(vec: list) -> str:
        return "[" + ",".join(map(str, vec)) + "]"

    def _get_main_category(self, main_name: str, conn) -> dict | None:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT main_category_id, main_name, avg_middle_category_carbon
            FROM main_category
            WHERE main_name = %s
            LIMIT 1;
            """,
            (main_name,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return None

        return {
            "category_type": "main",
            "category_id": row[0],
            "category_name": row[1],
            "co2eq_KRW": float(row[2]) if row[2] is not None else None,
            "similarity": 1.0,
        }

    def _get_middle_category(self, middle_name: str, conn) -> dict | None:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT middle_category_id, middle_name, co2eq_KRW
            FROM middle_category
            WHERE middle_name = %s
            LIMIT 1;
            """,
            (middle_name,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return None

        return {
            "category_type": "middle",
            "category_id": row[0],
            "category_name": row[1],
            "co2eq_KRW": float(row[2]) if row[2] is not None else None,
            "similarity": 1.0,
        }

    def _get_unclassified_fallback(self, conn) -> dict:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT AVG(avg_middle_category_carbon)
            FROM main_category
            WHERE avg_middle_category_carbon IS NOT NULL
              AND avg_middle_category_carbon > 0;
            """
        )
        row = cursor.fetchone()
        cursor.close()

        avg_carbon = (
            round(float(row[0]), CARBON_ROUND_DIGITS)
            if row and row[0] is not None
            else None
        )

        return {
            "category_type": "main",
            "category_id": None,
            "category_name": "미분류",
            "co2eq_KRW": avg_carbon,
            "similarity": 0.0,
            "classify_stage": 4,
        }

    def get_business_name(
        self,
        merchant_name: str,
        payment_location: str | None = None,
        conn=None,
    ) -> str | None:
        if not merchant_name or not merchant_name.strip():
            return None

        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()

        name = merchant_name.replace(" ", "")
        cursor = conn.cursor()

        try:
            if payment_location:
                payment_location = payment_location.replace(" ", "")

                cursor.execute(
                    """
                    SELECT business_name
                    FROM MERCHANT_SME
                    WHERE road_address = %s
                      AND original_name = %s
                    LIMIT 1;
                    """,
                    (payment_location, name),
                )
                row = cursor.fetchone()
                if row:
                    return row[0]

                cursor.execute(
                    """
                    SELECT business_name
                    FROM MERCHANT_SME
                    WHERE road_address = %s
                      AND similarity(original_name, %s) > 0.3
                    ORDER BY similarity(original_name, %s) DESC
                    LIMIT 1;
                    """,
                    (payment_location, name, name),
                )
                row = cursor.fetchone()
                if row:
                    return row[0]

            return None

        finally:
            cursor.close()
            if own_conn:
                conn.close()

    def _search_middle(self, query_text: str, conn) -> dict | None:
        embedding_str = self._to_embedding_str(
            self._get_model().encode(query_text).tolist()
        )

        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT v.middle_category_id,
                   mc.middle_name,
                   mc.co2eq_KRW,
                   1 - (v.embedding <=> %s::vector) AS similarity
            FROM middle_category_vec v
            JOIN middle_category mc
              ON v.middle_category_id = mc.middle_category_id
            ORDER BY v.embedding <=> %s::vector
            LIMIT 1;
            """,
            (embedding_str, embedding_str),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row or row[3] < SIMILARITY_THRESHOLD:
            return None

        return {
            "category_type": "middle",
            "category_id": row[0],
            "category_name": row[1],
            "co2eq_KRW": float(row[2]) if row[2] is not None else None,
            "similarity": float(row[3]),
        }

    def _search_all_categories(self, query_text: str, conn) -> dict | None:
        embedding_str = self._to_embedding_str(
            self._get_model().encode(query_text).tolist()
        )

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT v.main_category_id,
                   v.main_name,
                   mc.avg_middle_category_carbon,
                   1 - (v.embedding <=> %s::vector) AS similarity
            FROM main_category_vec v
            JOIN main_category mc
              ON v.main_category_id = mc.main_category_id
            ORDER BY v.embedding <=> %s::vector
            LIMIT 1;
            """,
            (embedding_str, embedding_str),
        )
        row = cursor.fetchone()

        if row and row[3] >= SIMILARITY_THRESHOLD:
            cursor.close()
            return {
                "category_type": "main",
                "category_id": row[0],
                "category_name": row[1],
                "co2eq_KRW": float(row[2]) if row[2] is not None else None,
                "similarity": float(row[3]),
            }

        cursor.execute(
            """
            SELECT v.middle_category_id,
                   mc.middle_name,
                   mc.co2eq_KRW,
                   1 - (v.embedding <=> %s::vector) AS similarity
            FROM middle_category_vec v
            JOIN middle_category mc
              ON v.middle_category_id = mc.middle_category_id
            ORDER BY v.embedding <=> %s::vector
            LIMIT 1;
            """,
            (embedding_str, embedding_str),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row or row[3] < SIMILARITY_THRESHOLD:
            return None

        return {
            "category_type": "middle",
            "category_id": row[0],
            "category_name": row[1],
            "co2eq_KRW": float(row[2]) if row[2] is not None else None,
            "similarity": float(row[3]),
        }

    def classify_from_merchant(
        self,
        merchant_name: str,
        payment_location: str | None = None,
        conn=None,
    ) -> dict:
        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()

        try:
            business_name = self.get_business_name(
                merchant_name,
                payment_location,
                conn=conn,
            )
            query = business_name if business_name else merchant_name

            for key, (category_type, category_name) in MERCHANT_KEYWORD_MAP.items():
                if key in merchant_name:
                    result = (
                        self._get_main_category(category_name, conn)
                        if category_type == "main"
                        else self._get_middle_category(category_name, conn)
                    )
                    if result:
                        result["classify_stage"] = 2
                        return result

            for key, main_name in MAIN_CATEGORY_FORCE_MAP.items():
                if key in query:
                    result = self._get_main_category(main_name, conn)
                    if result:
                        result["classify_stage"] = 2
                        return result

            for key, middle_name in MIDDLE_CATEGORY_FORCE_MAP.items():
                if key in query:
                    result = self._get_middle_category(middle_name, conn)
                    if result:
                        result["classify_stage"] = 2
                        return result

            result = self._search_all_categories(query, conn)
            if result:
                result["classify_stage"] = 2
                return result

            main_name = self._gemini.get_merchant_category_name(merchant_name)
            if main_name:
                result = self._get_main_category(main_name, conn)
                if result:
                    result["classify_stage"] = 3
                    return result

            return self._get_unclassified_fallback(conn)

        finally:
            if own_conn:
                conn.close()

    def classify_from_item(self, item_name: str, conn=None) -> dict:
        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()

        try:
            for key, middle_name in FAST_TRACK_MAP.items():
                if key in item_name:
                    result = self._get_middle_category(middle_name, conn)
                    if result:
                        result["classify_stage"] = 2
                        return result

            result = self._search_middle(item_name, conn)
            if result:
                result["classify_stage"] = 2
                return result

            middle_name = self._gemini.get_item_category_name(item_name)
            if middle_name:
                result = self._get_middle_category(middle_name, conn)
                if result:
                    result["classify_stage"] = 3
                    return result

            return self._get_unclassified_fallback(conn)

        finally:
            if own_conn:
                conn.close()