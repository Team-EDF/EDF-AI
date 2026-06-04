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
SBERT_MODEL_NAME = os.path.join(BASE_DIR, "services", "greenstep_sbert_v2")
SIMILARITY_THRESHOLD = 0.5


class MerchantClassifier:
    """
    3단계 분류 파이프라인:
      classify_from_merchant — 가맹점명만 있을 때 (5단계 폭포식)
      classify_from_item     — 세부 품목명이 있을 때 (3단계 폭포식)

    [DI 패턴] 두 공개 메서드 모두 conn 파라미터를 선택적으로 받는다.
    FastAPI 라우터에서 Depends(get_db)로 생성된 커넥션을 넘기면 요청 당
    단 하나의 TCP 연결만 사용한다. conn=None 시 자체 연결을 생성한다.
    """

    _model: SentenceTransformer | None = None
    _gemini: GeminiClassifier = GeminiClassifier()

    @classmethod
    def _get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            cls._model = SentenceTransformer(SBERT_MODEL_NAME)
        return cls._model

    @staticmethod
    def _to_embedding_str(vec: list) -> str:
        return "[" + ",".join(map(str, vec)) + "]"

    # ── DB 단순 조회 (conn 외부 주입, 자체 연결 생성 없음) ────────────────────

    def _get_main_category(self, main_name: str, conn) -> dict | None:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT main_category_id, main_name, avg_middle_category_carbon "
            "FROM main_category WHERE main_name = %s LIMIT 1;",
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
            "SELECT middle_category_id, middle_name, co2eq_KRW "
            "FROM middle_category WHERE middle_name = %s LIMIT 1;",
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

    # ── Safe-Default Fallback ─────────────────────────────────────────────────

    def _get_unclassified_fallback(self, conn) -> dict:
        """
        모든 분류 단계(DB/SBERT/Gemini) 실패 시 반환하는 방어 기본값.
        category_id=None + 전체 대분류 평균 탄소계수를 사용해 FK 위반 없이
        통계 누적이 가능하도록 한다. classify_stage=4로 미분류를 구별한다.
        """
        cursor = conn.cursor()
        cursor.execute(
            "SELECT AVG(avg_middle_category_carbon) FROM main_category "
            "WHERE avg_middle_category_carbon IS NOT NULL AND avg_middle_category_carbon > 0;"
        )
        row = cursor.fetchone()
        cursor.close()
        avg_carbon = (
            round(float(row[0]), CARBON_ROUND_DIGITS) if row and row[0] else None
        )
        return {
            "category_type": "main",
            "category_id": None,      # FK NULL 허용 → 스키마 Optional[int] 선언 필요
            "category_name": "미분류",
            "co2eq_KRW": avg_carbon,
            "similarity": 0.0,
            "classify_stage": 4,      # 4 = Safe-Default (1~3 외 별도 식별용)
        }

    # ── 소상공인 DB 업종명 조회 (classify_from_merchant 1단계) ────────────────

    def get_business_name(
        self,
        merchant_name: str,
        payment_location: str = None,
        conn=None,
    ) -> str | None:
        """MERCHANT_SME 테이블에서 가맹점명 → 표준산업분류명(업종명) 반환."""
        if not merchant_name or not merchant_name.strip():
            return None

        # conn=None 이면 자체 연결 (단독 호출 시 하위 호환성 유지)
        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()

        name = merchant_name.replace(" ", "")
        cursor = conn.cursor()
        try:
            if payment_location:
                payment_location = payment_location.replace(" ", "")
                # 도로명주소 + 상호명 정확 매칭
                cursor.execute(
                    "SELECT business_name FROM MERCHANT_SME "
                    "WHERE road_address = %s AND original_name = %s LIMIT 1;",
                    (payment_location, name),
                )
                row = cursor.fetchone()
                if row:
                    return row[0]

                # pg_trgm 유사도 검색 (주소 일치 + 상호명 유사)
                cursor.execute(
                    "SELECT business_name FROM MERCHANT_SME "
                    "WHERE road_address = %s AND similarity(original_name, %s) > 0.3 "
                    "ORDER BY similarity(original_name, %s) DESC LIMIT 1;",
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

    # ── SBERT 벡터 검색 ───────────────────────────────────────────────────────

    def _search_middle(self, query_text: str, conn) -> dict | None:
        """middle_category_vec에서 코사인 유사도 검색 (영수증 품목용)."""
        # greenstep_sbert_v2는 prefix 없이 학습 → 추론도 동일하게 bare text 사용
        embedding_str = self._to_embedding_str(
            self._get_model().encode(query_text).tolist()
        )
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
        """main_category_vec 우선 검색, 임계값 미달 시 middle_category_vec fallback (가맹점용)."""
        # greenstep_sbert_v2는 prefix 없이 학습 → 추론도 동일하게 bare text 사용
        embedding_str = self._to_embedding_str(
            self._get_model().encode(query_text).tolist()
        )
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
        if not row or row[3] < SIMILARITY_THRESHOLD:
            return None
        return {
            "category_type": "middle",
            "category_id": row[0],
            "category_name": row[1],
            "co2eq_KRW": float(row[2]) if row[2] is not None else None,
            "similarity": float(row[3]),
        }

    # ── 공개 분류 메서드 ──────────────────────────────────────────────────────

    def classify_from_merchant(
        self,
        merchant_name: str,
        payment_location: str = None,
        conn=None,
    ) -> dict:
        """
        가맹점명(+결제위치) → 카테고리 분류. 5단계 폭포식:
          1단계: 소상공인 DB 업종명 조회 (get_business_name)
          2단계: 가맹점명 키워드 강제 매핑 (MERCHANT_KEYWORD_MAP)
          3단계: 업종명 강제 매핑 (MAIN/MIDDLE_CATEGORY_FORCE_MAP)
          4단계: SBERT 벡터 유사도 검색
          5단계: Gemini API fallback
          최종: Safe-Default '미분류' (classify_stage=4)
        classify_stage 반환값: 2=키워드/SBERT, 3=Gemini, 4=미분류
        """
        # conn=None 이면 자체 연결 (단독 호출 시 하위 호환성 유지)
        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()
        try:
            business_name = self.get_business_name(merchant_name, payment_location, conn)
            query = business_name if business_name else merchant_name

            # 2단계: 가맹점명 키워드 강제 매핑
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

            # 3단계: 업종명 → 메인/세부 카테고리 강제 매핑
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

            # 4단계: SBERT 벡터 검색
            result = self._search_all_categories(query, conn)
            if result:
                result["classify_stage"] = 2
                return result

            # 5단계: Gemini fallback
            main_name = self._gemini.get_merchant_category_name(merchant_name)
            if main_name:
                result = self._get_main_category(main_name, conn)
                if result:
                    result["classify_stage"] = 3
                    return result

            # Safe-Default: 모든 단계 실패 시 대분류 평균 탄소계수로 '미분류' 반환
            # Gemini 장애/한도 초과 시에도 FK 위반 없이 통계 누적이 가능하다
            return self._get_unclassified_fallback(conn)
        finally:
            if own_conn:
                conn.close()

    def classify_from_item(self, item_name: str, conn=None) -> dict:
        """
        세부 품목명 → 세부 카테고리(middle_category) 분류. 3단계 폭포식:
          1단계: FAST_TRACK_MAP 직결 매핑
          2단계: SBERT 벡터 유사도 검색
          3단계: Gemini API fallback
          최종: Safe-Default '미분류' (classify_stage=4)
        classify_stage 반환값: 2=키워드/SBERT, 3=Gemini, 4=미분류
        """
        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()
        try:
            # 1단계: FAST_TRACK_MAP 직결 매핑
            for key, middle_name in FAST_TRACK_MAP.items():
                if key in item_name:
                    result = self._get_middle_category(middle_name, conn)
                    if result:
                        result["classify_stage"] = 2
                        return result

            # 2단계: SBERT 벡터 검색
            result = self._search_middle(item_name, conn)
            if result:
                result["classify_stage"] = 2
                return result

            # 3단계: Gemini fallback
            middle_name = self._gemini.get_item_category_name(item_name)
            if middle_name:
                result = self._get_middle_category(middle_name, conn)
                if result:
                    result["classify_stage"] = 3
                    return result

            # Safe-Default: 모든 단계 실패 시 대분류 평균 탄소계수로 '미분류' 반환
            return self._get_unclassified_fallback(conn)
        finally:
            if own_conn:
                conn.close()
                