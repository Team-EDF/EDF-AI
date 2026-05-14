import os
from sentence_transformers import SentenceTransformer
from app.database.connection import get_db_connection
from app.services.category_maps import FAST_TRACK_MAP, MAIN_CATEGORY_FORCE_MAP, MIDDLE_CATEGORY_FORCE_MAP, MERCHANT_KEYWORD_MAP

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBERT_MODEL_NAME = os.path.join(BASE_DIR, "services", "greenstep_sbert_v2")

SIMILARITY_THRESHOLD = 0.5

class MerchantClassifier:
    _model: SentenceTransformer | None = None

    @classmethod
    def _get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            cls._model = SentenceTransformer(SBERT_MODEL_NAME)
        return cls._model

    @staticmethod
    def _to_embedding_str(vec: list) -> str:
        return "[" + ",".join(map(str, vec)) + "]"

        # ── DB에서 main_category 조회 ─────────────────────────────────────────
    def _get_main_category(self, main_name: str) -> dict | None:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT main_category_id, main_name, avg_middle_category_carbon FROM main_category WHERE main_name = %s LIMIT 1;",
            (main_name,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None
        return {
            "category_type": "main",
            "category_id": row[0],
            "category_name": row[1],
            "co2eq_KRW": row[2],
            "similarity": 1.0,
        }

        # ── DB에서 middle_category 조회 ───────────────────────────────────────
    def _get_middle_category(self, middle_name: str) -> dict | None:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT middle_category_id, middle_name, co2eq_KRW FROM middle_category WHERE middle_name = %s LIMIT 1;",
            (middle_name,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None
        return {
            "category_type": "middle",
            "category_id": row[0],
            "category_name": row[1],
            "co2eq_KRW": row[2],
            "similarity": 1.0,
        }

    # ── 1단계: 소상공인 DB 업종명 조회 ───────────────────────────────────
    def get_business_name(self, merchant_name: str, payment_location: str = None) -> str | None:
        if not merchant_name or not merchant_name.strip():
            return None

        name = merchant_name.replace(" ", "")
        conn = get_db_connection()
        cursor = conn.cursor()

        # ── 1-1. 전체 주소로 후보군 필터링 ──────────────────────────────
        if payment_location:
            # exact match
            payment_location = payment_location.replace(" ", "")
            cursor.execute(
                """
                SELECT business_name FROM MERCHANT_SME
                WHERE road_address = %s AND original_name = %s
                LIMIT 1;
                """,
                (payment_location, name),
            )
            row = cursor.fetchone()
            if row:
                cursor.close()
                conn.close()
                return row[0]

            # pg_trgm 유사도 검색
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
                cursor.close()
                conn.close()
                return row[0]

        cursor.close()
        conn.close()
        return None

    # ── 2단계: middle_category_vec SBERT 검색 (영수증용) ─────────────────
    def _search_middle(self, query_text: str) -> dict | None:
        embedding_str = self._to_embedding_str(
            self._get_model().encode(f"query: {query_text}").tolist()
        )
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT v.middle_category_id, mc.middle_name, mc.co2eq_KRW,
                   1 - (v.embedding <=> %s::vector) AS similarity
            FROM middle_category_vec v
            JOIN middle_category mc ON v.middle_category_id = mc.middle_category_id
            ORDER BY v.embedding <=> %s::vector
            LIMIT 1;
            """,
            (embedding_str, embedding_str),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        middle_category_id, middle_name, co2eq_krw, similarity = row
        if similarity < SIMILARITY_THRESHOLD:
            return None

        return {
            "category_type": "middle",
            "category_id": middle_category_id,
            "category_name": middle_name,
            "co2eq_KRW": co2eq_krw,
            "similarity": float(similarity),
        }

    # ── 2단계: main 우선 SBERT 검색, 미만 시 middle fallback (명세서용) ────
    def _search_all_categories(self, query_text: str) -> dict | None:
        embedding_str = self._to_embedding_str(
            self._get_model().encode(f"query: {query_text}").tolist()
        )
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT v.main_category_id, v.main_name, mc.avg_middle_category_carbon,
                   1 - (v.embedding <=> %s::vector) AS similarity
            FROM main_category_vec v
            JOIN main_category mc ON v.main_category_id = mc.main_category_id
            ORDER BY v.embedding <=> %s::vector
            LIMIT 1;
            """,
            (embedding_str, embedding_str),
        )
        row = cursor.fetchone()

        if row:
            main_category_id, main_name, co2eq_krw, similarity = row
            if similarity >= SIMILARITY_THRESHOLD:
                cursor.close()
                conn.close()
                return {
                    "category_type": "main",
                    "category_id": main_category_id,
                    "category_name": main_name,
                    "co2eq_KRW": co2eq_krw,
                    "similarity": float(similarity),
                }

        cursor.execute(
            """
            SELECT v.middle_category_id, mc.middle_name, mc.co2eq_KRW,
                   1 - (v.embedding <=> %s::vector) AS similarity
            FROM middle_category_vec v
            JOIN middle_category mc ON v.middle_category_id = mc.middle_category_id
            ORDER BY v.embedding <=> %s::vector
            LIMIT 1;
            """,
            (embedding_str, embedding_str),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        middle_category_id, middle_name, co2eq_krw, similarity = row
        if similarity < SIMILARITY_THRESHOLD:
            return None

        return {
            "category_type": "middle",
            "category_id": middle_category_id,
            "category_name": middle_name,
            "co2eq_KRW": co2eq_krw,
            "similarity": float(similarity),
        }

    # 영수증(세부 품목명이 존재하지 않고 가맹점명, 결제 위치, 총 합계 금액 만 존재할경우)
    def classify_from_merchant(self, merchant_name: str, payment_location: str = None) -> dict | None:
        # 1단계: 소상공인 DB 업종명 조회
        business_name = self.get_business_name(merchant_name, payment_location)
        query = business_name if business_name else merchant_name

        # 2단계: 가맹점명 기반 키워드 매칭
        for key, (category_type, category_name) in MERCHANT_KEYWORD_MAP.items():
            if key in merchant_name:
                if category_type == "main":
                    result = self._get_main_category(category_name)
                else:
                    result = self._get_middle_category(category_name)
                if result:
                    result["classify_stage"] = 2
                    return result

        # 3단계: MAIN_CATEGORY_FORCE_MAP 체크(중분류명을 받았을 경우)
        for key, main_name in MAIN_CATEGORY_FORCE_MAP.items():
            if key in query:
                result = self._get_main_category(main_name)
                if result:
                    result["classify_stage"] = 2
                    return result

        # 3단계: MIDDLE_CATEGORY_FORCE_MAP 체크 (중분류명을 받았을 경우)
        for key, middle_name in MIDDLE_CATEGORY_FORCE_MAP.items():
            if key in query:
                result = self._get_middle_category(middle_name)
                if result:
                    result["classify_stage"] = 2
                    return result

        # 4단계: SBERT 검색
        result = self._search_all_categories(query)
        if result:
            result["classify_stage"] = 2
            return result

        return None  # Groq fallback

    # ── 영수증 분류 (세부 항목 있을 때) ───────────────────────────────────
    def classify_from_item(self, item_name: str) -> dict | None:
        # 1단계: FAST_TRACK_MAP 체크 (middle 카테고리명으로 SBERT 검색)
        for key, middle_name in FAST_TRACK_MAP.items():
            if key in item_name:
                result = self._get_middle_category(middle_name)
                if result:
                    result["classify_stage"] = 2
                    return result

        # 2단계: SBERT 검색
        result = self._search_middle(item_name)
        if result:
            result["classify_stage"] = 2
            return result

        return None  # Groq fallback

# (가맹점명 + 결제위치), 세부 품목 카테고리 분류 테스트
if __name__ == "__main__":
    classifier = MerchantClassifier()

    merchant_tests = [
        ("에코마트", "경기 용인시 기흥구 덕영대로 2077번길 16"),
        ("경보", "제주 제주시 오남로 297"),
        ("GS25기흥해링턴가점", "경기 용인시 기흥구 덕영대로2077번길 33"),
        ("백화꽃화원", "경북 예천 예천 노하 67-28"),
        ("제일식당", "경기 오산시 한신대길 135"),
        ("문정헤어", "경기 용인시 기흥구 덕영대로2077번길 18"),
        ("맥도날드용인신갈DT점", "경기도 용인시 기흥구 중부대로"),
        ("메가MGC커피 용인기흥점", "경기 용인시 기흥구 덕영대로2077번길 16"),
        ("미소야 용인기흥효성점", "경기 용인시 기흥구 덕영대로2077번길 33"),
        ("용인기흥 파리바게뜨", "경기 용인시 기흥구 영덕동 14-2")
    ]

    for name, location in merchant_tests:
        result = classifier.classify_from_merchant(name, location)
        print(f"🔹 가맹점명: {name} | 결제위치: {location}")
        print(f"🔸 분류 결과: {result}")
        print("-" * 60)

    item_tests = [
        "가나초코우유",
        "블랙페퍼닭가슴",
        "종합비타민",
        "사과",
        "바나나",
        "한라봉",
        "라임",
        "배",
        "파인애플",
        "토마토",
        "매실",
        "코코넛"
    ]
    print("🧾 [영수증 세부항목(Item) 실전 테스트]")
    print("=" * 60)
    for name in item_tests:
        result = classifier.classify_from_item(name)
        print(f"🔹 입력 텍스트: {name}")
        print(f"🔸 분류 결과:   {result}")
        print("-" * 60)
