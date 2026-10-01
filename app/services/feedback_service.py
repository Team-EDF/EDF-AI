import os
import re
from collections import defaultdict
from dotenv import load_dotenv
from google import genai
from google.genai import types
from app.services.chat_history_service import (
    create_conversation,
    get_recent_chat_history,
    save_chat_history,
    validate_conversation,
)
from app.services.rag_retriever_service import retrieve_eco_documents
load_dotenv()
GEMINI_MODEL = "gemini-2.5-flash"
_PROJECT_ID = os.getenv("GEMINI_PROJECT_ID")
_LOCATION = os.getenv(
    "GEMINI_LOCATION",
    "us-central1",
)
class FeedbackService:
    """
    소비기록 기반 친환경 피드백 서비스.
    지원 기능:
    1. 특정 record_id 기반 소비 피드백
    2. record_id 없이 user_id의 최근 소비기록 기반 피드백
    3. conversation_id 기반 연속 대화
    4. 이전 대화 내용을 활용한 후속 질문 처리
    5. 질문 의도에 따른 선택적 RAG 사용
    6. 현재 대화의 target item 추적
    7. target item 기반 RAG category 및 검색 query 생성
    """
    _client: genai.Client | None = None
    # ============================================================
    # Gemini Client
    # ============================================================
    @classmethod
    def _get_client(cls) -> genai.Client:
        """
        Vertex AI Gemini client를 최초 한 번 생성하고 재사용한다.
        """
        if cls._client is None:
            if not _PROJECT_ID:
                raise RuntimeError(
                    "GEMINI_PROJECT_ID 환경변수가 설정되지 않았습니다."
                )
            cls._client = genai.Client(
                vertexai=True,
                project=_PROJECT_ID,
                location=_LOCATION,
            )
        return cls._client
    # ============================================================
    # 특정 소비 기록 조회
    # ============================================================
    def get_consumption_summary(
        self,
        record_id: int,
        conn,
    ) -> dict | None:
        """
        하나의 record_id를 기준으로
        영수증 + 품목 + 카테고리 정보를 조회한다.
        """
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    record_id,
                    merchant_name,
                    payment_location,
                    record_date,
                    total_amount,
                    total_carbon_kg
                FROM consumption_records
                WHERE record_id = %s
                """,
                (record_id,),
            )
            record = cursor.fetchone()
            if not record:
                return None
            (
                db_record_id,
                merchant_name,
                payment_location,
                record_date,
                total_amount,
                total_carbon_kg,
            ) = record
            cursor.execute(
                """
                SELECT
                    i.item_id,
                    i.source_msg,
                    i.amount,
                    i.main_category_id,
                    mc.main_name,
                    i.middle_category_id,
                    mid.middle_name,
                    i.classify_stage,
                    i.carbon_kg
                FROM items i
                LEFT JOIN main_category mc
                    ON i.main_category_id = mc.main_category_id
                LEFT JOIN middle_category mid
                    ON i.middle_category_id = mid.middle_category_id
                WHERE i.record_id = %s
                ORDER BY i.item_id ASC
                """,
                (record_id,),
            )
            item_rows = cursor.fetchall()
        category_carbon = defaultdict(float)
        category_amount = defaultdict(int)
        item_list = []
        for row in item_rows:
            (
                item_id,
                item_name,
                amount,
                main_category_id,
                main_name,
                middle_category_id,
                middle_name,
                classify_stage,
                carbon_kg,
            ) = row
            amount_value = int(amount or 0)
            carbon_value = float(carbon_kg or 0)
            category_name = main_name or "미분류"
            category_carbon[category_name] += carbon_value
            category_amount[category_name] += amount_value
            item_list.append(
                {
                    "item_id": item_id,
                    "item_name": item_name,
                    "amount_krw": amount_value,
                    "main_category_id": main_category_id,
                    "main_name": main_name,
                    "middle_category_id": middle_category_id,
                    "middle_name": middle_name,
                    "classify_stage": classify_stage,
                    "carbon_kg": carbon_value,
                }
            )
        highest_carbon_category = None
        if category_carbon:
            highest_carbon_category = max(
                category_carbon,
                key=category_carbon.get,
            )
        return {
            "record_id": db_record_id,
            "merchant_name": merchant_name,
            "payment_location": payment_location,
            "record_date": (
                record_date.isoformat()
                if record_date
                else None
            ),
            "total_amount_krw": (
                int(total_amount)
                if total_amount is not None
                else None
            ),
            "total_carbon_kg": (
                float(total_carbon_kg)
                if total_carbon_kg is not None
                else None
            ),
            "category_carbon_summary": {
                category: round(value, 6)
                for category, value in category_carbon.items()
            },
            "category_amount_summary": dict(category_amount),
            "highest_carbon_category": highest_carbon_category,
            "items": item_list,
        }
    # ============================================================
    # 사용자 최근 소비 기록 조회
    # ============================================================
    def get_user_consumption_summary(
        self,
        user_id: int,
        conn,
        limit: int = 10,
    ) -> dict | None:
        """
        user_id의 최근 소비 기록을 조회하고
        여러 영수증을 하나의 소비 요약으로 생성한다.
        """
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    cr.record_id,
                    cr.merchant_name,
                    cr.record_date,
                    cr.total_amount,
                    cr.total_carbon_kg,
                    i.item_id,
                    i.source_msg,
                    i.amount,
                    i.carbon_kg,
                    i.classify_stage,
                    i.main_category_id,
                    mc.main_name,
                    i.middle_category_id,
                    mid.middle_name
                FROM consumption_records cr
                LEFT JOIN items i
                    ON cr.record_id = i.record_id
                LEFT JOIN main_category mc
                    ON i.main_category_id = mc.main_category_id
                LEFT JOIN middle_category mid
                    ON i.middle_category_id = mid.middle_category_id
                WHERE cr.user_id = %s
                  AND cr.record_id IN (
                      SELECT record_id
                      FROM consumption_records
                      WHERE user_id = %s
                      ORDER BY record_date DESC, record_id DESC
                      LIMIT %s
                  )
                ORDER BY
                    cr.record_date DESC,
                    cr.record_id DESC,
                    i.item_id ASC
                """,
                (
                    user_id,
                    user_id,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        if not rows:
            return None
        total_amount = 0
        total_carbon = 0.0
        category_carbon = defaultdict(float)
        category_amount = defaultdict(int)
        items = []
        processed_records = set()
        for row in rows:
            (
                record_id,
                merchant_name,
                record_date,
                record_total_amount,
                record_total_carbon,
                item_id,
                item_name,
                item_amount,
                item_carbon,
                classify_stage,
                main_category_id,
                main_name,
                middle_category_id,
                middle_name,
            ) = row
            # JOIN으로 같은 영수증이 품목 수만큼 반복되므로
            # 영수증 총액/총 탄소량은 한 번만 합산한다.
            if record_id not in processed_records:
                total_amount += int(record_total_amount or 0)
                total_carbon += float(record_total_carbon or 0)
                processed_records.add(record_id)
            if item_id is None:
                continue
            amount_value = int(item_amount or 0)
            carbon_value = float(item_carbon or 0)
            category_name = main_name or "미분류"
            category_carbon[category_name] += carbon_value
            category_amount[category_name] += amount_value
            items.append(
                {
                    "record_id": record_id,
                    "item_id": item_id,
                    "item_name": item_name,
                    "merchant_name": merchant_name,
                    "record_date": (
                        record_date.isoformat()
                        if record_date
                        else None
                    ),
                    "amount_krw": amount_value,
                    "main_category_id": main_category_id,
                    "main_name": main_name,
                    "middle_category_id": middle_category_id,
                    "middle_name": middle_name,
                    "classify_stage": classify_stage,
                    "carbon_kg": carbon_value,
                }
            )
        highest_carbon_category = None
        if category_carbon:
            highest_carbon_category = max(
                category_carbon,
                key=category_carbon.get,
            )
        return {
            "user_id": user_id,
            "record_count": len(processed_records),
            "total_amount_krw": total_amount,
            "total_carbon_kg": round(total_carbon, 6),
            "category_carbon_summary": {
                category: round(value, 6)
                for category, value in category_carbon.items()
            },
            "category_amount_summary": dict(category_amount),
            "highest_carbon_category": highest_carbon_category,
            "items": items,
        }
    # ============================================================
    # RAG 사용 여부 판단
    # ============================================================
    def _should_use_rag(
        self,
        user_message: str,
    ) -> bool:
        """
        외부 친환경 지식이나 행동 추천을
        필요로 하는 질문에만 RAG를 사용한다.
        """
        message = user_message.strip().lower()
        rag_keywords = [
            # 개선 / 감축
            "줄이",
            "줄여",
            "줄일",
            "감축",
            "절감",
            "개선",
            "낮추",
            "낮춰",
            # 추천 / 대안
            "추천",
            "대안",
            "대체",
            "바꾸",
            "어떻게 하면",
            "어떻게 해야",
            # 친환경 행동
            "친환경",
            "환경에 좋은",
            "환경을 위해",
            "실천",
            "텀블러",
            "재사용",
            "다회용",
            # 외부 근거 / 연구
            "연구",
            "논문",
            "근거",
            "자료",
            "효과",
            # 정량 근거 확인
            "정확히",
            "정확한",
            "수치",
            "숫자",
            "탄소배출량",
            "탄소 배출량",
            "절감량",
            "감축량",
            "절감률",
            "감축률",
            "어느 정도",
            "어느정도",
        ]
        return any(
            keyword in message
            for keyword in rag_keywords
        )
    # ============================================================
    # 정량 질문 판단 / 이전 수치 검증용 문맥
    # ============================================================
    def _is_quantitative_question(
        self,
        user_message: str,
    ) -> bool:
        """
        사용자가 수치, 단위, 비율, 정확한 탄소배출량 등
        정량적인 답을 요구하는 질문인지 판단한다.
        """
        message = (user_message or "").strip().lower()
        quantitative_keywords = [
            "정확히", "정확한", "수치", "숫자", "몇 kg", "몇kg",
            "몇 g", "몇g", "kgco2", "kg co2", "gco2", "g co2",
            "co2-eq", "co2eq", "탄소배출량", "탄소 배출량",
            "배출량은", "절감량", "감축량", "절감률", "감축률",
            "몇 %", "몇%", "퍼센트", "비율", "얼마나 배출",
            "어느 정도", "어느정도",
        ]
        return any(keyword in message for keyword in quantitative_keywords)

    def _strip_quantitative_claims(
        self,
        text: str,
    ) -> str:
        """
        이전 AI 답변을 검색/생성 문맥으로 재사용할 때 정량값을 제거한다.

        목적:
        - 품목명, 행동, 비교 대상 등 의미 문맥은 유지한다.
        - 이전 AI가 생성한 숫자가 RAG 검색어 또는 Gemini의 사실 근거로
          다시 유입되는 것을 방지한다.
        """
        if not text:
            return ""

        sanitized = text

        # 272g CO2-eq, 0.95 kgCO2e, 30%, 100g처럼
        # 숫자와 결합된 대표적인 정량 표현을 먼저 제거한다.
        quantitative_patterns = [
            r"\b\d+(?:[.,]\d+)?\s*(?:kg|g|mg|t)\s*(?:co2(?:-?eq|e)?|co₂(?:-?eq|e)?)?\b",
            r"\b\d+(?:[.,]\d+)?\s*%",
            r"\b\d+(?:[.,]\d+)?\s*(?:배|회|건|원|톤|킬로그램|그램)\b",
            r"\b\d+(?:[.,]\d+)?\b",
        ]

        for pattern in quantitative_patterns:
            sanitized = re.sub(
                pattern,
                "",
                sanitized,
                flags=re.IGNORECASE,
            )

        # 숫자 제거 뒤 남을 수 있는 단위/기호 조각과 불필요한 공백을 정리한다.
        sanitized = re.sub(
            r"\b(?:co2(?:-?eq|e)?|co₂(?:-?eq|e)?|kgco2e?|gco2e?)\b",
            "",
            sanitized,
            flags=re.IGNORECASE,
        )
        sanitized = re.sub(r"(?<![A-Za-z])-?eq\b", "", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
        sanitized = re.sub(r" *\n *", "\n", sanitized)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        sanitized = re.sub(r"\s+([,.;:])", r"\1", sanitized)
        return sanitized.strip()

    def _log_quantitative_rag_evidence(
        self,
        rag_context: str,
    ) -> None:
        """
        정량 질문 디버깅용으로 현재 RAG context 안의 숫자/단위 포함 문장만 출력한다.

        이전 대화가 아니라 이번 검색 결과에 실제 정량 근거가 들어왔는지
        터미널에서 확인하기 위한 로그이며, 사용자 응답에는 영향을 주지 않는다.
        """
        if not rag_context:
            print("[RAG QUANT EVIDENCE] none (empty context)")
            return

        candidates = re.split(r"(?<=[.!?])\s+|\n+", rag_context)
        evidence_lines = []
        quantitative_pattern = re.compile(
            r"(?:\d+(?:[.,]\d+)?\s*(?:%|kg|g|mg|t|톤|킬로그램|그램|배|회|원))"
            r"|(?:\d+(?:[.,]\d+)?\s*(?:kg|g)?\s*co2(?:-?eq|e)?)",
            flags=re.IGNORECASE,
        )

        for candidate in candidates:
            text = candidate.strip()
            if not text or not quantitative_pattern.search(text):
                continue
            evidence_lines.append(text[:500])
            if len(evidence_lines) >= 12:
                break

        print("[RAG QUANT EVIDENCE] ================================")
        if not evidence_lines:
            print("[RAG QUANT EVIDENCE] no quantitative sentence found")
        else:
            for index, evidence in enumerate(evidence_lines, start=1):
                print(f"[RAG QUANT EVIDENCE] {index}: {evidence}")
        print("[RAG QUANT EVIDENCE] ================================")

    def _build_quantitative_history_hint(
        self,
        chat_history: list[dict] | None,
    ) -> str:
        """
        정량 후속 질문에서 직전 대화의 검색 대상만 RAG에 전달한다.

        이전 AI 답변의 숫자는 검색 query에 넣지 않는다.
        품목명, 대체 식품명, 행동 등 대상 식별에 필요한 문맥만 유지한다.
        """
        if not chat_history:
            return ""

        recent_history = chat_history[-2:]
        history_lines = []

        for history in recent_history:
            user_text = (history.get("user_message") or "").strip()
            ai_text = (history.get("ai_response") or "").strip()

            if user_text:
                history_lines.append(f"이전 사용자 질문: {user_text}")

            if ai_text:
                sanitized_ai_text = self._strip_quantitative_claims(ai_text)
                history_lines.append(
                    f"이전 AI 답변(정량값 제거): {sanitized_ai_text}"
                )

        if not history_lines:
            return ""

        return (
            "아래 이전 대화는 검색 대상을 식별하기 위한 보조 문맥이다. "
            "이전 AI 답변의 정량값은 제거했으며, 현재 RAG 문서에서 "
            "수치·단위·비교 대상·조건을 새로 확인해야 한다.\n"
            + "\n".join(history_lines)
        )

    # ============================================================
    # 소비내역 문맥 사용 여부 판단
    # ============================================================
    def _should_use_consumption_context(
        self,
        user_message: str,
        summary: dict,
        chat_history: list[dict] | None = None,
    ) -> bool:
        """
        RAG 검색 query에 현재 영수증/소비 품목 정보를 포함할지 판단한다.
        일반 질문은 영수증 품목을 검색 query에서 제외하고,
        소비내역 의존형 질문과 소비 관련 후속 질문에서만 포함한다.
        """
        message = (user_message or "").strip().lower()
        if not message:
            return False
        items = summary.get("items", []) if summary else []
        # 1. 현재 질문에 실제 구매 품목명이 직접 등장
        for item in items:
            item_name = (item.get("item_name") or "").strip().lower()
            if item_name and item_name in message:
                print(
                    "[FeedbackService] "
                    "use_consumption_context=True "
                    f"reason=current_item:{item_name}"
                )
                return True
        # 2. 자신의 소비/영수증/구매 기록을 명시적으로 가리키는 표현
        consumption_reference_keywords = [
            "내 소비", "나의 소비", "내 소비내역", "내 소비 내역",
            "소비내역", "소비 내역", "소비 기록", "소비기록",
            "최근 소비", "이번 소비", "내가 산", "내가 구매한",
            "내가 결제한", "내가 먹은", "내가 마신",
            "이번 영수증", "이 영수증", "영수증에서", "영수증 기준",
            "이번 결제", "이 결제", "이번에 산", "최근에 산", "방금 산",
            "구매한 품목", "구매 품목", "산 품목",
            "이 품목", "그 품목", "해당 품목",
            "이 제품", "그 제품", "해당 제품",
            "가장 탄소", "탄소배출이 가장", "탄소 배출이 가장",
            "배출량이 가장",
        ]
        if any(keyword in message for keyword in consumption_reference_keywords):
            print(
                "[FeedbackService] "
                "use_consumption_context=True "
                "reason=explicit_consumption_reference"
            )
            return True
        # 3. 질문 자체로 완결되는 일반 환경/탄소 질문
        general_independent_keywords = [
            "일상생활", "일상 생활", "일반적으로", "보통",
            "환경에 얼마나", "환경에 도움",
            "탄소배출을 줄이", "탄소 배출을 줄이",
            "탄소발자국을 줄이", "탄소 발자국을 줄이",
            "음식물 쓰레기", "음식물쓰레기", "식품 폐기", "식품폐기",
            "텀블러", "다회용컵", "다회용 컵", "일회용컵", "일회용 컵",
            "대중교통", "에너지 절약", "재활용", "분리배출", "분리 배출",
        ]
        if any(keyword in message for keyword in general_independent_keywords):
            print(
                "[FeedbackService] "
                "use_consumption_context=False "
                "reason=independent_general_question"
            )
            return False
        # 4. 짧은 후속 질문은 최근 대화에서 소비 문맥이 이어지는지 확인
        followup_keywords = [
            "그거", "그건", "그중", "그 중", "그러면", "그럼", "왜",
            "다른 방법", "또 다른", "더 줄이", "줄이려면",
            "어떻게 하면", "어떻게 해야", "대신", "대체",
        ]
        looks_like_followup = (
            len(message) <= 40
            and any(keyword in message for keyword in followup_keywords)
        )
        if looks_like_followup and chat_history:
            for history in reversed(chat_history[-3:]):
                history_text = (
                    f"{history.get('user_message', '')} "
                    f"{history.get('ai_response', '')}"
                ).lower()
                for item in items:
                    item_name = (item.get("item_name") or "").strip().lower()
                    if item_name and item_name in history_text:
                        print(
                            "[FeedbackService] "
                            "use_consumption_context=True "
                            f"reason=followup_item:{item_name}"
                        )
                        return True
                if any(
                    keyword in history_text
                    for keyword in consumption_reference_keywords
                ):
                    print(
                        "[FeedbackService] "
                        "use_consumption_context=True "
                        "reason=followup_consumption_history"
                    )
                    return True
        # 5. 애매하면 검색 precision을 위해 소비내역을 강제로 넣지 않음
        print(
            "[FeedbackService] "
            "use_consumption_context=False "
            "reason=no_consumption_dependency"
        )
        return False
    # ============================================================
    # 현재 대화의 Target Item 추적
    # ============================================================
    def _find_target_item(
        self,
        user_message: str,
        summary: dict,
        chat_history: list[dict] | None = None,
    ) -> dict | None:
        """
        현재 질문이 어떤 소비 품목을 가리키는지 찾는다.
        우선순위:
        1. 현재 질문에 직접 언급된 품목
        2. 이전 대화에서 가장 최근에 언급된 품목
        3. 탄소배출량이 가장 높은 품목
        """
        items = summary.get(
            "items",
            [],
        )
        if not items:
            return None
        normalized_message = (
            user_message
            .strip()
            .lower()
        )
        # --------------------------------------------------------
        # 1. 현재 질문에 품목명이 직접 존재하는 경우
        # --------------------------------------------------------
        for item in items:
            item_name = (
                item.get("item_name")
                or ""
            ).strip()
            if not item_name:
                continue
            if (
                item_name.lower()
                in normalized_message
            ):
                print(
                    "[FeedbackService] "
                    f"target_item=current_message:"
                    f"{item_name}"
                )
                return item
        # --------------------------------------------------------
        # 2. 이전 대화에서 가장 최근에 언급된 품목
        # --------------------------------------------------------
        if chat_history:
            for history in reversed(
                chat_history
            ):
                history_text = (
                    f"{history.get('user_message', '')} "
                    f"{history.get('ai_response', '')}"
                ).lower()
                mentioned_items = []
                for item in items:
                    item_name = (
                        item.get("item_name")
                        or ""
                    ).strip()
                    if not item_name:
                        continue
                    if (
                        item_name.lower()
                        in history_text
                    ):
                        mentioned_items.append(
                            item
                        )
                if mentioned_items:
                    target_item = max(
                        mentioned_items,
                        key=lambda item: (
                            item.get("carbon_kg")
                            or 0
                        ),
                    )
                    print(
                        "[FeedbackService] "
                        f"target_item=history:"
                        f"{target_item.get('item_name')}"
                    )
                    return target_item
        # --------------------------------------------------------
        # 3. Fallback: 최고 탄소배출 품목
        # --------------------------------------------------------
        target_item = max(
            items,
            key=lambda item: (
                item.get("carbon_kg")
                or 0
            ),
        )
        print(
            "[FeedbackService] "
            f"target_item=fallback:"
            f"{target_item.get('item_name')}"
        )
        return target_item
    # ============================================================
    # Target Item 기반 RAG Category
    # ============================================================
    def _get_rag_category_hint(
        self,
        user_message: str,
        target_item: dict | None,
    ) -> str | None:
        """
        RAG category를 결정한다.
        우선순위:
        1. 현재 사용자 질문에 명확한 category 의도가 있으면 질문을 우선한다.
        2. 질문만으로 판단하기 어려우면 target item의 DB category/품목명을 사용한다.
        예: 현재 영수증의 fallback target item이 햄이어도 사용자가
        "카페에서 친환경적으로 소비하려면?"이라고 물으면 cafe를 선택한다.
        """
        normalized_message = (user_message or "").strip().lower()
        cafe_keywords = [
            "카페", "커피", "에스프레소", "라떼", "카푸치노",
            "콜드브루", "아메리카노", "텀블러", "개인컵", "개인 컵",
            "다회용컵", "다회용 컵", "일회용컵", "일회용 컵", "빨대",
        ]
        food_keywords = [
            "식음료", "식품", "음식", "식재료", "농산물", "축산물",
            "육류", "가공식품", "가공육", "햄", "소시지", "고기",
            "돼지고기", "소고기", "쇠고기", "닭고기", "채소", "과일",
            "두부", "콩류", "대체육", "음식물 쓰레기", "음식물쓰레기",
            "식품 폐기", "식품폐기",
        ]
        echo_guide_keywords = [
            # 일반적인 친환경 생활 / 생활 습관
            "일상생활",
            "일상 생활",
            "친환경 생활",
            "친환경 습관",
            "생활 습관",
            "생활습관",
            "탄소중립 생활",
            "탄소중립 실천",
            "탄소 줄이는 방법",
            "탄소배출 줄이는 방법",
            "탄소 배출 줄이는 방법",
            "탄소발자국 줄이는 방법",
            "탄소 발자국 줄이는 방법",
            "탄소배출을 줄이기",
            "탄소 배출을 줄이기",
            "탄소발자국을 줄이기",
            "탄소 발자국을 줄이기",
            # 에너지 / 이동 / 자원순환
            "에너지 절약",
            "전기 절약",
            "절전",
            "난방",
            "냉방",
            "대중교통",
            "교통",
            "자동차",
            "자전거",
            "도보",
            "걷기",
            "재활용",
            "분리배출",
            "분리 배출",
            "재사용",
            "중고",
            # 영어 일반 생활 행동
            "lifestyle",
            "household",
            "transport",
            "energy saving",
            "recycling",
            "carbon reduction",
            "reduce carbon emissions",
            "reduce carbon footprint",
        ]
        # 1. 현재 질문의 명시적 의도를 최우선으로 사용한다.
        if any(keyword in normalized_message for keyword in cafe_keywords):
            print(
                "[FeedbackService] "
                "rag_category_source=user_message:cafe"
            )
            return "cafe"
        if any(keyword in normalized_message for keyword in food_keywords):
            print(
                "[FeedbackService] "
                "rag_category_source=user_message:food"
            )
            return "food"
        if any(keyword in normalized_message for keyword in echo_guide_keywords):
            print(
                "[FeedbackService] "
                "rag_category_source=user_message:echo_guide"
            )
            return "echo_guide"
        # 2. 질문만으로 category를 판단하기 어려울 때만 target item을 사용한다.
        if not target_item:
            return None
        main_name = (target_item.get("main_name") or "").lower()
        middle_name = (target_item.get("middle_name") or "").lower()
        item_name = (target_item.get("item_name") or "").lower()
        category_text = f"{main_name} {middle_name} {item_name}"
        if any(keyword in category_text for keyword in cafe_keywords):
            print(
                "[FeedbackService] "
                "rag_category_source=target_item:cafe"
            )
            return "cafe"
        if any(keyword in category_text for keyword in food_keywords):
            print(
                "[FeedbackService] "
                "rag_category_source=target_item:food"
            )
            return "food"
        return None
    # ============================================================
    # RAG 검색 Query
    # ============================================================
    def _build_rag_query(
        self,
        user_message: str,
        summary: dict | None,
        target_item: dict | None = None,
        quantitative_history_hint: str = "",
    ) -> str:
        """
        현재 질문 + 소비기록 + target item으로 RAG 검색 query를 생성한다.
        핵심 원칙:
        - 사용자의 원문 질문을 검색문 맨 앞에 그대로 유지한다.
        - target item 정보는 검색 보조 문맥으로만 추가한다.
        - 사용자가 묻지 않은 의도를 과도하게 주입하지 않는다.
        - 육류 대체, 음식물 쓰레기, 과일/채소 소비처럼
          rag_service.py가 구분하는 food intent를 검색문에서도
          명확히 보존한다.
        """
        normalized_message = user_message.strip().lower()
        # --------------------------------------------------------
        # 현재 질문의 검색 의도 보조 문맥
        # --------------------------------------------------------
        intent_hints = []
        meat_keywords = [
            "소고기",
            "쇠고기",
            "돼지고기",
            "닭고기",
            "육류",
            "고기",
            "햄",
            "소시지",
            "가공육",
        ]
        substitution_keywords = [
            "대체",
            "대신",
            "바꾸",
            "추천",
        ]
        waste_keywords = [
            "음식물 쓰레기",
            "음식물쓰레기",
            "식품 폐기",
            "식품폐기",
            "남기",
            "버리",
            "폐기",
        ]
        produce_keywords = [
            "과일",
            "채소",
            "농산물",
        ]
        purchase_keywords = [
            "구매",
            "소비",
            "고르",
            "선택",
        ]
        has_meat = any(
            keyword in normalized_message
            for keyword in meat_keywords
        )
        has_substitution = any(
            keyword in normalized_message
            for keyword in substitution_keywords
        )
        has_waste = any(
            keyword in normalized_message
            for keyword in waste_keywords
        )
        has_produce = any(
            keyword in normalized_message
            for keyword in produce_keywords
        )
        has_purchase = any(
            keyword in normalized_message
            for keyword in purchase_keywords
        )
        if has_meat and has_substitution:
            intent_hints.append(
                "육류 또는 가공육을 탄소배출이 더 낮은 "
                "식품으로 대체하는 소비 행동"
            )
        if has_waste:
            intent_hints.append(
                "음식물 쓰레기 또는 식품 폐기 감축과 "
                "온실가스 감축 효과"
            )
        if has_produce and has_purchase:
            intent_hints.append(
                "과일·채소·농산물 구매 시 탄소배출을 "
                "줄일 수 있는 소비 선택"
            )
        # --------------------------------------------------------
        # Target Item이 있는 경우
        # --------------------------------------------------------
        if target_item:
            item_name = (
                target_item.get("item_name")
                or ""
            )
            main_name = (
                target_item.get("main_name")
                or ""
            )
            middle_name = (
                target_item.get("middle_name")
                or ""
            )
            target_context = f"""
현재 대화에서 사용자가 가리키는 소비 품목:
{item_name}
품목의 대분류:
{main_name}
품목의 중분류:
{middle_name}
""".strip()
        else:
            target_context = ""
        # --------------------------------------------------------
        # Target Item이 없거나 질문 자체가 전체 소비를
        # 대상으로 하는 경우 검색 보조 정보 생성
        # --------------------------------------------------------
        items = (
            summary.get("items", [])
            if summary
            else []
        )
        sorted_items = sorted(
            items,
            key=lambda item: (
                item.get("carbon_kg")
                or 0
            ),
            reverse=True,
        )
        top_items = sorted_items[:3]
        top_item_names = [
            item.get("item_name")
            for item in top_items
            if item.get("item_name")
        ]
        top_middle_categories = list(
            dict.fromkeys(
                item.get("middle_name")
                for item in top_items
                if item.get("middle_name")
            )
        )
        summary_context = ""
        if not target_item:
            summary_context = f"""
탄소배출량이 높은 주요 소비 품목:
{top_item_names}
주요 세부 카테고리:
{top_middle_categories}
""".strip()
        # --------------------------------------------------------
        # 검색 의도 문자열
        # --------------------------------------------------------
        if intent_hints:
            intent_context = "\n".join(
                f"- {hint}"
                for hint in intent_hints
            )
        else:
            intent_context = (
                "- 사용자의 질문과 직접 관련된 탄소배출 감축, "
                "친환경 소비 행동 및 연구 근거"
            )
        # --------------------------------------------------------
        # 최종 RAG 검색문
        #
        # 원문 질문을 가장 먼저 배치한다.
        # rag_service.py의 semantic search / intent detection이
        # 실제 질문의 핵심 단어를 잃지 않도록 하기 위함이다.
        # --------------------------------------------------------
        query_parts = [
            f"""사용자 원문 질문:
{user_message}"""
        ]
        if target_context:
            query_parts.append(target_context)
        if summary_context:
            query_parts.append(summary_context)
        if quantitative_history_hint:
            query_parts.append(
                "정량 주장 재검증용 이전 문맥:\n"
                f"{quantitative_history_hint}"
            )
        query_parts.append(
            f"""검색 의도:
{intent_context}
검색 시 우선할 내용:
- 사용자의 질문에 직접 답할 수 있는 자료
- 실제 소비 행동을 개선할 수 있는 자료
- 탄소배출 또는 온실가스 감축과 직접 관련된 근거
- 질문과 무관한 일반론보다 구체적인 대체·감축 행동 근거"""
        )
        return "\n\n".join(
            part.strip()
            for part in query_parts
            if part and part.strip()
        )
    # ============================================================
    # 이전 대화 문자열 생성
    # ============================================================
    def _build_history_context(
        self,
        chat_history: list[dict] | None,
    ) -> str:
        """
        DB에서 조회한 이전 대화를
        Gemini Prompt에 전달할 문자열로 변환한다.
        """
        if not chat_history:
            return "이전 대화 없음"
        history_lines = []
        for history in chat_history:
            history_lines.append(
                f"사용자: {history.get('user_message', '')}"
            )
            history_lines.append(
                f"AI: {history.get('ai_response', '')}"
            )
        return "\n".join(history_lines)
    # ============================================================
    # Gemini Prompt
    # ============================================================
    def _build_prompt(
        self,
        user_message: str,
        summary: dict,
        rag_context: str,
        chat_history: list[dict] | None = None,
        use_consumption_context: bool = True,
        is_quantitative_question: bool = False,
    ) -> str:
        """
        Gemini에게 전달할 최종 친환경 피드백 Prompt.
        """
        if summary.get("record_id") is not None:
            consumption_scope = "현재 선택한 소비 기록"
        else:
            consumption_scope = (
                f"사용자의 최근 소비 기록 "
                f"{summary.get('record_count', 0)}건"
            )
        history_context = self._build_history_context(
            chat_history=chat_history,
        )

        # 정량 질문에서는 이전 AI 답변의 숫자를 Gemini 프롬프트에서도 제거한다.
        # 이전 수치가 현재 RAG 근거처럼 재사용되는 경로를 차단한다.
        if is_quantitative_question and chat_history:
            sanitized_history = []
            for history in chat_history:
                sanitized_history.append(
                    {
                        "user_message": history.get("user_message", ""),
                        "ai_response": self._strip_quantitative_claims(
                            history.get("ai_response", "")
                        ),
                    }
                )
            history_context = self._build_history_context(
                chat_history=sanitized_history,
            )

        if is_quantitative_question:
            quantitative_grounding_rule = """
- 현재 질문은 정량적 근거를 요구하는 질문이다.
- 이전 AI 답변에 등장한 숫자, 비율, 단위, 연구 결과는 현재 답변의 근거가 아니다.
- 이전 AI 답변의 수치를 그대로 반복하지 말고, 현재 제공된 소비 데이터 또는 현재 RAG 참고자료에서 동일한 수치와 단위, 비교 대상, 적용 조건을 다시 확인한다.
- 현재 RAG 참고자료에서 해당 숫자를 직접 확인할 수 없다면 그 숫자는 답변에서 사용하지 않는다.
- 확인 가능한 정량 근거가 없다면 "현재 검색된 자료만으로는 정확한 수치를 확인하기 어렵다"고 설명하고 정성적인 방향만 제시한다.
""".strip()
        else:
            quantitative_grounding_rule = """
- 이전 AI 답변은 대화 문맥을 이해하는 데 사용할 수 있지만, 이전 답변 자체를 연구 근거나 새로운 사실의 출처로 취급하지 않는다.
""".strip()
        if use_consumption_context:
            history_consumption_rule = """
- 현재 질문은 소비내역과 관련된 질문이다.
- 이전 대화의 영수증, 구매 품목, 상점명, 결제금액,
  탄소배출량 등의 소비정보를 질문 해결에 필요한 범위에서 활용할 수 있다.
- 현재 질문과 관계없는 과거 소비정보는 억지로 끌어오지 않는다.
""".strip()
        else:
            history_consumption_rule = """
- 현재 질문은 특정 소비내역과 무관한 독립적인 질문이다.
- 이전 대화는 질문의 의미와 대화 흐름을 이해하는 용도로만 사용한다.
- 이전 대화에 등장한 영수증, 구매 품목, 상점명, 결제금액,
  탄소배출량 등의 소비정보를 현재 답변에 다시 사용하지 않는다.
- 현재 질문을 과거 소비내역과 임의로 연결하지 않는다.
""".strip()
        return f"""
너는 소비 기반 탄소 발자국 플랫폼
GreenStep의 친환경 소비 피드백 AI다.
사용자의 실제 소비 및 탄소배출량을 분석하고,
필요한 경우 제공된 RAG 참고자료를 근거로
실천 가능한 친환경 소비 피드백을 제공한다.
이 대화는 여러 턴으로 이어질 수 있다.
이전 대화가 존재하면 현재 질문을 이전 대화의
문맥과 연결하여 자연스럽게 답변해야 한다.
============================================================
[분석 대상]
============================================================
{consumption_scope}
============================================================
[사용자의 현재 소비 정보]
============================================================
가맹점:
{summary.get("merchant_name")}
결제 위치:
{summary.get("payment_location")}
결제일:
{summary.get("record_date")}
분석한 소비 기록 수:
{summary.get("record_count")}
총 소비금액:
{summary.get("total_amount_krw")}원
총 탄소배출량:
{summary.get("total_carbon_kg")} kgCO2e
카테고리별 탄소배출량:
{summary.get("category_carbon_summary")}
카테고리별 소비금액:
{summary.get("category_amount_summary")}
가장 탄소배출량이 높은 카테고리:
{summary.get("highest_carbon_category")}
품목별 정보:
{summary.get("items")}
============================================================
[이전 대화]
============================================================
{history_context}
============================================================
[RAG 참고자료]
============================================================
{rag_context}
============================================================
[현재 사용자 질문]
============================================================
{user_message}
============================================================
[대화 문맥 규칙]
============================================================
{history_consumption_rule}
{quantitative_grounding_rule}
1. 이전 대화가 존재한다면 현재 질문을
   독립적인 새로운 질문으로만 해석하지 않는다.
2. "그중에서는?", "왜?", "그러면?",
   "그건?", "그거 줄이려면?", "그 품목" 같은 표현은
   이전 대화의 내용을 참고해 의미를 해석한다.
3. 이전 답변에서 이미 설명한 내용을
   후속 질문마다 처음부터 반복하지 않는다.
4. 사용자가 짧은 후속 질문을 했다면
   해당 질문에 필요한 내용부터 직접 답한다.
5. 이전 대화는 문맥을 이해하기 위한 정보로 사용한다.
6. 탄소배출량, 소비금액, 구매 품목 등의 사실은
   현재 제공된 소비 데이터를 최우선 근거로 사용한다.
7. 이전 AI 답변과 현재 소비 데이터가 충돌하면
   현재 소비 데이터를 우선한다.
8. 이전 AI 답변에 잘못된 사실이 있더라도
   그것을 새로운 사실처럼 확정해서 사용하지 않는다.
============================================================
[기본 분석 규칙]
============================================================
9. 소비금액이 아니라 탄소배출량을 기준으로
   소비 우선순위를 분석한다.
10. 탄소배출량이 높은 품목 또는 카테고리를
    우선적으로 분석한다.
11. 사용자가 구매하지 않은 품목이나 서비스를
    구매했다고 가정하지 않는다.
12. 소비금액과 탄소배출량을 혼동하지 않는다.
13. 사용자 소비 데이터와 RAG 자료가 충돌할 경우
    사용자의 실제 소비 데이터를 우선한다.
14. RAG 참고자료가 사용자의 소비와 직접 관련되지 않는다면
    억지로 해당 자료를 연결하지 않는다.
15. RAG 근거가 충분하지 않은 경우
    일반적인 친환경 실천방안을 제안할 수 있지만,
    구체적인 정량 수치는 제시하지 않는다.
16. 특정 record_id가 없는 경우
    하나의 영수증에 대한 분석인 것처럼 표현하지 않는다.
17. 여러 소비 기록을 분석한 경우
    필요한 경우 "최근 소비 기록 기준"임을 명확하게 표현한다.
18. 친환경 행동 추천은 사용자가 추천,
    개선 방법 또는 대안을 묻거나
    답변에 실질적으로 필요한 경우 제공한다.
19. 모든 답변에 친환경 행동 2~3개를
    기계적으로 반복하지 않는다.
============================================================
[탄소 수치 사용 규칙 - 매우 중요]
============================================================
20. 근거 없는 탄소배출량,
    탄소 절감량 또는 절감률을 절대 만들어내지 않는다.
21. RAG 문서에 없는 구체적인 수치를
    생성하거나 추정하지 않는다.
22. RAG 문서에 탄소 감축 수치가 존재하더라도,
    그 수치는 해당 연구의 국가, 연구대상,
    기간, 소비조건, 생활방식 및 시나리오에서
    산출된 연구 결과로만 취급한다.
23. 논문이나 보고서에서 제시된 평균 탄소 감축량을
    현재 사용자의 개인 예상 탄소 감축량으로
    직접 적용하지 않는다.
24. 별도의 사용자별 계산 근거가 없는 경우
    개인화된 탄소 절감량 또는 절감률을 제시하지 않는다.
25. 사용자 개인의 탄소 절감량을 계산하려면
    현재 이용량, 대체 행동,
    관련 배출계수 및 계산식이 모두 필요하다.
26. 이 정보가 충분하지 않다면
    개인 절감량을 계산하지 않는다.
27. RAG 연구 결과의 숫자를 사용자의 소비금액,
    탄소배출량 또는 구매횟수에 단순 비례하여
    임의 환산하지 않는다.
28. 연구 수치를 언급할 경우
    연구 결과라는 사실과 적용 조건을 함께 표현한다.
29. 다른 국가에서 수행된 연구 결과를
    한국 사용자에게 그대로 적용하지 않는다.
30. 서로 다른 연구의 수치를 임의로 조합하여
    새로운 탄소 감축 수치를 계산하지 않는다.
31. 개인화된 피드백에서는
    정확한 감축량을 임의 제시하기보다
    - "감축 잠재력이 큰 행동"
    - "상대적으로 탄소 감축 효과가 큰 선택"
    - "탄소배출을 줄이는 데 도움이 될 수 있는 행동"
    등의 표현을 우선 사용한다.
32. 사용자 소비 데이터에 포함된 실제 수치는 신뢰 가능한 1차 근거로 취급한다.
    영수증 총액, 품목별 소비금액, 품목별 탄소배출량, 총 탄소배출량,
    카테고리별 탄소배출량 및 최근 소비 기록 집계값은 제공된 값 그대로 사용한다.
33. RAG 문서의 연구·환경 관련 정량 수치는 현재 제공된 RAG 참고자료에
    실제로 명시된 값만 사용한다.
34. 사용자 소비 데이터와 RAG 참고자료 어디에도 존재하지 않는 수치는
    임의로 계산, 추정, 보간 또는 생성하지 않는다.
35. RAG 정량 수치를 사용할 경우 원문에서 확인 가능한 단위, 비교 대상,
    산정 기준 및 적용 조건을 유지한다. 기준을 확인할 수 없으면 단정적으로 사용하지 않는다.
36. 사용자 소비 데이터의 실제 수치와 RAG의 연구 수치를 명확히 구분하며,
    연구 수치를 사용자의 실제 소비 배출량인 것처럼 표현하지 않는다.
37. 정량적 근거가 부족하면 숫자를 새로 만들지 말고
    탄소배출 감소 방향과 실천 행동을 정성적으로 설명한다.
37-1. 특히 현재 질문이 이전 답변의 수치를 재확인하는 후속 질문이라면,
      이전 AI 답변에 있던 숫자를 근거로 재사용하지 않는다.
      현재 RAG 참고자료에서 동일한 숫자와 단위·조건을 확인할 수 있을 때만 사용한다.
37-2. 현재 RAG 참고자료가 이전 답변의 숫자를 뒷받침하지 못하면
      이전 답변의 수치를 반복하지 말고, 현재 자료로는 정확한 수치를
      확인할 수 없다고 명확히 답한다.
============================================================
[RAG 근거 사용 규칙]
============================================================
38. RAG 참고자료에 명확한 근거가 있을 때만
    특정 연구 결과를 언급한다.
39. RAG 참고자료가 여러 개인 경우
    사용자의 소비와 가장 직접적으로 관련된
    자료를 우선적으로 활용한다.
40. 논문의 연구 결과와
    일반적인 친환경 지식을 구분한다.
41. RAG 문서의 특정 연구 결과를
    사용자에게 적용할 때는
    연구의 조건과 한계를 고려한다.
42. RAG 참고자료에 현재 질문에 직접 답하는 강한 정량 근거가 있다면
    일반적인 정성 설명만 반복하지 말고 해당 근거를 우선적으로 활용한다.
    강한 정량 근거란 질문의 핵심 대상과 직접 관련되고, 수치의 단위·비교 대상·
    적용 조건 또는 시나리오를 참고자료에서 확인할 수 있는 근거를 말한다.
43. 정량 근거를 사용할 때는 반드시 연구·보고서의 조건을 함께 유지한다.
    해당 수치를 사용자의 개인 감축량으로 변환하거나 그대로 적용하지 않는다.
    질문과 직접 관련된 강한 정량 근거가 없거나 적용 조건이 불명확한 경우에는
    가장 직접적인 정성 근거를 사용하고 새로운 수치를 만들지 않는다.
============================================================
[답변 작성 규칙]
============================================================
44. 친절하고 이해하기 쉬운 한국어로 답한다.
45. 지나치게 긴 답변을 작성하지 않는다.
46. 사용자의 질문에 대한 직접적인 답변을
    가장 먼저 제시한다.
47. 개선 방법을 묻는 질문이라면
    현재 소비에서 가장 먼저 개선하면 좋은 부분을
    명확하게 알려준다.
48. 추천 행동이 필요한 경우
    가능하면 우선순위가 높은 순서대로 설명한다.
49. 죄책감을 유발하거나
    사용자를 비난하는 표현을 사용하지 않는다.
50. 단순한 후속 질문에는
    전체 소비 분석을 다시 작성하지 않는다.
51. 이전 대화와 자연스럽게 연결되는
    일반적인 채팅 형태로 답한다.
52. RAG 참고자료는 답변의 정확성과 추천 근거를 강화하기 위한
    내부 참고자료로 사용한다.
53. 사용자에게 보여주는 최종 피드백에는
    "참고 근거", "상세 근거", "출처", "문서 n" 같은 별도 근거 섹션을
    작성하지 않는다. "(참고: 문서 1)", "[문서 2]", "문서 1에 따르면"처럼
    내부 문서 번호를 인용하는 표현도 절대 출력하지 않는다.
54. PDF 파일명, 문서명, 페이지 번호, RAG 검색 과정,
    검색 점수 또는 내부 문서 번호를 사용자 피드백에 노출하지 않는다.
    근거가 유용하면 출처 표기 없이 답변 문장 안에 자연스럽게 반영한다.
55. RAG에서 얻은 유용한 내용은 별도의 출처 목록으로 나열하지 말고,
    사용자의 질문에 대한 설명과 추천 행동 안에 자연스럽게 반영한다.
56. RAG의 정량 수치를 사용할 때는 앞의 탄소 수치 사용 규칙을 그대로 지키며,
    필요한 경우 "연구에서는", "해당 연구 조건에서는"처럼
    연구 결과임을 자연스럽게 밝혀 사용자 실제 수치와 혼동되지 않게 한다.
57. RAG 자료가 없거나 실제 답변에 활용하지 않았다면
    존재하지 않는 근거나 연구 결과를 만들어내지 않는다.
58. 사용자에게는 핵심 답변 → 이유 → 실천 가능한 행동의 흐름을 우선하며,
    근거 설명 때문에 답변이 보고서처럼 길어지지 않도록 한다.
59. 답변을 작성하기 전에 RAG 참고자료를 내부적으로 다음 순서로 판단한다.
    - 현재 질문에 직접 답하는 근거인지 확인한다.
    - 직접 관련된 근거 중 구체적이고 검증 가능한 정량 근거가 있으면 우선 활용한다.
    - 정량 근거가 없거나 조건이 불명확하면 가장 직접적인 정성 근거를 활용한다.
    - 질문과 관련 없는 근거는 최종 답변에 넣지 않는다.
60. 강한 정량 근거를 활용할 때는 숫자만 나열하지 말고,
    그 연구 결과가 현재 질문에 어떤 의미가 있는지 한 문장으로 설명한다.
    단, 연구 결과를 사용자의 개인 예상 감축량으로 표현하지 않는다.
61. 이전 대화의 AI 답변에 "(참고: 문서 n)" 같은 표현이 있더라도
    그 형식을 따라 하지 않는다. 현재 답변에서는 내부 출처 표기를 제거하고
    현재 RAG 참고자료의 실제 내용만 근거로 새로 답한다.
============================================================
[답변]
============================================================
""".strip()
    # ============================================================
    # 선택적 RAG + Gemini 피드백 생성
    # ============================================================
    def generate_feedback(
        self,
        user_message: str,
        consumption_summary: dict,
        chat_history: list[dict] | None = None,
    ) -> dict:
        """
        단순 소비 조회:
            소비 DB + 이전 대화 + Gemini
        친환경 추천/개선 질문:
            target item 추적
            + 소비 DB
            + 이전 대화
            + 선택적 RAG
            + Gemini
        """
        is_quantitative_question = self._is_quantitative_question(
            user_message=user_message,
        )
        use_rag = (
            self._should_use_rag(user_message=user_message)
            or is_quantitative_question
        )
        use_consumption_context = (
            self._should_use_consumption_context(
                user_message=user_message,
                summary=consumption_summary,
                chat_history=chat_history,
            )
        )
        rag_category = None
        rag_context = ""
        rag_sources = []
        # --------------------------------------------------------
        # RAG가 필요한 질문일 때만 검색
        # --------------------------------------------------------
        if use_rag:
            # 소비내역이 필요한 질문에서만 target item을 추적한다.
            # 일반 질문이 최고 탄소 품목 fallback 때문에 food/cafe 쪽으로
            # 끌려가는 것을 방지한다.
            target_item = None
            if use_consumption_context:
                target_item = self._find_target_item(
                    user_message=user_message,
                    summary=consumption_summary,
                    chat_history=chat_history,
                )
            # ----------------------------------------------------
            # RAG Category 결정
            #
            # 우선순위:
            # 현재 사용자 질문 > 필요한 경우 target item > retriever 자체 판별
            # ----------------------------------------------------
            rag_category_hint = (
                self._get_rag_category_hint(
                    user_message=user_message,
                    target_item=target_item,
                )
            )

            # 소비내역과 무관한 독립적인 일반 질문인데 질문 자체에서
            # cafe/food 등 명시적 카테고리를 찾지 못한 경우에는
            # 전체 문서군을 섞어 검색하지 않고 생활 전반 가이드로 제한한다.
            # 이렇게 해야 직전 food/cafe 대화나 정량 근거가 일반 질문의
            # 검색 결과를 과도하게 끌고 가는 것을 방지할 수 있다.
            if rag_category_hint is None and not use_consumption_context:
                rag_category_hint = "echo_guide"
                print(
                    "[FeedbackService] "
                    "rag_category_source=independent_general:echo_guide"
                )
            # 정량 후속 질문은 이전 답변의 대상/수치 표현을 검색 보조문맥으로만 사용한다.
            # 실제 숫자의 사실 여부는 반드시 이번 RAG 검색 결과에서 다시 검증한다.
            quantitative_history_hint = ""
            if is_quantitative_question:
                quantitative_history_hint = self._build_quantitative_history_hint(
                    chat_history=chat_history,
                )
            # 일반 질문은 질문 자체만 검색한다.
            # 소비내역 의존형 질문에서만 summary/target item을 보조 문맥으로 사용한다.
            rag_query = self._build_rag_query(
                user_message=user_message,
                summary=(
                    consumption_summary
                    if use_consumption_context
                    else None
                ),
                target_item=(
                    target_item
                    if use_consumption_context
                    else None
                ),
                quantitative_history_hint=quantitative_history_hint,
            )
            print(
                "[FeedbackService] "
                f"use_consumption_context={use_consumption_context}"
            )
            print(
                "[FeedbackService] "
                f"rag_category_hint="
                f"{rag_category_hint}"
            )
            try:
                rag_result = retrieve_eco_documents(
                    query=rag_query,
                    k=3,
                    preferred_category=rag_category_hint,
                )
                rag_category = (
                    rag_result.get("query_category")
                    or rag_category_hint
                )
                rag_context = rag_result.get(
                    "context",
                    "",
                )
                rag_sources = rag_result.get(
                    "sources",
                    [],
                )
                if is_quantitative_question:
                    self._log_quantitative_rag_evidence(
                        rag_context=rag_context,
                    )
            except Exception as error:
                print(
                    "[FeedbackService] "
                    f"RAG 검색 실패: "
                    f"{type(error).__name__}: {error}"
                )
        print(
            "[FeedbackService] "
            f"use_rag={use_rag}"
        )
        print(
            "[FeedbackService] "
            f"is_quantitative_question={is_quantitative_question}"
        )
        # --------------------------------------------------------
        # Gemini Prompt 생성
        # --------------------------------------------------------
        # 현재 질문이 소비내역과 관련 없는 일반 질문이면
        # Gemini 프롬프트에도 영수증/품목 정보를 전달하지 않는다.
        prompt_summary = (
            consumption_summary
            if use_consumption_context
            else {}
        )
        prompt = self._build_prompt(
            user_message=user_message,
            summary=prompt_summary,
            rag_context=rag_context,
            chat_history=chat_history,
            use_consumption_context=use_consumption_context,
            is_quantitative_question=is_quantitative_question,
        )
        response = (
            self._get_client()
            .models
            .generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=2000,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0,
                    ),
                ),
            )
        )
        if response.candidates:
            print(
                "[Gemini Debug] "
                f"finish_reason="
                f"{response.candidates[0].finish_reason}"
            )
        print(
            "[Gemini Debug] "
            f"usage={response.usage_metadata}"
        )
        print(
            "[Gemini Debug] "
            f"text_length="
            f"{len(response.text) if response.text else 0}"
        )
        if not response.text:
            raise RuntimeError(
                "Gemini가 피드백 응답을 반환하지 않았습니다."
            )
        return {
            "feedback": response.text.strip(),
            "rag_category": rag_category,
            "rag_sources": rag_sources,
        }
    # ============================================================
    # 최종 Feedback Chat
    # ============================================================
    def chat(
        self,
        user_id: int,
        user_message: str,
        conn,
        conversation_id: int | None = None,
        record_id: int | None = None,
    ) -> dict:
        """
        /feedback/chat API의 최종 진입점.
        """
        # ---------------------------------------------------------
        # 1. 대화방 생성 또는 검증
        # ---------------------------------------------------------
        if conversation_id is None:
            conversation_id = create_conversation(
                conn=conn,
                user_id=user_id,
                title=user_message[:100],
            )
        else:
            is_valid_conversation = validate_conversation(
                conn=conn,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if not is_valid_conversation:
                raise ValueError(
                    "해당 대화방을 찾을 수 없거나 "
                    "사용자의 대화방이 아닙니다."
                )
        # ---------------------------------------------------------
        # 2. 이전 대화 조회
        # ---------------------------------------------------------
        chat_history = get_recent_chat_history(
            conn=conn,
            conversation_id=conversation_id,
            user_id=user_id,
            limit=6,
        )
        # ---------------------------------------------------------
        # 3. 소비기록 조회
        # ---------------------------------------------------------
        if record_id is not None:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT record_id
                    FROM consumption_records
                    WHERE record_id = %s
                      AND user_id = %s
                    """,
                    (
                        record_id,
                        user_id,
                    ),
                )
                record_owner = cursor.fetchone()
            if not record_owner:
                raise ValueError(
                    "해당 소비 기록을 찾을 수 없거나 "
                    "사용자의 소비 기록이 아닙니다."
                )
            summary = self.get_consumption_summary(
                record_id=record_id,
                conn=conn,
            )
            if summary is None:
                raise ValueError(
                    f"record_id={record_id}인 "
                    "소비기록을 찾을 수 없습니다."
                )
        else:
            summary = self.get_user_consumption_summary(
                user_id=user_id,
                conn=conn,
            )
            if summary is None:
                raise ValueError(
                    f"user_id={user_id}의 "
                    "소비기록을 찾을 수 없습니다."
                )
        # ---------------------------------------------------------
        # 4. 선택적 RAG + Gemini 피드백
        # ---------------------------------------------------------
        generated = self.generate_feedback(
            user_message=user_message,
            consumption_summary=summary,
            chat_history=chat_history,
        )
        feedback_text = generated["feedback"]
        rag_category = generated.get(
            "rag_category"
        )
        rag_sources = generated.get(
            "rag_sources",
            [],
        )
        # ---------------------------------------------------------
        # 5. 채팅내역 저장
        # ---------------------------------------------------------
        chat_id = save_chat_history(
            conn=conn,
            user_id=user_id,
            conversation_id=conversation_id,
            record_id=record_id,
            user_message=user_message,
            consumption_summary=summary,
            ai_response=feedback_text,
        )
        # ---------------------------------------------------------
        # 6. API 응답
        # ---------------------------------------------------------
        return {
            "chat_id": chat_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "record_id": record_id,
            "message": user_message,
            "feedback": feedback_text,
            "total_carbon_kg": summary.get(
                "total_carbon_kg"
            ),
            "highest_carbon_category": summary.get(
                "highest_carbon_category"
            ),
            "rag_category": rag_category,
            "rag_sources": rag_sources,
        }
