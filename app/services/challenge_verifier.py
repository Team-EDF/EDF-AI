"""
챌린지 사진 인증 (텀블러 + 영수증 / 저탄소 인증 마크 + 영수증).

흐름: 사진 1~3장 -> 이미지 정리(방향·크기) -> Gemini 비전이 "보이는 것"만 구조화해서 읽음
      -> 코드의 규칙(judge)이 통과/실패를 결정 -> 영수증 지문(중복 방지용) 반환.

설계 원칙
- AI는 사진에서 사실(텀블러가 보이는지, 어떤 인증 마크가 보이는지, 영수증의 가맹점·날짜·금액·할인 줄)을
  읽기만 한다. 통과 여부는 이 파일의 규칙이 정해서, 같은 읽기 결과면 항상 같은 판정이 나온다.
- 사진은 저장하지 않는다. 판정과 영수증 지문(해시)만 쓴다.
- 영수증 지문은 (날짜, 시각, 금액)의 해시다. 같은 영수증을 다시 찍어도(촬영 각도·OCR 오차가 달라도)
  날짜·시각·금액은 같아서 중복으로 잡힌다. 지문의 중복 검사는 BE가 한다 (DB 유니크).
- 완벽한 위조 방지는 아니다(남의 영수증 등). 합리적인 검사 + 중복 방지 수준이다.
  참고 근거: 영수증 거래 지문(거래 시각·금액·가맹점 해시)으로 중복/재사용을 잡는 일반적인 방식,
  EXIF는 쉽게 지울 수 있어 보조 증거로만 쓴다는 점.
"""
import hashlib
import io
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from app.services.realdata_service import CAFE_KEYWORDS

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

VERIFY_MODEL = "gemini-2.5-flash"
VERIFY_TIMEOUT_MS = 25_000          # 비전 호출은 사진 때문에 문구 생성보다 오래 걸린다 (보통 3~8초)
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 1600               # 긴 변 기준. 크게 보내도 정확도는 거의 같고 느려지기만 한다
RECEIPT_MAX_AGE_DAYS = 1            # 오늘 또는 어제 영수증만 인정

KINDS = ("TUMBLER", "LOW_CARBON")

# 영수증 할인/적립 줄에 이 말이 있으면 텀블러(개인컵)를 썼다는 영수증 증거로 본다.
# (스타벅스는 개인컵 할인 대신 "에코별" 적립을 고를 수도 있다)
TUMBLER_DISCOUNT_KEYWORDS = (
    "텀블러", "개인컵", "개인 컵", "다회용컵", "다회용 컵", "다회용기", "에코별", "에코 별", "머그", "tumbler",
)
EXTRA_CAFE_KEYWORDS = ("베이커리", "bakery", "티하우스", "tea", "도넛", "디저트", "스무디", "주스", "juice")

# 저탄소 인증으로 인정하는 마크 (BE/FE에는 이 종류 이름만 나간다)
ACCEPTED_MARKS = {
    "LOW_CARBON_PRODUCT": "탄소성적표지 저탄소제품 인증(환경부)",
    "LOW_CARBON_AGRI": "저탄소 농축산물 인증(농림축산식품부)",
    "LOW_CARBON_LIVESTOCK": "저탄소 축산물 인증(농림축산식품부)",
}
# 비슷해 보이지만 저탄소 인증이 아닌 것 (안내 문구용)
NOT_ACCEPTED_MARKS = {
    "CARBON_FOOTPRINT_ONLY": "탄소성적표지 1단계(배출량 표시만 있는 것)",
    "OTHER_ECO": "친환경·유기농·무항생제 같은 다른 인증",
}
MarkName = Literal[
    "LOW_CARBON_PRODUCT", "LOW_CARBON_AGRI", "LOW_CARBON_LIVESTOCK", "CARBON_FOOTPRINT_ONLY", "OTHER_ECO",
]


class VerifierUnavailable(Exception):
    """AI(Gemini) 호출 실패/시간 초과. 사용자는 잠시 후 다시 시도하면 된다."""


class BadImages(ValueError):
    """사진이 없거나 형식/크기가 맞지 않음."""


# ---------------------------------------------------------------- 비전이 읽어 오는 구조

class ReceiptInfo(BaseModel):
    readable: bool = Field(description="영수증이 사진에 있고, 가맹점·날짜·금액을 읽을 수 있으면 true")
    merchant_name: Optional[str] = None
    payment_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    payment_time: Optional[str] = Field(default=None, description="HH:MM (24시간)")
    total_amount: Optional[int] = Field(default=None, description="결제 금액(원, 정수)")
    item_names: list[str] = []
    discount_lines: list[str] = Field(default=[], description="할인·적립·쿠폰 줄을 영수증에 찍힌 그대로")


class VisionResult(BaseModel):
    tumbler_visible: bool = Field(description="다회용 텀블러/개인 컵이 사진에 보이면 true. 일회용 컵은 false")
    marks: list[MarkName] = []
    receipt: ReceiptInfo
    is_cafe_or_beverage_shop: bool = False
    looks_like_screen_photo: bool = False
    looks_edited: bool = False
    notes: Optional[str] = None


# ---------------------------------------------------------------- 이미지 정리

def prepare_images(images: list[bytes]) -> list[bytes]:
    """
    사진을 검사하고 JPEG로 정리한다 (EXIF 방향 반영, 긴 변 MAX_IMAGE_SIDE로 축소).
    사진이 없거나 너무 많거나 크거나 이미지가 아니면 BadImages.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not images:
        raise BadImages("사진을 1장 이상 보내 주세요.")
    if len(images) > MAX_IMAGES:
        raise BadImages(f"사진은 최대 {MAX_IMAGES}장까지 보낼 수 있어요.")

    prepared = []
    for raw in images:
        if not raw:
            raise BadImages("비어 있는 사진이 있어요.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise BadImages("사진 한 장은 8MB 이하여야 해요.")
        try:
            image = Image.open(io.BytesIO(raw))
            image.load()
        except (UnidentifiedImageError, OSError):
            raise BadImages("이미지 파일이 아니에요.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        longest = max(image.size)
        if longest > MAX_IMAGE_SIDE:
            scale = MAX_IMAGE_SIDE / longest
            image = image.resize((round(image.width * scale), round(image.height * scale)))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        prepared.append(buffer.getvalue())
    return prepared


# ---------------------------------------------------------------- Gemini 비전

_client = None
_client_lock = threading.Lock()


def llm_enabled() -> bool:
    """CHALLENGE_VERIFY_ENABLED=false 이면 사진 인증을 끈다 (AI 장애/비용 문제 때 스위치)."""
    return os.getenv("CHALLENGE_VERIFY_ENABLED", "true").strip().lower() != "false"


def _get_client():
    """Gemini 비전 클라이언트 (팀의 다른 서비스처럼 Vertex(GEMINI_PROJECT_ID) 우선, 없으면 API 키)."""
    global _client
    with _client_lock:
        if _client is None:
            from dotenv import load_dotenv
            from google import genai
            from google.genai import types

            load_dotenv()
            http_options = types.HttpOptions(timeout=VERIFY_TIMEOUT_MS)
            project_id = os.getenv("GEMINI_PROJECT_ID")
            if project_id:
                _client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location=os.getenv("GEMINI_LOCATION", "us-central1"),
                    http_options=http_options,
                )
            else:
                _client = genai.Client(http_options=http_options)
        return _client


def warm_up() -> None:
    """서버 기동 때 비전 클라이언트를 미리 만든다 (실패해도 기동·다른 기능에는 영향 없음)."""
    if not llm_enabled():
        return
    try:
        _get_client()
    except Exception as e:  # noqa: BLE001
        logger.warning("사진 인증 비전 warm-up 실패 (요청 때 다시 시도): %s: %s", type(e).__name__, str(e)[:100])


def build_prompt(kind: str, today: date) -> str:
    """사진에서 "보이는 사실"만 읽게 하는 프롬프트. 통과 여부는 묻지 않는다."""
    kind_text = {
        "TUMBLER": "이 사진들은 '카페에서 텀블러를 썼다'는 인증용이다. 텀블러와 카페 영수증이 함께 있거나 따로 찍혀 있다.",
        "LOW_CARBON": "이 사진들은 '저탄소 인증 마크가 붙은 식품을 샀다'는 인증용이다. 인증 마크가 붙은 상품과 영수증이 함께 있거나 따로 찍혀 있다.",
    }[kind]
    return f"""너는 영수증/상품 사진 판독기다. {kind_text}
사진에 실제로 보이는 것만 읽고, 보이지 않는 것은 추측하지 않는다. 오늘 날짜는 {today.isoformat()}이다.

읽을 항목:
1. tumbler_visible: 다회용 텀블러/개인 컵(뚜껑 있는 보온병형, 머그, 개인 물병 등)이 보이면 true.
   카페에서 주는 일회용 종이컵/플라스틱컵은 false.
2. marks: 사진 속 상품 포장에 보이는 인증 마크를 모두 고른다 (없으면 빈 목록).
   - LOW_CARBON_PRODUCT: 환경부 '탄소성적표지'에서 '저탄소제품' 단계를 나타내는 마크(저탄소 글자가 있는 마크)
   - LOW_CARBON_AGRI: 농림축산식품부 '저탄소 농축산물 인증' 마크
   - LOW_CARBON_LIVESTOCK: '저탄소 축산물 인증' 마크(저탄소 세 글자 표시)
   - CARBON_FOOTPRINT_ONLY: 탄소 배출량(CO2e g)만 적힌 '탄소성적표지' 1단계 마크 (저탄소 글자가 없는 것)
   - OTHER_ECO: 친환경/유기농/무농약/무항생제 같은 다른 인증 마크
   마크의 글자나 모양이 흐려서 확실하지 않으면 고르지 않는다.
3. receipt: 영수증이 사진에 있으면 읽는다.
   - readable: 가맹점명, 결제 날짜, 결제 금액을 모두 읽을 수 있으면 true, 영수증이 없거나 흐리면 false
   - payment_date는 YYYY-MM-DD, payment_time은 HH:MM(24시간), total_amount는 원 단위 정수
   - discount_lines: 영수증의 할인/적립/쿠폰 줄을 찍힌 그대로 (예: "개인컵 할인 -400")
   - 앱 화면에 띄운 전자영수증도 영수증이다.
4. is_cafe_or_beverage_shop: 가맹점이 카페/커피/음료 전문점이면 true.
5. looks_like_screen_photo: 영수증이 모니터나 휴대폰 화면을 찍은 것처럼 보이면 true (참고용).
6. looks_edited: 숫자·글자 위조나 합성의 뚜렷한 흔적(글꼴 불일치, 어색한 정렬 등)이 보이면 true. 확실할 때만 true.
7. notes: 판독하기 어려웠던 점이 있으면 짧게(없으면 null).

JSON 하나만 출력한다."""


def analyze_images(images: list[bytes], kind: str, today: date) -> VisionResult:
    """Gemini 비전으로 사진을 읽는다. 실패하면 VerifierUnavailable."""
    from google.genai import types

    try:
        parts = [types.Part.from_bytes(data=data, mime_type="image/jpeg") for data in images]
        response = _get_client().models.generate_content(
            model=VERIFY_MODEL,
            contents=[*parts, build_prompt(kind, today)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VisionResult,
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return VisionResult.model_validate_json(response.text or "")
    except Exception as e:  # 키 없음/시간 초과/형식 오류 등 모두 "AI 일시 불가"로 본다
        logger.warning("사진 인증 비전 호출 실패: %s: %s", type(e).__name__, str(e)[:150])
        raise VerifierUnavailable(f"{type(e).__name__}") from e


# ---------------------------------------------------------------- 판정 규칙

def normalize_text(text: str | None) -> str:
    """공백·특수문자를 없애고 소문자로 (가맹점 이름 비교/지문용)."""
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def make_fingerprint(receipt: ReceiptInfo) -> str | None:
    """
    영수증 지문. (날짜, 시각, 금액) 해시. 시각을 못 읽었으면 가맹점 이름 앞 4글자를 더한다.
    날짜나 금액이 없으면 None (중복 방지가 안 돼서 인증할 수 없다).
    """
    if not receipt.payment_date or not receipt.total_amount:
        return None
    parts = [receipt.payment_date, (receipt.payment_time or "").strip(), str(int(receipt.total_amount))]
    if not receipt.payment_time:
        parts.append(normalize_text(receipt.merchant_name)[:4])
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:40]


def is_cafe(receipt: ReceiptInfo, flag: bool) -> bool:
    """카페/음료점 여부: 비전의 판단 또는 가맹점 이름 키워드."""
    name = (receipt.merchant_name or "").lower()
    keywords = tuple(CAFE_KEYWORDS) + EXTRA_CAFE_KEYWORDS
    return flag or any(keyword in name for keyword in keywords)


def has_tumbler_discount(receipt: ReceiptInfo) -> bool:
    """할인/적립 줄에 텀블러(개인컵) 관련 문구가 있는지."""
    lines = " ".join(receipt.discount_lines or []).lower()
    return any(keyword in lines for keyword in TUMBLER_DISCOUNT_KEYWORDS)


def _result(passed: bool, code: str, message: str, kind: str, vision: VisionResult | None = None, **extra) -> dict:
    receipt = vision.receipt if vision else None
    return {
        "passed": passed,
        "code": code,
        "message": message,
        "kind": kind,
        "receipt": {
            "merchant_name": receipt.merchant_name,
            "payment_date": receipt.payment_date,
            "payment_time": receipt.payment_time,
            "total_amount": receipt.total_amount,
            "fingerprint": make_fingerprint(receipt),
        } if receipt else None,
        "evidence": extra.pop("evidence", None),
        "marks": [m for m in (vision.marks if vision else []) if m in ACCEPTED_MARKS],
        **extra,
    }


def judge(kind: str, vision: VisionResult, now: datetime | None = None) -> dict:
    """
    읽은 결과로 통과/실패를 정한다. 같은 입력이면 항상 같은 결과.
    반환: {passed, code, message, kind, receipt{...,fingerprint}, evidence, marks}
    실패 코드: RECEIPT_UNREADABLE, RECEIPT_DATE, RECEIPT_TOO_OLD, RECEIPT_EDITED, NOT_CAFE, NO_TUMBLER, NO_MARK
    """
    if kind not in KINDS:
        raise ValueError(f"알 수 없는 인증 종류: {kind}")
    now = now or datetime.now(KST)
    today = now.astimezone(KST).date()
    receipt = vision.receipt

    # 1) 영수증을 읽을 수 있는가 (날짜와 금액이 있어야 중복 방지 지문을 만들 수 있다)
    fingerprint = make_fingerprint(receipt)
    if not receipt.readable or fingerprint is None:
        return _result(False, "RECEIPT_UNREADABLE",
                       "영수증의 가맹점·날짜·금액이 잘 보이게 다시 찍어 주세요.", kind, vision)

    # 2) 영수증 날짜: 오늘 또는 어제
    try:
        paid_on = date.fromisoformat(receipt.payment_date)
    except ValueError:
        return _result(False, "RECEIPT_DATE", "영수증 날짜를 읽지 못했어요. 날짜가 잘 보이게 다시 찍어 주세요.", kind, vision)
    if paid_on > today or paid_on < today - timedelta(days=RECEIPT_MAX_AGE_DAYS):
        return _result(False, "RECEIPT_TOO_OLD",
                       "오늘이나 어제 결제한 영수증만 인증할 수 있어요.", kind, vision)

    # 3) 편집 흔적이 뚜렷하면 거절
    if vision.looks_edited:
        return _result(False, "RECEIPT_EDITED", "편집된 것으로 보이는 영수증은 인증할 수 없어요.", kind, vision)

    # 4) 종류별 조건
    if kind == "TUMBLER":
        if not is_cafe(receipt, vision.is_cafe_or_beverage_shop):
            return _result(False, "NOT_CAFE", "카페(음료점) 영수증이 아니에요. 카페 영수증과 함께 찍어 주세요.", kind, vision)
        discount = has_tumbler_discount(receipt)
        if not (vision.tumbler_visible or discount):
            return _result(False, "NO_TUMBLER",
                           "텀블러(다회용 컵)가 보이도록 찍어 주세요. 영수증에 개인컵 할인이 찍혀 있어도 인증돼요.", kind, vision)
        if vision.tumbler_visible and discount:
            evidence, how = "PHOTO_AND_RECEIPT", "텀블러 사진과 영수증의 개인컵 할인 문구가 모두 확인됐어요."
        elif vision.tumbler_visible:
            evidence, how = "PHOTO", "텀블러와 카페 영수증이 확인됐어요."
        else:
            evidence, how = "RECEIPT_DISCOUNT", "영수증의 개인컵 할인 문구로 확인됐어요."
        return _result(True, "OK", f"인증 완료! {how}", kind, vision, evidence=evidence)

    # LOW_CARBON
    accepted = [m for m in vision.marks if m in ACCEPTED_MARKS]
    if not accepted:
        others = [NOT_ACCEPTED_MARKS[m] for m in vision.marks if m in NOT_ACCEPTED_MARKS]
        hint = f" (보이는 마크: {', '.join(others)} — 저탄소 인증이 아니에요)" if others else ""
        return _result(False, "NO_MARK", f"저탄소 인증 마크가 선명하게 보이도록 상품을 찍어 주세요.{hint}", kind, vision)
    labels = ", ".join(ACCEPTED_MARKS[m] for m in accepted)
    return _result(True, "OK", f"인증 완료! {labels} 마크와 영수증이 확인됐어요.", kind, vision, evidence="MARK_AND_RECEIPT")


# ---------------------------------------------------------------- 메인

def verify_challenge(
    kind: str,
    images: list[bytes],
    now: datetime | None = None,
    analyzer: Callable[[list[bytes], str, date], VisionResult] | None = None,
) -> dict:
    """
    사진으로 챌린지를 인증한다.
    analyzer는 테스트에서 가짜 비전을 끼우기 위한 자리다 (기본은 Gemini).
    예외: BadImages(사진 문제), VerifierUnavailable(AI 일시 불가/비활성).
    """
    if kind not in KINDS:
        raise BadImages(f"알 수 없는 인증 종류예요: {kind}")
    if not llm_enabled() and analyzer is None:
        raise VerifierUnavailable("DISABLED")
    prepared = prepare_images(images)
    now = now or datetime.now(KST)
    vision = (analyzer or analyze_images)(prepared, kind, now.astimezone(KST).date())
    return judge(kind, vision, now)
