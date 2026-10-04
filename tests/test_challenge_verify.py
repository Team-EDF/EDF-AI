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
    ReceiptInfo,
    VerifierUnavailable,
    VisionResult,
    judge,
    make_fingerprint,
    prepare_images,
    verify_challenge,
)

NOW = datetime(2026, 10, 4, 14, 0, tzinfo=cv.KST)


def _receipt(**overrides) -> ReceiptInfo:
    base = dict(
        readable=True, merchant_name="스타벅스 강남점", payment_date="2026-10-04", payment_time="13:41",
        total_amount=4500, item_names=["아메리카노"], discount_lines=[],
    )
    base.update(overrides)
    return ReceiptInfo(**base)


def _vision(receipt=None, **overrides) -> VisionResult:
    base = dict(tumbler_visible=True, marks=[], receipt=receipt or _receipt(), is_cafe_or_beverage_shop=True)
    base.update(overrides)
    return VisionResult(**base)


def _jpeg(size=(400, 300), color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


# ---------------------------------------------------------------- 텀블러

def test_tumbler_passes_with_photo_and_cafe_receipt():
    result = judge("TUMBLER", _vision(), NOW)
    assert result["passed"] and result["code"] == "OK" and result["evidence"] == "PHOTO"
    assert result["receipt"]["fingerprint"]


def test_tumbler_passes_with_receipt_discount_even_without_photo():
    vision = _vision(_receipt(discount_lines=["개인컵 할인 -400"]), tumbler_visible=False)
    result = judge("TUMBLER", vision, NOW)
    assert result["passed"] and result["evidence"] == "RECEIPT_DISCOUNT"


def test_tumbler_photo_plus_discount_is_strongest_evidence():
    vision = _vision(_receipt(discount_lines=["에코별 적립 1"]))
    assert judge("TUMBLER", vision, NOW)["evidence"] == "PHOTO_AND_RECEIPT"


def test_tumbler_fails_without_tumbler_or_discount():
    result = judge("TUMBLER", _vision(tumbler_visible=False), NOW)
    assert not result["passed"] and result["code"] == "NO_TUMBLER"


def test_tumbler_requires_cafe_receipt():
    vision = _vision(_receipt(merchant_name="이마트 역삼점"), is_cafe_or_beverage_shop=False)
    result = judge("TUMBLER", vision, NOW)
    assert not result["passed"] and result["code"] == "NOT_CAFE"


def test_cafe_is_recognized_by_name_even_if_vision_flag_is_false():
    vision = _vision(_receipt(merchant_name="메가커피 선릉점"), is_cafe_or_beverage_shop=False)
    assert judge("TUMBLER", vision, NOW)["passed"]


# ---------------------------------------------------------------- 영수증 공통 규칙

def test_receipt_must_be_readable_with_date_and_amount():
    for receipt in (_receipt(readable=False), _receipt(total_amount=None), _receipt(payment_date=None)):
        result = judge("TUMBLER", _vision(receipt), NOW)
        assert not result["passed"] and result["code"] == "RECEIPT_UNREADABLE"
        assert result["receipt"]["fingerprint"] is None or receipt.readable is False


def test_receipt_date_must_be_today_or_yesterday():
    assert judge("TUMBLER", _vision(_receipt(payment_date="2026-10-03")), NOW)["passed"]        # 어제
    for old in ("2026-10-02", "2026-09-01", "2026-10-05"):                                      # 그제 / 오래됨 / 미래
        result = judge("TUMBLER", _vision(_receipt(payment_date=old)), NOW)
        assert not result["passed"] and result["code"] == "RECEIPT_TOO_OLD", old


def test_bad_date_text_is_reported():
    result = judge("TUMBLER", _vision(_receipt(payment_date="오늘")), NOW)
    assert not result["passed"] and result["code"] == "RECEIPT_DATE"


def test_edited_receipt_is_rejected_but_screen_photo_is_fine():
    assert judge("TUMBLER", _vision(looks_edited=True), NOW)["code"] == "RECEIPT_EDITED"
    # 앱 전자영수증은 화면을 찍은 사진이라 거절하면 안 된다
    assert judge("TUMBLER", _vision(looks_like_screen_photo=True), NOW)["passed"]


def test_date_uses_kst():
    # UTC 2026-10-03 16:00 = KST 2026-10-04 01:00 -> KST 기준으로 오늘/어제를 센다
    utc_now = datetime(2026, 10, 3, 16, 0, tzinfo=cv.timezone.utc)
    assert judge("TUMBLER", _vision(_receipt(payment_date="2026-10-04")), utc_now)["passed"]
    assert not judge("TUMBLER", _vision(_receipt(payment_date="2026-10-02")), utc_now)["passed"]


# ---------------------------------------------------------------- 저탄소 마크

def test_low_carbon_passes_with_accepted_mark_and_receipt():
    vision = _vision(_receipt(merchant_name="이마트 역삼점"), tumbler_visible=False, marks=["LOW_CARBON_AGRI"],
                     is_cafe_or_beverage_shop=False)
    result = judge("LOW_CARBON", vision, NOW)
    assert result["passed"] and result["evidence"] == "MARK_AND_RECEIPT" and result["marks"] == ["LOW_CARBON_AGRI"]


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
    fake_ok = lambda images, kind, today: _vision(_receipt(payment_date=today.isoformat()))
    with mock.patch.object(cv, "analyze_images", fake_ok):
        r = _client().post("/api/challenges/verify", data={"kind": "TUMBLER"},
                           files=[("images", ("a.jpg", _jpeg(), "image/jpeg")), ("images", ("b.jpg", _jpeg(), "image/jpeg"))])
    body = r.json()
    assert r.status_code == 200 and body["passed"] is True and body["receipt"]["fingerprint"]

    fake_no = lambda images, kind, today: _vision(tumbler_visible=False, receipt=_receipt(payment_date=today.isoformat()))
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
