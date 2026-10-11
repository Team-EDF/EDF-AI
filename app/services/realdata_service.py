"""
실데이터(영수증) 기반 영역별 월 탄소량 집계.

설문 대신 사용자가 실제로 등록한 영수증으로 이동/식품/카페/쇼핑 영역의 최근 30일 배출량(kg)과
지출(원)을 계산한다. 영역 레벨 기준선이 "월 탄소량(kg)"이라서, 30일 합계를 그대로 같은 기준선에
쓸 수 있다.

원본: consumption_records(영수증) + 영수증의 ocr_data(JSON, 품목별 카테고리/탄소/금액).
백엔드 대시보드(RecordConfirmService)가 category_stats를 만들 때 읽는 것과 같은 JSON이라
사용자가 화면에서 보는 집계와 일치한다. (items 테이블은 팀이 10/2에 채우기 시작해서 그 이전
영수증에는 없고, category_stats는 월 단위로만 합쳐져 있어 쓰지 않는다.)

주의
- user_id로 걸러서 읽는다. 로컬 DB에는 user_id가 없는 AI 테스트용 영수증이 많이 섞여 있다.
- 확정(ocr_status='SUCCESS')된 영수증만 센다. 확인 대기(WAITING_CONFIRM)는 아직 대시보드에 안 들어간다.
"""
import json
from datetime import date, timedelta

from app.database.connection import get_db_connection

DATA_WINDOW_DAYS = 30        # 최근 며칠치 영수증을 볼지 (월 탄소량 기준선과 맞추려고 30일)
DATA_MIN_RECEIPTS = 5        # 이 건수 이상이어야 실데이터를 믿고 프로필을 계산한다

# 메인 카테고리 -> 프로필 영역. 식음료는 카페 여부에 따라 food/cafe로 나눈다.
AREA_BY_MAIN_CATEGORY = {
    "교통": "move",
    "대중교통": "move",
    "쇼핑소비재": "shop",
    "식음료": "food",
}
PROFILE_AREA_KEYS = ("move", "food", "cafe", "shop")

# 카페 영수증으로 보는 가맹점 이름 (소문자로 비교). 식음료 영수증 중 이 이름이 들어가면 cafe 영역.
CAFE_KEYWORDS = (
    "카페", "커피", "cafe", "coffee", "스타벅스", "투썸", "이디야", "메가mgc", "메가커피",
    "컴포즈", "빽다방", "할리스", "폴바셋", "블루보틀", "탐앤탐스", "엔제리너스", "더벤티", "매머드",
)


def is_cafe_merchant(merchant_name: str | None) -> bool:
    """가맹점 이름이 카페/커피 계열인지."""
    if not merchant_name:
        return False
    name = merchant_name.lower()
    return any(keyword in name for keyword in CAFE_KEYWORDS)


def _to_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_record_entries(ocr_data: str | None, total_carbon_kg=None, total_amount=None) -> list[dict]:
    """
    영수증 ocr_data(JSON)에서 [{main_name, carbon_kg, amount_krw}, ...]를 꺼낸다.
    백엔드 RecordConfirmService와 같은 규칙: 품목(item_results)이 있으면 품목별로,
    없으면 가맹점 카테고리(merchant_category) 한 줄로 본다. 읽을 수 없으면 빈 목록.
    """
    if not ocr_data:
        return []
    try:
        data = json.loads(ocr_data)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []

    entries = []
    for item in data.get("item_results") or []:
        category = item.get("category") if isinstance(item, dict) else None
        if not category:
            continue
        entries.append({
            "main_name": category.get("main_name") or "미분류",
            "carbon_kg": _to_float(item.get("carbon_kg")),
            "amount_krw": _to_float(item.get("amount_krw")),
        })
    if entries:
        return entries

    merchant_category = data.get("merchant_category")
    if isinstance(merchant_category, dict):
        carbon = data.get("merchant_carbon_kg")
        return [{
            "main_name": merchant_category.get("main_name") or "미분류",
            "carbon_kg": _to_float(carbon if carbon is not None else total_carbon_kg),
            "amount_krw": _to_float(data.get("total_amount_krw") if data.get("total_amount_krw") is not None else total_amount),
        }]
    return []


def aggregate_areas(records: list[dict]) -> dict[str, dict]:
    """
    영수증 목록을 영역별 {carbon_kg, spend_krw}로 합친다.
    records: [{"merchant_name", "ocr_data", "total_carbon_kg", "total_amount"}, ...]
    """
    areas = {key: {"carbon_kg": 0.0, "spend_krw": 0.0} for key in PROFILE_AREA_KEYS}
    for record in records:
        cafe_receipt = is_cafe_merchant(record.get("merchant_name"))
        for entry in parse_record_entries(record.get("ocr_data"), record.get("total_carbon_kg"), record.get("total_amount")):
            area = AREA_BY_MAIN_CATEGORY.get(entry["main_name"])
            if area is None:
                continue  # 가정에너지/건강의료/여가문화/기타는 프로필 영역이 아니다
            if area == "food" and cafe_receipt:
                area = "cafe"
            areas[area]["carbon_kg"] += entry["carbon_kg"]
            areas[area]["spend_krw"] += entry["amount_krw"]
    return {
        key: {"carbon_kg": round(value["carbon_kg"], 3), "spend_krw": int(round(value["spend_krw"]))}
        for key, value in areas.items()
    }


def fetch_receipts(user_id: int, start: date, end: date, conn=None) -> list[dict]:
    """사용자의 기간 내 확정(SUCCESS) 영수증을 읽는다."""
    own_conn = conn is None
    if own_conn:
        conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT merchant_name, ocr_data, total_carbon_kg, total_amount
            FROM consumption_records
            WHERE user_id = %s
              AND ocr_status = 'SUCCESS'
              AND record_date BETWEEN %s AND %s;
            """,
            (user_id, start, end),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        if own_conn:
            conn.close()
    return [
        {"merchant_name": r[0], "ocr_data": r[1], "total_carbon_kg": r[2], "total_amount": r[3]}
        for r in rows
    ]


def get_area_stats(
    user_id: int,
    conn=None,
    today: date | None = None,
    window_days: int = DATA_WINDOW_DAYS,
    min_receipts: int = DATA_MIN_RECEIPTS,
) -> dict:
    """
    사용자의 최근 window_days일 영수증으로 영역별 배출량을 집계한다.
    반환: {"receipts", "window_days", "min_receipts", "enough", "areas": {영역: {carbon_kg, spend_krw}}}
    enough가 False면 영수증이 모자라 실데이터로 프로필을 만들지 않는다 (areas는 참고용).
    """
    end = today or date.today()
    start = end - timedelta(days=window_days - 1)
    records = fetch_receipts(user_id, start, end, conn=conn)
    return {
        "receipts": len(records),
        "window_days": window_days,
        "min_receipts": min_receipts,
        "enough": len(records) >= min_receipts,
        "areas": aggregate_areas(records),
    }
