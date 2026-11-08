"""
영수증 검증/보정 단위 테스트 (receipt_validation_service).

각 케이스는 실제 영수증 118장 일괄 테스트에서 나온 문제를 최소 형태로 재현한 것이다.
실행: python -m pytest tests/test_receipt_validation.py  (프로젝트 루트에서)
"""
import sys

sys.path.insert(0, ".")

from app.services import receipt_validation_service as v


def test_수량_2_이상이면_줄금액으로_보정():
    # 영화표: 성인 16,000 × 2 = 32,000 인데 단가 16,000만 품목 가격으로 읽힌 경우
    parsed = {"total_amount": 32000, "items": [{"name": "성인", "price": 16000}]}

    assert v.reconcile_quantities(parsed) is True
    assert parsed["items"][0]["price"] == 32000
    assert parsed["items"][0]["quantity"] == 2


def test_수량_보정은_정확히_맞을_때만():
    # 어떤 수량 조합으로도 총액이 안 맞으면 건드리지 않는다.
    parsed = {
        "total_amount": 12345,
        "items": [{"name": "a", "price": 5000}, {"name": "b", "price": 3000}],
    }

    assert v.reconcile_quantities(parsed) is False
    assert [i["price"] for i in parsed["items"]] == [5000, 3000]


def test_수량_보정은_애매하면_건드리지_않는다():
    # 1000×1 + 1000×3 == 1000×3 + 1000×1 처럼 해가 둘이면 보정하지 않는다.
    parsed = {
        "total_amount": 4000,
        "items": [{"name": "a", "price": 1000}, {"name": "b", "price": 1000}],
    }

    assert v.reconcile_quantities(parsed) is False


def test_이미_맞는_영수증은_그대로():
    parsed = {"total_amount": 8000, "items": [{"name": "a", "price": 8000}]}

    assert v.reconcile_quantities(parsed) is False
    assert "quantity" not in parsed["items"][0]


def test_총액이_공급가액으로_잡힌_경우_합계로_보정():
    # 카드 매출전표: 공급가액 5,455 / 부가세 545 / 합계 6,000
    parsed = {"total_amount": 5455}
    raw = "금 액\n5. 455 원\n부가세\n545 원\n합 계\n6,000 원"

    assert v.fix_total_amount(parsed, raw) is True
    assert parsed["total_amount"] == 6000


def test_총액이_부가세로_잡힌_경우_합계로_보정():
    parsed = {"total_amount": 2727}
    raw = "27,273원\n부가가치세\n2,727원\n합계금액\n30,000"

    assert v.fix_total_amount(parsed, raw) is True
    assert parsed["total_amount"] == 30000


def test_정상_총액은_보정하지_않는다():
    parsed = {"total_amount": 13960}
    raw = "결제금액 13,960원\n과세 12,691\n부가세 1,269"

    assert v.fix_total_amount(parsed, raw) is False
    assert parsed["total_amount"] == 13960


def test_주문서는_영수증이_아니다():
    raw = "[주문\n서]\n주문내역\n상품명\n수량\n고기듬뿍곰탕\n총주문금액\n13,500원\n주문일시: 2026-08-29"

    assert v.classify_document(raw) == "order_form"


def test_결제정보가_있으면_제목에_주문서가_있어도_영수증():
    raw = "주문서 번호 12\n신용카드 승인번호 12345678\n카드번호 5327-****\n부가세 590\n합계 6,500원"

    assert v.classify_document(raw) == "receipt"


def test_일반_영수증은_영수증():
    assert v.classify_document("영수증\n라떼 4,500\n합계 4,500\n신용카드") == "receipt"


def test_주소_조각이_품목으로_읽힌_경우_제거():
    raw = "경기 용인시 기흥구 덕영대로2077번길 18 (영덕동)\n아메리카노 4,500"
    parsed = {"items": [{"name": "영덕동", "price": 13637}, {"name": "아메리카노", "price": 4500}]}

    assert v.drop_address_items(parsed, raw) == 1
    assert [i["name"] for i in parsed["items"]] == ["아메리카노"]


def test_주소와_글자가_같아도_주소_줄에_없으면_품목_유지():
    # '소보로', '우동'은 이름이 '로', '동'으로 끝나지만 주소가 아니다.
    raw = "대전 중구 대종로480번길 15\n소보로 3,000\n우동 8,000"
    parsed = {"items": [{"name": "소보로", "price": 3000}, {"name": "우동", "price": 8000}]}

    assert v.drop_address_items(parsed, raw) == 0
    assert len(parsed["items"]) == 2


def test_환불_영수증은_헤더_반품표시와_마이너스_금액으로_판정():
    raw = (
        "CJ 올리브영 목동점\n2026-07-11 14:41:02\n+ 반품이 고객용\n카드전표\n"
        "상품명 단가 수량 금액\n6,900 -1\n-5,900\n17,900 -1\n-12,900\n"
        "신용카드\n-3,000\n-15,800\n결제금액\n-15,800"
    )

    assert v.is_refund(raw) is True


def test_정상_영수증의_할인_줄과_반품_안내문은_환불이_아니다():
    raw = (
        "에코마트\n2026-05-03\n상품명 단가 수량 금액\n우유 3,980 1 3,980\n"
        "할인금액: -1,820\n합계 15,390\n신용카드 15,390\n"
        "※ 반품시 영수증을 지참해 주세요. 교환/환불은 3일 이내"
    )

    assert v.is_refund(raw) is False


def test_전표_제목은_가맹점명이_아니다():
    # '[신용구매]'가 가맹점명으로 읽혀 '자동차구입'으로 분류된 실제 사례
    from app.services.ocr_fallback_service import _is_garbage_merchant

    for title in ["[신용구매]", "신용구매", "IC신용승인", "신용카드 매출전표", "카드매출전표", "가맹점명"]:
        assert _is_garbage_merchant(title) is True, title

    for name in ["STORYPCLAB 한신대점", "문정헤어", "우리반점", "신용산약국", "신전떡볶이 마곡점"]:
        assert _is_garbage_merchant(name) is False, name


def test_깨진_품목명_판별():
    # 일괄 테스트에서 합계는 맞았지만 분류가 틀어졌던 실제 품목명
    for name in ["갤 금액", "2스쿱", "h2 일반", "수량 판매금액", "", "성인"]:
        assert v.is_suspicious_item_name(name) is True, name

    for name in ["아메리카노", "돈까스", "우유", "고추장제육쌈김밥", "농심 짜왕멀티팩", "CJ)햇반매일잡곡", "3분카레"]:
        assert v.is_suspicious_item_name(name) is False, name


def test_머리글_없는_영수증도_품목_줄을_감지():
    # 스타벅스 현금영수증: '단가', '수량' 머리글이 없다 (실제 OCR 원문의 해당 부분)
    raw = (
        "STARBUCKS®\n영등포구청점\n현금(지출증빙)\n2022-09-26 11:26:31\n"
        "G)콜드브루\n뚜뚜호 (A-50)\n5,400 1 5,400\n합계 STARBUCKS 5,400\n결제금액\n(부가세포함)\n5,400\n(491)"
    )

    assert v.has_item_line(raw) is True


def test_두_줄로_쪼개진_품목_줄도_감지():
    assert v.has_item_line("아메리카노\n4,500 2\n9,000\n합계 9,000") is True


def test_카드_전표는_품목_줄이_없다():
    raw = (
        "[신용구매]\n가맹점명 : STORYPCLAB 한신대점\n승인번호 : 30009482\n"
        "단말 번호 : 7243548001\n거래 금액 : 1818 원\n부가세 : 182 원\n합계 : 2000 원"
    )

    assert v.has_item_line(raw) is False


def test_곱셈이_안_맞는_숫자열은_품목_줄이_아니다():
    # 카드번호·전화번호·바코드처럼 숫자만 이어진 줄
    assert v.has_item_line("5327 50 1234\n031 284 9032\n2026 06 22") is False


def test_합계_상태():
    assert v.sum_status({"total_amount": 100, "items": []}) == "noitem"
    assert v.sum_status({"total_amount": 1000, "items": [{"price": 1000}]}) == "match"
    assert v.sum_status({"total_amount": 1309, "items": [{"price": 1300}]}) == "close"
    assert v.sum_status({"total_amount": 5000, "items": [{"price": 1000}]}) == "mismatch"


def test_지문은_같은_영수증이면_같다():
    raw_a = "2026/11/02 21:23 계산원\n결제금액 13,960원"
    raw_b = "GS THE FRESH\n2026/11/02[월] 21:23\n13,960원"
    parsed = {"payment_date": "2026-11-02", "total_amount": 13960, "merchant_name": "GS THE FRESH"}

    assert v.make_receipt_fingerprint(parsed, raw_a) == v.make_receipt_fingerprint(parsed, raw_b)


def test_지문은_날짜나_시각이_다르면_다르다():
    # 같은 품목·같은 금액이라도 다른 날 산 영수증은 다른 영수증이다.
    parsed_a = {"payment_date": "2026-06-18", "total_amount": 13960}
    parsed_b = {"payment_date": "2026-11-02", "total_amount": 13960}
    raw = "21:23"

    assert v.make_receipt_fingerprint(parsed_a, raw) != v.make_receipt_fingerprint(parsed_b, raw)

    parsed_c = {"payment_date": "2026-11-02", "total_amount": 13960}
    assert v.make_receipt_fingerprint(parsed_c, "21:23") != v.make_receipt_fingerprint(parsed_c, "09:05")


def test_날짜나_총액이_없으면_지문_없음():
    assert v.make_receipt_fingerprint({"total_amount": 1000}, "12:00") is None
    assert v.make_receipt_fingerprint({"payment_date": "2026-01-01"}, "12:00") is None
