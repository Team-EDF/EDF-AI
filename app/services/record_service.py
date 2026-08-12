from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.api.schemas.merchant import (
    ClassifyRequest,
    ClassifyResponse,
)
from app.database.connection import get_db_connection


class RecordService:
    @staticmethod
    def _normalize_record_date(value: Any) -> date:
        """
        결제 일자를 DB에 저장 가능한 date 객체로 정규화한다.

        지원 형식:
        - None: 오늘 날짜
        - datetime: 날짜 부분만 사용
        - date: 그대로 사용
        - str: YYYY-MM-DD 형식으로 변환

        잘못된 문자열이나 지원하지 않는 타입이면 오늘 날짜를 사용한다.
        """
        if value is None:
            return date.today()

        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, date):
            return value

        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return date.today()

        return date.today()

    def save_receipt(
        self,
        request: ClassifyRequest,
        response: ClassifyResponse,
        user_id: int | None = None,
        image_url: str | None = None,
        conn=None,
    ) -> int:
        """
        OCR 분류 결과를 DB에 저장한다.

        처리 순서:
        1. consumption_records에 영수증 전체 정보 저장
        2. items에 품목별 분류 결과 저장
        3. periodic_stats에 일간·주간·월간 통계 누적
        4. category_stats에 대분류별 통계 누적

        반환값:
            생성된 consumption_records.record_id
        """
        record_date = self._normalize_record_date(
            request.payment_date
        )

        total_amount = (
            sum(item.amount_krw for item in request.items)
            if request.items
            else (request.total_amount_krw or 0)
        )

        own_conn = conn is None

        if own_conn:
            conn = get_db_connection()

        try:
            with conn.cursor() as cur:
                # 1. 영수증 전체 소비 기록 저장
                cur.execute(
                    """
                    INSERT INTO consumption_records
                        (
                            user_id,
                            merchant_name,
                            payment_location,
                            source_type,
                            record_date,
                            total_amount,
                            total_carbon_kg,
                            image_url
                        )
                    VALUES
                        (
                            %s,
                            %s,
                            %s,
                            'receipt',
                            %s,
                            %s,
                            %s,
                            %s
                        )
                    RETURNING record_id;
                    """,
                    (
                        user_id,
                        request.merchant_name,
                        request.payment_location,
                        record_date,
                        total_amount,
                        (
                            response.total_carbon_kg
                            if response.total_carbon_kg is not None
                            else response.merchant_carbon_kg
                        ),
                        image_url,
                    ),
                )

                inserted = cur.fetchone()

                if not inserted:
                    raise RuntimeError(
                        "consumption_records 저장 후 "
                        "record_id를 반환받지 못했습니다."
                    )

                record_id: int = inserted[0]

                # 2. 품목별 분류 결과 저장
                if response.item_results:
                    for item in response.item_results:
                        category = item.category

                        main_category_id = (
                            category.main_category_id
                            if category
                            else None
                        )

                        middle_category_id = (
                            category.middle_category_id
                            if category
                            else None
                        )

                        classify_stage = (
                            category.classify_stage
                            if category
                            else None
                        )

                        cur.execute(
                            """
                            INSERT INTO items
                                (
                                    record_id,
                                    main_category_id,
                                    middle_category_id,
                                    source_type,
                                    source_msg,
                                    amount,
                                    classify_stage,
                                    carbon_kg
                                )
                            VALUES
                                (
                                    %s,
                                    %s,
                                    %s,
                                    'item',
                                    %s,
                                    %s,
                                    %s,
                                    %s
                                );
                            """,
                            (
                                record_id,
                                main_category_id,
                                middle_category_id,
                                item.item_name,
                                item.amount_krw,
                                classify_stage,
                                item.carbon_kg,
                            ),
                        )

                # 품목이 없으면 가맹점 분류 결과를 하나의 item으로 저장
                else:
                    category = response.merchant_category

                    main_category_id = (
                        category.main_category_id
                        if category
                        else None
                    )

                    middle_category_id = (
                        category.middle_category_id
                        if category
                        else None
                    )

                    classify_stage = (
                        category.classify_stage
                        if category
                        else None
                    )

                    cur.execute(
                        """
                        INSERT INTO items
                            (
                                record_id,
                                main_category_id,
                                middle_category_id,
                                source_type,
                                source_msg,
                                amount,
                                classify_stage,
                                carbon_kg
                            )
                        VALUES
                            (
                                %s,
                                %s,
                                %s,
                                'merchant',
                                %s,
                                %s,
                                %s,
                                %s
                            );
                        """,
                        (
                            record_id,
                            main_category_id,
                            middle_category_id,
                            request.merchant_name,
                            total_amount,
                            classify_stage,
                            response.merchant_carbon_kg,
                        ),
                    )

                # 3~4. 기간별 및 카테고리별 통계 갱신
                self._update_stats(
                    cur=cur,
                    record_id=record_id,
                    record_date=record_date,
                    user_id=user_id,
                )

            conn.commit()
            return record_id

        except Exception:
            conn.rollback()
            raise

        finally:
            if own_conn:
                conn.close()

    def _update_stats(
        self,
        cur,
        record_id: int,
        record_date: date,
        user_id: int | None,
    ) -> None:
        """
        저장된 record_id의 품목을 대분류 단위로 집계한 뒤
        periodic_stats와 category_stats에 누적한다.
        """
        cur.execute(
            """
            SELECT
                COALESCE(
                    mc_main.main_category_id,
                    item.main_category_id
                ) AS main_cat_id,

                COALESCE(
                    mc_main.main_name,
                    direct_main.main_name,
                    '미분류'
                ) AS main_cat_name,

                COALESCE(
                    SUM(item.carbon_kg),
                    0
                ) AS carbon,

                COALESCE(
                    SUM(item.amount),
                    0
                ) AS spending

            FROM items item

            LEFT JOIN middle_category middle
                ON middle.middle_category_id
                 = item.middle_category_id

            LEFT JOIN main_category mc_main
                ON mc_main.main_category_id
                 = middle.main_category_id

            LEFT JOIN main_category direct_main
                ON direct_main.main_category_id
                 = item.main_category_id

            WHERE item.record_id = %s

            GROUP BY
                COALESCE(
                    mc_main.main_category_id,
                    item.main_category_id
                ),
                COALESCE(
                    mc_main.main_name,
                    direct_main.main_name,
                    '미분류'
                );
            """,
            (record_id,),
        )

        category_rows = cur.fetchall()

        total_carbon = sum(
            row[2] for row in category_rows
        )

        total_spending = sum(
            row[3] for row in category_rows
        )

        periods = {
            "DAILY": record_date,
            "WEEKLY": (
                record_date
                - timedelta(days=record_date.weekday())
            ),
            "MONTHLY": record_date.replace(day=1),
        }

        for period_type, period_start in periods.items():
            self._upsert_periodic_stats(
                cur=cur,
                user_id=user_id,
                period_type=period_type,
                period_start=period_start,
                total_carbon=total_carbon,
                total_spending=total_spending,
            )

            self._upsert_category_stats(
                cur=cur,
                user_id=user_id,
                period_type=period_type,
                period_start=period_start,
                category_rows=category_rows,
            )

    def _upsert_periodic_stats(
        self,
        cur,
        user_id: int | None,
        period_type: str,
        period_start: date,
        total_carbon,
        total_spending,
    ) -> None:
        """
        periodic_stats에 기간별 총 탄소배출량과 총 지출액을 누적한다.
        """
        if user_id is not None:
            cur.execute(
                """
                SELECT stat_id
                FROM periodic_stats
                WHERE user_id = %s
                  AND period_type = %s
                  AND period_start = %s
                LIMIT 1;
                """,
                (
                    user_id,
                    period_type,
                    period_start,
                ),
            )

        else:
            cur.execute(
                """
                SELECT stat_id
                FROM periodic_stats
                WHERE user_id IS NULL
                  AND period_type = %s
                  AND period_start = %s
                LIMIT 1;
                """,
                (
                    period_type,
                    period_start,
                ),
            )

        row = cur.fetchone()

        if row:
            cur.execute(
                """
                UPDATE periodic_stats
                SET
                    total_carbon
                        = total_carbon + %s,
                    total_spending
                        = total_spending + %s
                WHERE stat_id = %s;
                """,
                (
                    total_carbon,
                    total_spending,
                    row[0],
                ),
            )

        else:
            cur.execute(
                """
                INSERT INTO periodic_stats
                    (
                        user_id,
                        period_type,
                        period_start,
                        total_carbon,
                        total_spending
                    )
                VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    );
                """,
                (
                    user_id,
                    period_type,
                    period_start,
                    total_carbon,
                    total_spending,
                ),
            )

    def _upsert_category_stats(
        self,
        cur,
        user_id: int | None,
        period_type: str,
        period_start: date,
        category_rows,
    ) -> None:
        """
        category_stats에 기간별 대분류 탄소량과 지출액을 누적한다.
        """
        for (
            main_category_id,
            main_category_name,
            carbon,
            spending,
        ) in category_rows:
            cur.execute(
                """
                INSERT INTO category_stats
                    (
                        user_id,
                        period_type,
                        period_start,
                        main_category_id,
                        category_name,
                        category_carbon,
                        category_spending
                    )
                VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )

                ON CONFLICT
                    (
                        COALESCE(user_id, -1),
                        period_type,
                        period_start,
                        COALESCE(main_category_id, -1)
                    )

                DO UPDATE SET
                    category_carbon
                        = category_stats.category_carbon
                        + EXCLUDED.category_carbon,

                    category_spending
                        = category_stats.category_spending
                        + EXCLUDED.category_spending;
                """,
                (
                    user_id,
                    period_type,
                    period_start,
                    main_category_id,
                    main_category_name,
                    carbon,
                    spending,
                ),
            )

    def get_category_stats(
        self,
        user_id: int | None = None,
        period_type: str = "MONTHLY",
        period_start: date | None = None,
        conn=None,
    ) -> list[dict]:
        """
        category_stats에서 대분류별 통계를 조회한다.

        반환 항목:
        - main_category_id
        - category_name
        - category_carbon
        - category_spending
        - percentage
        """
        if period_start is None:
            period_start = date.today().replace(day=1)

        if user_id is not None:
            user_filter = "stats.user_id = %(user_id)s"
        else:
            user_filter = "stats.user_id IS NULL"

        sql = f"""
            SELECT
                stats.main_category_id,
                stats.category_name,
                stats.category_carbon,
                stats.category_spending,

                CASE
                    WHEN SUM(
                        stats.category_carbon
                    ) OVER () > 0
                    THEN ROUND(
                        (
                            stats.category_carbon
                            / SUM(
                                stats.category_carbon
                            ) OVER ()
                            * 100
                        )::NUMERIC,
                        2
                    )
                    ELSE 0
                END AS percentage

            FROM category_stats stats

            WHERE {user_filter}
              AND stats.period_type
                    = %(period_type)s
              AND stats.period_start
                    = %(period_start)s

            ORDER BY
                stats.category_carbon DESC;
        """

        own_conn = conn is None

        if own_conn:
            conn = get_db_connection()

        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {
                        "user_id": user_id,
                        "period_type": period_type,
                        "period_start": period_start,
                    },
                )

                rows = cur.fetchall()

                return [
                    {
                        "main_category_id": row[0],
                        "category_name": row[1],
                        "category_carbon": row[2],
                        "category_spending": row[3],
                        "percentage": (
                            float(row[4])
                            if row[4] is not None
                            else 0.0
                        ),
                    }
                    for row in rows
                ]

        finally:
            if own_conn:
                conn.close()