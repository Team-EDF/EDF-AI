from app.database.connection import get_db_connection


class MerchantClassifier:

    def get_industry_name(self, merchant_name: str) -> str | None:
        """
        가맹점명으로 MERCHANT_SME 테이블에서 표준산업분류명 반환.
        매칭 실패 시 None 반환.
        """
        if not merchant_name or not merchant_name.strip():
            return None

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT industry_name
            FROM MERCHANT_SME
            WHERE original_name = %s
            LIMIT 1;
            """,
            (merchant_name.strip(),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        return row[0] if row else None
