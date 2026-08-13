import json
import os
from collections import defaultdict

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.services.chat_history_service import save_chat_history
from app.services.rag_retriever_service import (
    retrieve_eco_documents,
)


load_dotenv()


GEMINI_MODEL = "gemini-2.5-flash"

_PROJECT_ID = os.getenv(
    "GEMINI_PROJECT_ID"
)

_LOCATION = os.getenv(
    "GEMINI_LOCATION",
    "us-central1",
)


class FeedbackService:
    """
    소비기록 기반 친환경 피드백 서비스.

    처리 과정:

    record_id
        ↓
    consumption_records 조회
        ↓
    items + 카테고리 조회
        ↓
    소비 데이터 요약
        ↓
    탄소배출 상위 품목 중심 RAG 검색
        ↓
    Gemini 친환경 피드백 생성
        ↓
    chat_history 저장
    """

    _client: genai.Client | None = None

    # ============================================================
    # Gemini Client
    # ============================================================

    @classmethod
    def _get_client(cls) -> genai.Client:
        """
        Vertex AI Gemini client를
        최초 한 번 생성하고 재사용한다.
        """

        if cls._client is None:

            if not _PROJECT_ID:
                raise RuntimeError(
                    "GEMINI_PROJECT_ID "
                    "환경변수가 설정되지 않았습니다."
                )

            cls._client = genai.Client(
                vertexai=True,
                project=_PROJECT_ID,
                location=_LOCATION,
            )

        return cls._client

    # ============================================================
    # 소비 기록 조회
    # ============================================================

    def get_consumption_summary(
        self,
        record_id: int,
        conn,
    ) -> dict | None:
        """
        record_id의 소비기록 및 품목정보를 조회하고
        피드백 생성용 summary로 변환한다.

        # [수정] 원래는 consumption_records.merchant_name/payment_location과
        # 별도 items 테이블을 JOIN해서 읽었으나, 실제로 배포된 테이블은
        # 백엔드(RecordConfirmService/ConsumptionRecord 엔티티) 기준이라
        # 그 컬럼/테이블이 존재하지 않아 매번 500 에러가 났다.
        # 백엔드는 품목·카테고리 분류 결과를 정규화하지 않고
        # AI의 /ocr/classify 응답(ClassifyResponse) 전체를
        # consumption_records.ocr_data에 JSON 문자열로 그대로 저장하므로,
        # 여기서도 items 테이블 대신 ocr_data JSON을 파싱해서 사용한다.
        """

        cursor = conn.cursor()

        try:
            # =====================================================
            # 소비기록 조회 (ocr_data JSON에 품목/카테고리 정보 포함)
            # =====================================================

            cursor.execute(
                """
                SELECT
                    record_id,
                    ocr_data,
                    record_date,
                    total_amount,
                    total_carbon_kg

                FROM consumption_records

                WHERE record_id = %s

                LIMIT 1;
                """,
                (record_id,),
            )

            record = cursor.fetchone()

            if not record:
                return None

            (
                db_record_id,
                ocr_data,
                record_date,
                total_amount,
                total_carbon_kg,
            ) = record

        finally:
            cursor.close()

        # [수정] ClassifyResponse가 저장된 ocr_data JSON 파싱
        parsed = json.loads(ocr_data) if ocr_data else {}

        merchant_name = parsed.get("merchant_name")
        payment_location = parsed.get("payment_location")
        item_results = parsed.get("item_results") or []
        merchant_category = parsed.get("merchant_category")
        merchant_carbon_kg = parsed.get("merchant_carbon_kg")

        # =========================================================
        # 카테고리별 합계 계산
        # =========================================================

        category_carbon = defaultdict(float)
        category_amount = defaultdict(int)

        item_list = []

        # [수정] 품목별 분류 결과(item_results)가 있으면 품목 단위로 집계하고,
        # 없으면(가맹점명만으로 분류된 경우) merchant_category로 대체한다.
        # RecordConfirmService.confirmByRecordId()의 집계 로직과 동일하게 맞춤.
        if item_results:
            for index, item in enumerate(item_results):
                category = item.get("category") or {}

                item_name = item.get("item_name")
                amount_value = int(item.get("amount_krw") or 0)
                carbon_value = float(item.get("carbon_kg") or 0.0)

                main_category_id = category.get("main_category_id")
                main_name = category.get("main_name")
                middle_category_id = category.get("middle_category_id")
                middle_name = category.get("middle_name")
                classify_stage = category.get("classify_stage")

                category_name = main_name if main_name else "미분류"

                category_carbon[category_name] += carbon_value
                category_amount[category_name] += amount_value

                item_list.append(
                    {
                        "item_id":
                            index,

                        "item_name":
                            item_name,

                        "amount_krw":
                            amount_value,

                        "main_category_id":
                            main_category_id,

                        "main_name":
                            main_name,

                        "middle_category_id":
                            middle_category_id,

                        "middle_name":
                            middle_name,

                        "classify_stage":
                            classify_stage,

                        "carbon_kg":
                            carbon_value,
                    }
                )
        elif merchant_category:
            main_name = merchant_category.get("main_name")
            category_name = main_name if main_name else "미분류"
            carbon_value = float(merchant_carbon_kg or 0.0)
            amount_value = int(total_amount or 0)

            category_carbon[category_name] += carbon_value
            category_amount[category_name] += amount_value

            item_list.append(
                {
                    "item_id":
                        0,

                    "item_name":
                        merchant_name,

                    "amount_krw":
                        amount_value,

                    "main_category_id":
                        merchant_category.get("main_category_id"),

                    "main_name":
                        main_name,

                    "middle_category_id":
                        merchant_category.get("middle_category_id"),

                    "middle_name":
                        merchant_category.get("middle_name"),

                    "classify_stage":
                        merchant_category.get("classify_stage"),

                    "carbon_kg":
                        carbon_value,
                }
            )

        # =========================================================
        # 가장 탄소배출량이 높은 카테고리
        # =========================================================

        highest_carbon_category = None

        if category_carbon:
            highest_carbon_category = max(
                category_carbon,
                key=category_carbon.get,
            )

        # =========================================================
        # 최종 소비 Summary
        # =========================================================

        return {
            "record_id":
                db_record_id,

            "merchant_name":
                merchant_name,

            "payment_location":
                payment_location,

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
                category: round(
                    value,
                    6,
                )
                for category, value
                in category_carbon.items()
            },

            "category_amount_summary":
                dict(category_amount),

            "highest_carbon_category":
                highest_carbon_category,

            "items":
                item_list,
        }

    # ============================================================
    # RAG 검색 Query
    # ============================================================

    def _build_rag_query(
        self,
        user_message: str,
        summary: dict,
    ) -> str:
        """
        사용자 질문과 실제 소비기록을 이용해
        RAG 검색용 query를 생성한다.

        전체 품목을 모두 넣지 않고,
        탄소배출량이 높은 상위 품목과
        해당 품목의 중분류를 우선적으로 사용한다.
        """

        items = summary.get(
            "items",
            [],
        )

        # =========================================================
        # 탄소배출량 기준 내림차순 정렬
        # =========================================================

        sorted_items = sorted(
            items,
            key=lambda item: (
                item.get("carbon_kg")
                or 0
            ),
            reverse=True,
        )

        # =========================================================
        # 탄소배출량 상위 3개 품목
        # =========================================================

        top_items = (
            sorted_items[:3]
        )

        top_item_names = [
            item.get("item_name")
            for item in top_items
            if item.get("item_name")
        ]

        # =========================================================
        # 상위 품목 중분류 추출
        # =========================================================

        top_middle_categories = list(
            dict.fromkeys(
                item.get("middle_name")
                for item in top_items
                if item.get("middle_name")
            )
        )

        # =========================================================
        # RAG Query 생성
        # =========================================================

        return f"""
사용자 질문:
{user_message}

탄소배출량이 높은 주요 소비 품목:
{top_item_names}

주요 세부 카테고리:
{top_middle_categories}
""".strip()

    # ============================================================
    # Gemini Prompt
    # ============================================================

    def _build_prompt(
        self,
        user_message: str,
        summary: dict,
        rag_context: str,
    ) -> str:
        """
        Gemini에게 전달할 최종 친환경 피드백 Prompt.

        RAG 연구의 정량 수치를
        사용자 개인 예상 절감량으로
        직접 변환하지 않는다.
        """

        return f"""
너는 소비 기반 탄소 발자국 플랫폼
GreenStep의 친환경 소비 피드백 AI다.

사용자의 실제 소비 및 탄소배출량을 분석하고,
제공된 RAG 참고자료를 근거로
실천 가능한 친환경 소비 피드백을 제공한다.


============================================================
[사용자의 소비 정보]
============================================================

가맹점:
{summary.get("merchant_name")}

결제 위치:
{summary.get("payment_location")}

결제일:
{summary.get("record_date")}

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
[RAG 참고자료]
============================================================

{rag_context}


============================================================
[사용자 질문]
============================================================

{user_message}


============================================================
[기본 분석 규칙]
============================================================

1. 소비금액이 아니라 탄소배출량을 기준으로
   소비 우선순위를 분석한다.

2. 탄소배출량이 높은 품목 또는 카테고리를
   우선적으로 분석한다.

3. 사용자가 실제로 실행할 수 있는
   친환경 행동을 2~3개 추천한다.

4. 사용자가 구매하지 않은 품목이나 서비스를
   구매했다고 가정하지 않는다.

5. 소비금액과 탄소배출량을 혼동하지 않는다.

6. 사용자 소비 데이터와 RAG 자료가 충돌할 경우
   사용자의 실제 소비 데이터를 우선한다.

7. RAG 참고자료가 사용자의 소비와 직접 관련되지 않는다면
   억지로 해당 자료를 연결하지 않는다.

8. RAG 근거가 충분하지 않은 경우
   일반적인 친환경 실천방안을 제안할 수 있지만,
   구체적인 정량 수치는 제시하지 않는다.


============================================================
[탄소 수치 사용 규칙 - 매우 중요]
============================================================

9. 근거 없는 탄소배출량,
   탄소 절감량 또는 절감률을 절대 만들어내지 않는다.

10. RAG 문서에 없는 구체적인 수치를
    생성하거나 추정하지 않는다.

11. RAG 문서에 탄소 감축 수치가 존재하더라도,
    그 수치는 해당 연구의 국가, 연구대상,
    기간, 소비조건, 생활방식 및 시나리오에서
    산출된 연구 결과로만 취급한다.

12. 논문이나 보고서에서 제시된 평균 탄소 감축량을
    현재 사용자의 개인 예상 탄소 감축량으로
    직접 적용하지 않는다.

13. 다음과 같은 형태의 개인화된 수치 표현은
    별도의 사용자별 계산 근거가 없는 경우 사용하지 않는다.

    잘못된 예:
    "자동차 이용을 줄이면
    당신은 연간 1,500 kgCO2e를 절감할 수 있습니다."

    잘못된 예:
    "텀블러를 사용하면
    당신의 탄소배출량이 30% 감소합니다."

14. 사용자 개인의 탄소 절감량을 계산하려면
    현재 이용량, 대체 행동,
    관련 배출계수 및 계산식이 모두 필요하다.

    이 정보가 현재 소비 데이터에 충분히 존재하지 않는다면
    개인 절감량을 계산하지 않는다.

15. RAG 연구 결과의 숫자를 사용자의 소비금액,
    탄소배출량 또는 구매횟수에 단순 비례하여
    임의 환산하지 않는다.

16. 연구 수치를 언급해야 할 경우
    반드시 연구 결과라는 사실과 적용 조건을 함께 표현한다.

17. 연구 수치를 설명할 때는 가능한 경우
    다음과 같은 표현을 사용한다.

    - "연구에서는"
    - "해당 연구 조건에서는"
    - "연구 대상 국가에서는"
    - "연구 시나리오에서는"
    - "평균적으로"
    - "감축 잠재력이 큰 행동으로 평가되었습니다"

18. 연구 결과의 수치를 사용자에게 설명할 때
    연구 대상 국가나 조건을 알 수 있다면
    반드시 함께 밝힌다.

19. 다른 국가에서 수행된 연구 결과를
    한국 사용자에게 그대로 적용하지 않는다.

20. 연구의 탄소 감축 수치는
    행동의 상대적 효과나 우선순위를 설명하기 위한
    근거로 사용하는 것을 우선한다.

21. 개인화된 피드백에서는
    정확한 감축량을 임의 제시하기보다

    - "감축 잠재력이 큰 행동"
    - "상대적으로 탄소 감축 효과가 큰 선택"
    - "탄소배출을 줄이는 데 도움이 될 수 있는 행동"

    등의 표현을 우선 사용한다.


============================================================
[RAG 근거 사용 규칙]
============================================================

22. RAG 참고자료에 명확한 근거가 있을 때만
    특정 연구 결과를 언급한다.

23. RAG 참고자료가 여러 개인 경우
    사용자의 소비와 가장 직접적으로 관련된
    자료를 우선적으로 활용한다.

24. 논문의 연구 결과와
    자신의 일반적인 친환경 지식을 구분한다.

25. RAG 문서의 특정 연구 결과를
    실제 사용자에게 적용할 때는
    연구의 조건과 한계를 고려한다.

26. 서로 다른 연구의 수치를 임의로 조합하여
    새로운 탄소 감축 수치를 계산하지 않는다.

27. RAG에 특정 수치가 있다고 해서
    해당 수치를 반드시 답변에 사용할 필요는 없다.

28. 사용자의 행동을 추천하는 데
    정량 수치가 꼭 필요하지 않다면
    정성적인 연구 근거를 우선 사용한다.


============================================================
[답변 작성 규칙]
============================================================

29. 친절하고 이해하기 쉬운 한국어로 답한다.

30. 지나치게 긴 답변을 작성하지 않는다.

31. 사용자의 현재 소비에서
    가장 먼저 개선하면 좋은 부분을 명확히 알려준다.

32. 추천 행동은 가능하면
    우선순위가 높은 순서대로 설명한다.

33. 죄책감을 유발하거나
    사용자를 비난하는 표현을 사용하지 않는다.

34. RAG 참고자료를 활용했다면
    답변 마지막에 반드시
    "참고 근거:"를 작성한다.

35. 참고 근거에는
    실제로 활용한 문서의 내용을 간단하게 설명한다.

36. RAG 자료가 없거나
    실제 답변에서 활용하지 않았다면
    존재하지 않는 출처를 만들어내지 않는다.


============================================================
[답변]
============================================================
""".strip()

    # ============================================================
    # RAG + Gemini 피드백 생성
    # ============================================================

    def generate_feedback(
        self,
        user_message: str,
        consumption_summary: dict,
    ) -> dict:
        """
        소비요약
            ↓
        RAG 검색
            ↓
        Gemini 피드백 생성
        """

        # =========================================================
        # RAG 검색 Query 생성
        # =========================================================

        rag_query = self._build_rag_query(
            user_message=user_message,
            summary=consumption_summary,
        )

        # =========================================================
        # RAG 문서 검색
        # =========================================================

        try:
            rag_result = (
                retrieve_eco_documents(
                    query=rag_query,
                    k=3,
                )
            )

            rag_category = (
                rag_result.get(
                    "query_category"
                )
            )

            rag_context = (
                rag_result.get(
                    "context",
                    "",
                )
            )

            rag_sources = (
                rag_result.get(
                    "sources",
                    [],
                )
            )

        except Exception as error:

            print(
                "[FeedbackService] "
                f"RAG 검색 실패: "
                f"{type(error).__name__}: {error}"
            )

            rag_category = None
            rag_context = ""
            rag_sources = []

        # =========================================================
        # Gemini Prompt 생성
        # =========================================================

        prompt = self._build_prompt(
            user_message=user_message,
            summary=consumption_summary,
            rag_context=rag_context,
        )

        # =========================================================
        # Gemini 실행
        # =========================================================

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

        # =========================================================
        # Gemini Debug
        # =========================================================

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

        # =========================================================
        # Gemini 응답 확인
        # =========================================================

        if not response.text:
            raise RuntimeError(
                "Gemini가 피드백 응답을 "
                "반환하지 않았습니다."
            )

        return {
            "feedback":
                response.text.strip(),

            "rag_category":
                rag_category,

            "rag_sources":
                rag_sources,
        }

    # ============================================================
    # 최종 Feedback Chat
    # ============================================================

    def chat(
        self,
        record_id: int,
        user_message: str,
        conn,
    ) -> dict:
        """
        /feedback/chat API의 최종 진입점.

        1. 소비기록 조회
        2. RAG 검색
        3. Gemini 피드백 생성
        4. 채팅내역 저장
        5. API 응답 반환
        """

        # =========================================================
        # 1. 소비기록 조회
        # =========================================================

        summary = self.get_consumption_summary(
            record_id=record_id,
            conn=conn,
        )

        if summary is None:
            raise ValueError(
                f"record_id={record_id}인 "
                "소비기록을 찾을 수 없습니다."
            )

        # =========================================================
        # 2. RAG + Gemini 피드백
        # =========================================================

        generated = self.generate_feedback(
            user_message=user_message,
            consumption_summary=summary,
        )

        feedback_text = (
            generated["feedback"]
        )

        rag_category = (
            generated.get(
                "rag_category"
            )
        )

        rag_sources = (
            generated.get(
                "rag_sources",
                [],
            )
        )

        # =========================================================
        # 3. 채팅내역 저장
        # =========================================================

        chat_id = save_chat_history(
            conn=conn,
            record_id=record_id,
            user_id=None,
            user_message=user_message,
            consumption_summary=summary,
            ai_response=feedback_text,
        )

        # =========================================================
        # 4. API 응답
        # =========================================================

        return {
            "chat_id":
                chat_id,

            "record_id":
                record_id,

            "message":
                user_message,

            "feedback":
                feedback_text,

            "total_carbon_kg":
                summary.get(
                    "total_carbon_kg"
                ),

            "highest_carbon_category":
                summary.get(
                    "highest_carbon_category"
                ),

            "rag_category":
                rag_category,

            "rag_sources":
                rag_sources,
        }