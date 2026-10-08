"""
챌린지 사진 인증 규칙 테스트 (Gemini 호출 없음: 비전 결과를 직접 만들어 판정 규칙만 검사한다).

실행: pytest tests/test_challenge_verify.py
(pytest가 없으면: python -c "import tests.test_challenge_verify as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]")
"""
import io
from datetime import date, datetime
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import challenges as challenges_route
from app.services import challenge_verifier as cv
from app.services.challenge_verifier import (
    BadImages,
    ImageFinding,
    ReceiptInfo,
    VerifierUnavailable,
    VisionResult,
    judge,
    make_fingerprint,
    prepare_images,
    verify_challenge,
)

NOW = datetime(2026, 10, 4, 14, 0, tzinfo=cv.KST)      # 결제 시각 13:41 영수증이 "방금" 영수증이 되는 기준 시각


def _receipt(**overrides) -> ReceiptInfo:
    base = dict(
        readable=True, merchant_name="스타벅스 강남점", payment_date="2026-10-04", payment_time="13:41",
        total_amount=4500, item_names=["아메리카노"], discount_lines=[],
    )
    base.update(overrides)
    return ReceiptInfo(**base)


def _finding(receipt=None, **overrides) -> ImageFinding:
    """사진 한 장에서 읽은 내용 (기본: 텀블러가 보이고 카페 영수증도 같은 사진에 있음)."""
    base = dict(tumbler_visible=True, marks=[], receipt=receipt or _receipt(), is_cafe_or_beverage_shop=True)
    base.update(overrides)
    return ImageFinding(**base)


def _vision(receipt=None, **overrides) -> VisionResult:
    """사진 한 장짜리 읽기 결과."""
    return VisionResult(images=[_finding(receipt, **overrides)])


def _no_receipt() -> ReceiptInfo:
    return ReceiptInfo(readable=False)


def _jpeg(size=(400, 300), color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


# ---------------------------------------------------------------- 텀블러

def test_tumbler_passes_with_photo_and_cafe_receipt_in_same_photo():
    result = judge("TUMBLER", _vision(), NOW)
    assert result["passed"] and result["code"] == "OK" and result["evidence"] == "PHOTO"
    assert result["receipt"]["fingerprint"]


def test_tumbler_passes_with_receipt_discount_even_without_tumbler_in_photo():
    vision = _vision(_receipt(discount_lines=["개인컵 할인 -400"]), tumbler_visible=False)
    result = judge("TUMBLER", vision, NOW)
    assert result["passed"] and result["evidence"] == "RECEIPT_DISCOUNT"


def test_discount_only_path_can_be_switched_off():
    vision = _vision(_receipt(discount_lines=["개인컵 할인 -400"]), tumbler_visible=False)
    with mock.patch.object(cv, "ALLOW_RECEIPT_DISCOUNT_ONLY", False):
        result = judge("TUMBLER", vision, NOW)
    assert not result["passed"] and result["code"] == "NO_TUMBLER"


def test_tumbler_photo_plus_discount_is_strongest_evidence():
    vision = _vision(_receipt(discount_lines=["에코별 적립 1"]))
    assert judge("TUMBLER", vision, NOW)["evidence"] == "PHOTO_AND_RECEIPT"


def test_tumbler_fails_without_tumbler_or_discount():
    result = judge("TUMBLER", _vision(tumbler_visible=False), NOW)
    assert not result["passed"] and result["code"] == "NO_TUMBLER"
    assert "한 사진에" in result["message"]


def test_tumbler_requires_cafe_receipt():
    vision = _vision(_receipt(merchant_name="이마트 역삼점"), is_cafe_or_beverage_shop=False)
    result = judge("TUMBLER", vision, NOW)
    assert not result["passed"] and result["code"] == "NOT_CAFE"


def test_cafe_is_recognized_by_name_even_if_vision_flag_is_false():
    vision = _vision(_receipt(merchant_name="메가커피 선릉점"), is_cafe_or_beverage_shop=False)
    assert judge("TUMBLER", vision, NOW)["passed"]


# ---------------------------------------------------------------- 같은 사진 안에서만 인정 (악용 방지)

def test_tumbler_and_receipt_in_different_photos_is_rejected():
    """집에서 찍은 텀블러 + 일회용 컵으로 산 영수증을 따로 내는 경우."""
    tumbler_only = _finding(_no_receipt(), tumbler_visible=True)
    receipt_only = _finding(_receipt(), tumbler_visible=False)
    result = judge("TUMBLER", VisionResult(images=[tumbler_only, receipt_only]), NOW)
    assert not result["passed"] and result["code"] == "SEPARATE_PHOTOS"
    assert "한 사진 안에" in result["message"]


def test_separate_photos_with_receipt_first_is_also_rejected():
    result = judge("TUMBLER", VisionResult(images=[_finding(_receipt(), tumbler_visible=False),
                                                   _finding(_no_receipt(), tumbler_visible=True)]), NOW)
    assert not result["passed"] and result["code"] == "SEPARATE_PHOTOS"


def test_separate_photos_still_pass_when_the_receipt_has_a_discount_line():
    # 영수증 자체에 개인컵 할인이 찍혀 있으면(매장 기록) 따로 찍어도 영수증만으로 인정된다
    discount_receipt = _finding(_receipt(discount_lines=["개인컵 할인 -400"]), tumbler_visible=False)
    result = judge("TUMBLER", VisionResult(images=[_finding(_no_receipt()), discount_receipt]), NOW)
    assert result["passed"] and result["evidence"] == "RECEIPT_DISCOUNT"


def test_a_second_photo_can_rescue_a_blurry_first_photo():
    blurry = _finding(_no_receipt(), tumbler_visible=True)
    clear = _finding(_receipt(), tumbler_visible=True)
    result = judge("TUMBLER", VisionResult(images=[blurry, clear]), NOW)
    assert result["passed"] and result["receipt"]["fingerprint"] == make_fingerprint(_receipt())


def test_mixed_photos_never_combine_conditions():
    # 사진1: 텀블러 + 오래된 영수증, 사진2: 텀블러 없음 + 최근 영수증 -> 어느 한 사진도 모든 조건을 만족하지 못한다
    old = _finding(_receipt(payment_date="2026-10-01"), tumbler_visible=True)
    recent_no_tumbler = _finding(_receipt(payment_time="13:50"), tumbler_visible=False)
    result = judge("TUMBLER", VisionResult(images=[old, recent_no_tumbler]), NOW)
    assert not result["passed"]


def test_no_images_read_is_unreadable():
    result = judge("TUMBLER", VisionResult(images=[]), NOW)
    assert not result["passed"] and result["code"] == "RECEIPT_UNREADABLE"


# ---------------------------------------------------------------- 영수증 공통 규칙

def test_receipt_must_be_readable_with_date_and_amount():
    for receipt in (_receipt(readable=False), _receipt(total_amount=None), _receipt(payment_date=None)):
        result = judge("TUMBLER", _vision(receipt), NOW)
        assert not result["passed"] and result["code"] == "RECEIPT_UNREADABLE"


def test_receipt_must_be_within_24_hours_when_time_is_known():
    # NOW = 2026-10-04 14:00
    assert judge("TUMBLER", _vision(_receipt(payment_date="2026-10-03", payment_time="15:00")), NOW)["passed"]      # 23시간 전
    assert judge("TUMBLER", _vision(_receipt(payment_date="2026-10-04", payment_time="09:00")), NOW)["passed"]      # 5시간 전
    for date_text, time_text in (("2026-10-03", "13:41"),     # 24시간 19분 전
                                 ("2026-10-02", "20:00"),     # 이틀 가까이
                                 ("2026-10-04", "14:30")):    # 30분 뒤 (미래)
        result = judge("TUMBLER", _vision(_receipt(payment_date=date_text, payment_time=time_text)), NOW)
        assert not result["passed"] and result["code"] == "RECEIPT_TOO_OLD", (date_text, time_text)
    # 15분까지의 기기 시계 오차는 허용
    assert judge("TUMBLER", _vision(_receipt(payment_date="2026-10-04", payment_time="14:10")), NOW)["passed"]


def test_receipt_date_only_falls_back_to_today_or_yesterday():
    assert judge("TUMBLER", _vision(_receipt(payment_time=None, payment_date="2026-10-03")), NOW)["passed"]
    assert judge("TUMBLER", _vision(_receipt(payment_time=None)), NOW)["passed"]
    for old in ("2026-10-02", "2026-09-01", "2026-10-05"):
        result = judge("TUMBLER", _vision(_receipt(payment_time=None, payment_date=old)), NOW)
        assert not result["passed"] and result["code"] == "RECEIPT_TOO_OLD", old


def test_bad_date_text_is_reported():
    result = judge("TUMBLER", _vision(_receipt(payment_date="오늘")), NOW)
    assert not result["passed"] and result["code"] == "RECEIPT_DATE"


def test_edited_receipt_is_rejected_but_screen_photo_is_fine():
    assert judge("TUMBLER", _vision(looks_edited=True), NOW)["code"] == "RECEIPT_EDITED"
    # 앱 전자영수증은 화면을 찍은 사진이라 거절하면 안 된다
    assert judge("TUMBLER", _vision(looks_like_screen_photo=True), NOW)["passed"]


def test_time_uses_kst():
    # UTC 2026-10-04 05:00 = KST 14:00
    utc_now = datetime(2026, 10, 4, 5, 0, tzinfo=cv.timezone.utc)
    assert judge("TUMBLER", _vision(_receipt(payment_time="13:41")), utc_now)["passed"]
    assert not judge("TUMBLER", _vision(_receipt(payment_time="15:30")), utc_now)["passed"]      # KST로는 1시간 반 뒤


# ---------------------------------------------------------------- 저탄소 마크

def test_low_carbon_passes_with_accepted_mark_and_receipt_in_same_photo():
    vision = _vision(_receipt(merchant_name="이마트 역삼점"), tumbler_visible=False, marks=["LOW_CARBON_AGRI"],
                     is_cafe_or_beverage_shop=False)
    result = judge("LOW_CARBON", vision, NOW)
    assert result["passed"] and result["evidence"] == "MARK_AND_RECEIPT" and result["marks"] == ["LOW_CARBON_AGRI"]


def test_low_carbon_mark_and_receipt_in_different_photos_is_rejected():
    mark_only = _finding(_no_receipt(), tumbler_visible=False, marks=["LOW_CARBON_PRODUCT"])
    receipt_only = _finding(_receipt(merchant_name="이마트 역삼점"), tumbler_visible=False)
    result = judge("LOW_CARBON", VisionResult(images=[mark_only, receipt_only]), NOW)
    assert not result["passed"] and result["code"] == "SEPARATE_PHOTOS"


def test_low_carbon_rejects_other_marks_with_explanation():
    for marks in ([], ["CARBON_FOOTPRINT_ONLY"], ["OTHER_ECO"]):
        result = judge("LOW_CARBON", _vision(marks=marks), NOW)
        assert not result["passed"] and result["code"] == "NO_MARK"
    hint = judge("LOW_CARBON", _vision(marks=["CARBON_FOOTPRINT_ONLY"]), NOW)["message"]
    assert "저탄소 인증이 아니에요" in hint


def test_low_carbon_still_needs_a_valid_receipt():
    result = judge("LOW_CARBON", _vision(_receipt(readable=False), marks=["LOW_CARBON_PRODUCT"]), NOW)
    assert not result["passed"] and result["code"] == "RECEIPT_UNREADABLE"


# ---------------------------------------------------------------- 영수증 지문 (중복 방지)

def test_fingerprint_is_stable_across_ocr_noise_in_merchant_and_items():
    a = make_fingerprint(_receipt(merchant_name="스타벅스 강남점", item_names=["아메리카노"]))
    b = make_fingerprint(_receipt(merchant_name="STARBUCKS 강남", item_names=["Americano"], discount_lines=["x"]))
    assert a == b                               # 시각이 있으면 가맹점 이름이 달라도 같은 영수증


def test_fingerprint_differs_for_different_receipts():
    base = make_fingerprint(_receipt())
    assert base != make_fingerprint(_receipt(total_amount=4600))
    assert base != make_fingerprint(_receipt(payment_time="13:42"))
    assert base != make_fingerprint(_receipt(payment_date="2026-10-03"))


def test_fingerprint_without_time_uses_merchant_prefix():
    one = make_fingerprint(_receipt(payment_time=None, merchant_name="메가커피 선릉점"))
    two = make_fingerprint(_receipt(payment_time=None, merchant_name="메가 커피 선릉"))
    other = make_fingerprint(_receipt(payment_time=None, merchant_name="컴포즈커피 선릉점"))
    assert one == two and one != other


def test_fingerprint_needs_date_and_amount():
    assert make_fingerprint(_receipt(total_amount=None)) is None
    assert make_fingerprint(_receipt(payment_date=None)) is None


# ---------------------------------------------------------------- 이미지 정리

def test_prepare_images_shrinks_and_reencodes():
    prepared = prepare_images([_jpeg((3200, 2400)), _jpeg((500, 500))])
    from PIL import Image

    big = Image.open(io.BytesIO(prepared[0]))
    assert max(big.size) == cv.MAX_IMAGE_SIDE and big.format == "JPEG"
    assert Image.open(io.BytesIO(prepared[1])).size == (500, 500)


def test_prepare_images_applies_exif_rotation():
    from PIL import Image

    image = Image.new("RGB", (400, 200), (0, 128, 0))
    exif = Image.Exif()
    exif[0x0112] = 6                            # 시계 방향 90도 회전 필요
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    rotated = Image.open(io.BytesIO(prepare_images([buffer.getvalue()])[0]))
    assert rotated.size == (200, 400)


def test_prepare_images_rejects_bad_input():
    for bad in ([], [b""], [b"not an image"], [_jpeg()] * (cv.MAX_IMAGES + 1)):
        try:
            prepare_images(bad)
        except BadImages:
            continue
        raise AssertionError(f"BadImages가 나와야 함: {bad!r:.40}")
    try:
        prepare_images([b"x" * (cv.MAX_IMAGE_BYTES + 1)])
    except BadImages:
        pass
    else:
        raise AssertionError("너무 큰 사진은 거절해야 함")


# ---------------------------------------------------------------- 프롬프트

def test_prompt_asks_for_per_photo_reading():
    prompt = cv.build_prompt("TUMBLER", date(2026, 10, 4), 2)
    assert "사진 2장" in prompt and "사진마다 따로" in prompt and "합치지 않는다" in prompt


# ---------------------------------------------------------------- 전체 흐름 / API

def test_verify_challenge_with_fake_analyzer_gets_kst_today():
    seen = {}

    def fake(images, kind, today: date):
        seen.update(count=len(images), kind=kind, today=today)
        return _vision()

    result = verify_challenge("TUMBLER", [_jpeg(), _jpeg()], now=NOW, analyzer=fake)
    assert result["passed"] and seen == {"count": 2, "kind": "TUMBLER", "today": date(2026, 10, 4)}


def test_verify_challenge_unknown_kind_and_disabled_switch():
    try:
        verify_challenge("PHONE", [_jpeg()], analyzer=lambda *a: _vision())
    except BadImages:
        pass
    else:
        raise AssertionError("알 수 없는 종류는 BadImages")
    with mock.patch.dict("os.environ", {"CHALLENGE_VERIFY_ENABLED": "false"}):
        try:
            verify_challenge("TUMBLER", [_jpeg()])
        except VerifierUnavailable:
            pass
        else:
            raise AssertionError("스위치를 끄면 VerifierUnavailable")


def _client():
    app = FastAPI()
    app.include_router(challenges_route.router, prefix="/api")
    return TestClient(app)


def test_verify_endpoint_pass_and_fail_are_200():
    # 결제 시각을 모르는 영수증(날짜만 오늘)이면 실제 현재 시각과 상관없이 통과 규칙만 본다
    fake_ok = lambda images, kind, today: _vision(_receipt(payment_date=today.isoformat(), payment_time=None))
    with mock.patch.object(cv, "analyze_images", fake_ok):
        r = _client().post("/api/challenges/verify", data={"kind": "TUMBLER"},
                           files=[("images", ("a.jpg", _jpeg(), "image/jpeg")), ("images", ("b.jpg", _jpeg(), "image/jpeg"))])
    body = r.json()
    assert r.status_code == 200 and body["passed"] is True and body["receipt"]["fingerprint"]

    fake_no = lambda images, kind, today: _vision(
        _receipt(payment_date=today.isoformat(), payment_time=None), tumbler_visible=False)
    with mock.patch.object(cv, "analyze_images", fake_no):
        r = _client().post("/api/challenges/verify", data={"kind": "TUMBLER"}, files=[("images", ("a.jpg", _jpeg(), "image/jpeg"))])
    assert r.status_code == 200 and r.json()["passed"] is False and r.json()["code"] == "NO_TUMBLER"


def test_verify_endpoint_errors():
    client = _client()
    r = client.post("/api/challenges/verify", data={"kind": "TUMBLER"}, files=[("images", ("a.txt", b"hello", "text/plain"))])
    assert r.status_code == 400 and "이미지" in r.json()["detail"]
    r = client.post("/api/challenges/verify", data={"kind": "NOPE"}, files=[("images", ("a.jpg", _jpeg(), "image/jpeg"))])
    assert r.status_code == 400
    with mock.patch.object(cv, "analyze_images", side_effect=VerifierUnavailable("TimeoutError")):
        r = client.post("/api/challenges/verify", data={"kind": "TUMBLER"}, files=[("images", ("a.jpg", _jpeg(), "image/jpeg"))])
    assert r.status_code == 503
