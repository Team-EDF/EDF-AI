from __future__ import annotations
from datetime import date, timedelta
from app.database.connection import get_db_connection
from app.api.schemas.merchant import ClassifyRequest, ClassifyResponse


class RecordService:
    def save_receipt(
        self,
        request: ClassifyRequest,
        response: ClassifyResponse,
        user_id: int | None = None,
        image_url: str | None = None,
        conn=None,
    ) -> int:
        """
        분류 결과를 DB에 저장.
        1) consumption_records 1행 INSERT
        2) items N행 INSERT
        3) periodic_stats (DAILY/WEEKLY/MONTHLY) UPSERT
        4) category_stats (메인 카테고리별) UPSERT
        반환값: record_id
        """
        record_date = None
        if request.payment_date:
            try:
                record_date = date.fromisoformat(request.payment_date)
            except ValueError:
                pass
        record_date = record_date or date.today()

        total_amount = (
            sum(i.amount_krw for i in request.items)
            if request.items
            else (request.total_amount_krw or 0)
        )

        # conn=None 이면 자체 연결 (단독 호출 시 하위 호환성 유지)
        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # 1) consumption_records
                cur.execute(
                    """
                    INSERT INTO consumption_records
                        (user_id, merchant_name, payment_location, source_type,
                         record_date, total_amount, total_carbon_kg, image_url)
                    VALUES (%s, %s, %s, 'receipt', %s, %s, %s, %s)
                    RETURNING record_id
                    """,
                    (
                        user_id,
                        request.merchant_name,
                        request.payment_location,
                        record_date,
                        total_amount,
                        response.total_carbon_kg or response.merchant_carbon_kg,
                        image_url,
                    ),
                )
                record_id: int = cur.fetchone()[0]

                # 2) items
                if response.item_results:
                    for item in response.item_results:
                        cat = item.category
                        cur.execute(
                            """
                            INSERT INTO items
                                (record_id, main_category_id, middle_category_id,
                                 source_type, source_msg, amount,
                                 classify_stage, carbon_kg)
                            VALUES (%s, %s, %s, 'item', %s, %s, %s, %s)
                            """,
                            (
                                record_id,
                                cat.category_id if (cat and cat.category_type == "main") else None,
                                cat.category_id if (cat and cat.category_type == "middle") else None,
                                item.item_name,
                                item.amount_krw,
                                cat.classify_stage if cat else None,
                                item.carbon_kg,
                            ),
                        )
                else:
                    cat = response.merchant_category
                    cur.execute(
                        """
                        INSERT INTO items
                            (record_id, main_category_id, middle_category_id,
                             source_type, source_msg, amount,
                             classify_stage, carbon_kg)
                        VALUES (%s, %s, %s, 'merchant', %s, %s, %s, %s)
                        """,
                        (
                            record_id,
                            cat.category_id if (cat and cat.category_type == "main") else None,
                            None,
                            request.merchant_name,
                            total_amount,
                            cat.classify_stage if cat else None,
                            response.merchant_carbon_kg,
                        ),
                    )

                # 3+4) 메인 카테고리별 집계 → periodic_stats + category_stats 누적
                self._update_stats(cur, record_id, record_date, user_id)

            conn.commit()
            return record_id
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
        방금 저장된 record_id의 items를 메인 카테고리 단위로 집계해
        periodic_stats(DAILY/WEEKLY/MONTHLY) 와 category_stats를 누적 UPSERT.

        category_stats는 periodic_stats와 독립된 테이블로,
        (user_id, period_type, period_start, main_category_id) 를 UPSERT 키로 사용한다.
        새 카테고리 조합 → INSERT / 기존 조합 → carbon + spending 누적 UPDATE.
        """
        # items → middle_category → main_category 경로로 집계
        cur.execute(
            """
            SELECT
                COALESCE(mc_main.main_category_id, i.main_category_id) AS main_cat_id,
                COALESCE(mc_main.main_name, direct.main_name, '미분류') AS main_cat_name,
                COALESCE(SUM(i.carbon_kg), 0) AS carbon,
                COALESCE(SUM(i.amount),    0) AS spending
            FROM items i
            LEFT JOIN middle_category mc
                   ON mc.middle_category_id = i.middle_category_id
            LEFT JOIN main_category mc_main
                   ON mc_main.main_category_id = mc.main_category_id
            LEFT JOIN main_category direct
                   ON direct.main_category_id = i.main_category_id
            WHERE i.record_id = %s
            GROUP BY
                COALESCE(mc_main.main_category_id, i.main_category_id),
                COALESCE(mc_main.main_name, direct.main_name, '미분류')
            """,
            (record_id,),
        )
        cat_rows = cur.fetchall()  # [(main_cat_id, main_cat_name, carbon, spending), ...]

        total_carbon   = sum(r[2] for r in cat_rows)
        total_spending = sum(r[3] for r in cat_rows)

        periods = {
            "DAILY":   record_date,
            "WEEKLY":  record_date - timedelta(days=record_date.weekday()),  # 해당 주 월요일
            "MONTHLY": record_date.replace(day=1),
        }

        for period_type, period_start in periods.items():
            # ── periodic_stats UPSERT (기간 합산용) ───────────────────────────
            if user_id is not None:
                cur.execute(
                    """
                    SELECT stat_id FROM periodic_stats
                    WHERE user_id = %s AND period_type = %s AND period_start = %s
                    """,
                    (user_id, period_type, period_start),
                )
            else:
                cur.execute(
                    """
                    SELECT stat_id FROM periodic_stats
                    WHERE user_id IS NULL AND period_type = %s AND period_start = %s
                    """,
                    (period_type, period_start),
                )

            row = cur.fetchone()
            if row:
                cur.execute(
                    """
                    UPDATE periodic_stats
                    SET total_carbon   = total_carbon   + %s,
                        total_spending = total_spending + %s
                    WHERE stat_id = %s
                    """,
                    (total_carbon, total_spending, row[0]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO periodic_stats
                        (user_id, period_type, period_start, total_carbon, total_spending)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (user_id, period_type, period_start, total_carbon, total_spending),
                )

            # ── category_stats UPSERT (카테고리별 누적) ───────────────────────
            # category_stats는 periodic_stats와 독립적으로 기간 정보를 직접 보유한다.
            # UNIQUE 인덱스(uq_category_stats)가 걸려 있어 ON CONFLICT로 단순하게 처리.
            for main_cat_id, main_cat_name, carbon, spending in cat_rows:
                cur.execute(
                    """
                    INSERT INTO category_stats
                        (user_id, period_type, period_start,
                         main_category_id, category_name,
                         category_carbon, category_spending)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (
                        COALESCE(user_id, -1),
                        period_type,
                        period_start,
                        COALESCE(main_category_id, -1)
                    )
                    DO UPDATE SET
                        category_carbon   = category_stats.category_carbon   + EXCLUDED.category_carbon,
                        category_spending = category_stats.category_spending + EXCLUDED.category_spending
                    """,
                    (
                        user_id, period_type, period_start,
                        main_cat_id, main_cat_name,
                        carbon, spending,
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
        category_stats 테이블에서 메인 카테고리별 집계 조회.
        category_stats가 독립 테이블이므로 periodic_stats JOIN 없이 직접 조회.
        percentage는 해당 기간 category_carbon 합계 대비 비율로 계산.
        """
        if period_start is None:
            period_start = date.today().replace(day=1)

        # user_id NULL 여부에 따라 조건 분기 (NULL = IS NULL 비교 필요)
        if user_id is not None:
            user_filter = "cs.user_id = %(user_id)s"
        else:
            user_filter = "cs.user_id IS NULL"

        # percentage: periodic_stats JOIN 없이 window 함수로 전체 합산 대비 비율 계산
        sql = f"""
        SELECT
            cs.main_category_id,
            cs.category_name,
            cs.category_carbon,
            cs.category_spending,
            CASE WHEN SUM(cs.category_carbon) OVER () > 0
                 THEN ROUND((cs.category_carbon / SUM(cs.category_carbon) OVER () * 100)::NUMERIC, 2)
                 ELSE 0
            END AS percentage
        FROM category_stats cs
        WHERE {user_filter}
          AND cs.period_type  = %(period_type)s
          AND cs.period_start = %(period_start)s
        ORDER BY cs.category_carbon DESC
        """

        # conn=None 이면 자체 연결 (단독 호출 시 하위 호환성 유지)
        own_conn = conn is None
        if own_conn:
            conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, {
                    "user_id":      user_id,
                    "period_type":  period_type,
                    "period_start": period_start,
                })
                rows = cur.fetchall()
                return [
                    {
                        "main_category_id":  r[0],
                        "category_name":     r[1],
                        "category_carbon":   r[2],
                        "category_spending": r[3],
                        "percentage":        float(r[4]) if r[4] is not None else 0.0,
                    }
                    for r in rows
                ]
        finally:
            if own_conn:
                conn.close()
