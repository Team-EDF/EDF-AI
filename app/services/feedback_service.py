import os
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
        ]

        return any(
            keyword in message
            for keyword in rag_keywords
        )

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
        target_item: dict | None,
    ) -> str | None:
        """
        target item의 DB 카테고리와 품목명을 이용해
        RAG category를 결정한다.
        """

        if not target_item:
            return None

        main_name = (
            target_item.get("main_name")
            or ""
        ).lower()

        middle_name = (
            target_item.get("middle_name")
            or ""
        ).lower()

        item_name = (
            target_item.get("item_name")
            or ""
        ).lower()

        category_text = (
            f"{main_name} "
            f"{middle_name} "
            f"{item_name}"
        )

        # --------------------------------------------------------
        # Cafe
        # --------------------------------------------------------

        cafe_keywords = [
            "카페",
            "커피",
            "에스프레소",
            "라떼",
            "카푸치노",
            "콜드브루",
            "아메리카노",
            "텀블러",
        ]

        if any(
            keyword in category_text
            for keyword in cafe_keywords
        ):
            return "cafe"

        # --------------------------------------------------------
        # Food
        # --------------------------------------------------------

        food_keywords = [
            "식음료",
            "식품",
            "음식",
            "식재료",
            "농산물",
            "축산물",
            "육류",
            "가공식품",
            "가공육",
            "햄",
            "소시지",
            "고기",
            "돼지고기",
            "소고기",
            "쇠고기",
            "닭고기",
            "채소",
            "과일",
        ]

        if any(
            keyword in category_text
            for keyword in food_keywords
        ):
            return "food"

        return None

    # ============================================================
    # RAG 검색 Query
    # ============================================================

    def _build_rag_query(
        self,
        user_message: str,
        summary: dict,
        target_item: dict | None = None,
    ) -> str:
        """
        현재 질문 + 소비기록 + target item으로
        RAG 검색 query를 생성한다.
        """

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

            return f"""
사용자 질문:
{user_message}

현재 대화에서 사용자가 가리키는 소비 품목:
{item_name}

품목의 대분류:
{main_name}

품목의 중분류:
{middle_name}

검색 목적:
해당 품목과 관련된 탄소배출 감축,
친환경 소비, 대체 소비,
소비 습관 개선 방법 및 연구 근거
""".strip()

        # --------------------------------------------------------
        # Target Item이 없는 경우
        # --------------------------------------------------------

        items = summary.get(
            "items",
            [],
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

        return f"""
사용자 질문:
{user_message}

탄소배출량이 높은 주요 소비 품목:
{top_item_names}

주요 세부 카테고리:
{top_middle_categories}
""".strip()

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


============================================================
[RAG 근거 사용 규칙]
============================================================

32. RAG 참고자료에 명확한 근거가 있을 때만
    특정 연구 결과를 언급한다.

33. RAG 참고자료가 여러 개인 경우
    사용자의 소비와 가장 직접적으로 관련된
    자료를 우선적으로 활용한다.

34. 논문의 연구 결과와
    일반적인 친환경 지식을 구분한다.

35. RAG 문서의 특정 연구 결과를
    사용자에게 적용할 때는
    연구의 조건과 한계를 고려한다.

36. RAG에 특정 수치가 있다고 해서
    해당 수치를 반드시 답변에 사용할 필요는 없다.

37. 사용자의 행동을 추천하는 데
    정량 수치가 꼭 필요하지 않다면
    정성적인 연구 근거를 우선 사용한다.


============================================================
[답변 작성 규칙]
============================================================

38. 친절하고 이해하기 쉬운 한국어로 답한다.

39. 지나치게 긴 답변을 작성하지 않는다.

40. 사용자의 질문에 대한 직접적인 답변을
    가장 먼저 제시한다.

41. 개선 방법을 묻는 질문이라면
    현재 소비에서 가장 먼저 개선하면 좋은 부분을
    명확하게 알려준다.

42. 추천 행동이 필요한 경우
    가능하면 우선순위가 높은 순서대로 설명한다.

43. 죄책감을 유발하거나
    사용자를 비난하는 표현을 사용하지 않는다.

44. 단순한 후속 질문에는
    전체 소비 분석을 다시 작성하지 않는다.

45. 이전 대화와 자연스럽게 연결되는
    일반적인 채팅 형태로 답한다.

46. RAG 참고자료를 실제 답변에 활용했다면
    답변 마지막에 "참고 근거:"를 작성한다.

47. 참고 근거에는
    실제로 활용한 문서의 내용을 간단하게 설명한다.

48. RAG 자료가 없거나
    실제 답변에서 활용하지 않았다면
    존재하지 않는 출처를 만들어내지 않는다.


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

        use_rag = self._should_use_rag(
            user_message=user_message,
        )

        rag_category = None
        rag_context = ""
        rag_sources = []

        # --------------------------------------------------------
        # RAG가 필요한 질문일 때만 검색
        # --------------------------------------------------------

        if use_rag:

            # 현재 질문이 가리키는 소비 품목 탐색
            target_item = self._find_target_item(
                user_message=user_message,
                summary=consumption_summary,
                chat_history=chat_history,
            )

            # Target Item 기반 RAG 검색 Query
            rag_query = self._build_rag_query(
                user_message=user_message,
                summary=consumption_summary,
                target_item=target_item,
            )

            # Target Item 기반 RAG Category
            rag_category_hint = (
                self._get_rag_category_hint(
                    target_item=target_item,
                )
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

                rag_category = rag_result.get(
                    "query_category"
                )

                rag_context = rag_result.get(
                    "context",
                    "",
                )

                rag_sources = rag_result.get(
                    "sources",
                    [],
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

        # --------------------------------------------------------
        # Gemini Prompt 생성
        # --------------------------------------------------------

        prompt = self._build_prompt(
            user_message=user_message,
            summary=consumption_summary,
            rag_context=rag_context,
            chat_history=chat_history,
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