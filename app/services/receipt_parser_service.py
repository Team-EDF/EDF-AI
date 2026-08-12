import re
from typing import Dict, List, Optional


# ── 컴파일된 패턴 ──────────────────────────────────────────────────────────────

# 주소 판별
_ADDRESS_RE = re.compile(
    r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
    r"|([가-힣]{2,}(시|도|구|군)\s?[가-힣]+(시|구|군|읍|면|동|로|길))"
    r"|(로\s?\d+|길\s?\d+|번지|번길)"
    r"|\d+호\s*[\(（\[]"
    r"|\d+호\s*[가-힣]+동"
)

# 날짜
_DATE_RE = re.compile(
    r"(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})"
    r"|(\d{2}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})"
    r"|(\d{4}년\s*\d{1,2}월\s*\d{1,2}일)"
)

# 가맹점명 레이블
_MERCHANT_LABEL_RE = re.compile(
    r"^(상호|가맹점명?|점포명?|매장명?|사업체명)\s*[:：]\s*(.+)"
)

# 수량 × 단가 = 총액
_QTY_PRICE_RE = re.compile(
    r"(\d+)\s*[×xX✕*]\s*[\d,]+\s*[=＝]\s*([\d,]+)"
)

# 할인/차감 금액
_DISCOUNT_RE = re.compile(
    r"[-−＿]\s*[\d,]+"
)

# 가맹점명 후보에서 제외
_MERCHANT_SKIP = {
    "영수증",
    "receipt",
    "tax invoice",
    "간이",
    "세금계산서",
    "invoice",
    "주문",
    "받아가세요",

    # 추가
    "상품명",
    "품명",
    "품목명",
    "단가",
    "수량",
    "금액",
    "결제대상금액",
}

# 소계 / 중간합계
_SUBTOTAL_KEYWORDS = {
    "소계",
    "소합계",
    "중간합계",
    "sub total",
    "subtotal",
    "소 계",
    "합계수량",
    "수량합계",
}

# 총액 우선 키워드
_TOTAL_PRIORITY = [
    "결제대상금액",       # 추가
    "결제 대상 금액",     # 추가
    "결제금액",
    "결제 금액",
    "받을금액",
    "받을 금액",
    "받은금액",
    "받은 금액",
    "총금액",
    "총 금액",
    "총액",
    "실결제",
    "실결제금액",
    "청구금액",
    "합계금액",
    "판매총액",
    "판매 총액",
]

_TOTAL_SECONDARY = [
    "합계",
    "합게",
    "총매출액",
    "매출액",
    "total",
    "금액:",
    "계:",
]

# 카드번호 / 거래번호
_MASKED_NUMBER_RE = re.compile(
    r"\*{2,}"
    r"|\d{4}[-]\d{2,}"
    r"|\d{8,}"
    r"|\d{2,4}[-]\d{3,4}[-]\d{4}"
)


# ── 상품으로 절대 취급하면 안 되는 이름 ───────────────────────────────────────

_NON_ITEM_NAMES = {
    "결제대상금액",
    "결제금액",
    "결제금",
    "승인금액",
    "청구금액",

    "합계",
    "총합계",
    "총액",
    "총금액",
    "판매총액",
    "판매금액",
    "매출액",

    "소계",
    "소합계",
    "중간합계",

    "받을금액",
    "받은금액",

    "공급금액",
    "공급가액",

    "과세금액",
    "과세물품",
    "면세금액",
    "면세물품",

    "부가세",
    "부가가치세",
    "세액",

    "신용",
    "현금",
    "카드",
    "일시불",
    "할부",

    "거스름돈",
    "잔액",

    "상품명",
    "품명",
    "품목명",

    "단가",
    "수량",
    "금액",

    "단가수량",
    "수량금액",
    "단가수량금액",

    "no",
    "no.",
}


# ── 텍스트 전처리 ──────────────────────────────────────────────────────────────

def clean_ocr_text(
    raw_text: str,
) -> str:

    lines = raw_text.splitlines()

    cleaned = []

    for line in lines:

        line = line.strip()

        line = re.sub(
            r"\d{3}-\d{2}-\d{5}",
            "",
            line,
        ).strip()

        line = re.sub(
            r"\s+",
            " ",
            line,
        )

        if line:
            cleaned.append(
                line
            )

    return "\n".join(
        cleaned
    )


# ── 가맹점명 추출 ──────────────────────────────────────────────────────────────

def extract_merchant_name(
    text: str,
) -> Optional[str]:
    """
    레이블 우선 →
    상단 휴리스틱 순서로 가맹점명 추출.
    """

    lines = text.splitlines()

    # 1. 명시적 레이블
    for line in lines[:20]:

        m = _MERCHANT_LABEL_RE.match(
            line.strip()
        )

        if m:

            name = (
                m.group(2)
                .strip()
            )

            name = re.sub(
                r"[^\w\s가-힣]",
                "",
                name,
            ).strip()

            if (
                len(name) >= 2
                and is_valid_item_name(name)
            ):
                return name

    korean_candidate = None
    english_candidate = None

    # 2. 영수증 상단 검색
    for line in lines[:8]:

        line = line.strip()

        if (
            not line
            or len(line) < 2
            or len(line) > 40
        ):
            continue

        if re.fullmatch(
            r"[\d\s\-/.,:()]+",
            line,
        ):
            continue

        if is_noise_line(
            line
        ):
            continue

        if _DATE_RE.search(
            line
        ):
            continue

        if re.search(
            r"\d{2,4}[-–]\d{3,4}(?!\d)",
            line,
        ):
            continue

        if _ADDRESS_RE.search(
            line
        ):
            continue

        if any(
            kw in line.lower()
            for kw in _MERCHANT_SKIP
        ):
            continue

        if not re.search(
            r"[가-힣a-zA-Z]",
            line,
        ):
            continue

        # 가게명 / 대표자명
        if "/" in line or "／" in line:

            candidate = re.split(
                r"[/／]",
                line,
            )[0].strip()

            cname = re.sub(
                r"[^\w\s가-힣]",
                "",
                candidate,
            ).strip()

            if (
                len(cname) >= 2
                and re.search(
                    r"[가-힣a-zA-Z]",
                    cname,
                )
            ):
                return cname

            continue

        name = re.sub(
            r"[^\w\s가-힣]",
            "",
            line,
        ).strip()

        if re.search(
            r"^\d[\d,]*원$",
            name,
        ):
            continue

        if len(name) < 2:
            continue

        if len(
            name.split()
        ) >= 4:
            continue

        name_no_space = (
            name.replace(
                " ",
                "",
            )
            .lower()
        )

        if (
            name_no_space
            in _NON_ITEM_NAMES
        ):
            continue

        if any(
            kw.replace(" ", "")
            in name_no_space
            for kw in (
                _TOTAL_PRIORITY
                + _TOTAL_SECONDARY
            )
        ):
            continue

        if re.search(
            r"[가-힣]",
            name,
        ):

            if korean_candidate is None:
                korean_candidate = name

        else:

            if (
                english_candidate is None
                and re.search(
                    r"[a-z]",
                    name,
                )
            ):
                english_candidate = name

    # 상단에서 찾지 못했으면 조금 더 아래 검색
    if korean_candidate is None:

        for line in lines[8:25]:

            line = line.strip()

            if (
                not line
                or len(line) < 2
                or len(line) > 40
            ):
                continue

            if re.fullmatch(
                r"[\d\s\-/.,:()]+",
                line,
            ):
                continue

            if is_noise_line(
                line
            ):
                continue

            if _DATE_RE.search(
                line
            ):
                continue

            if re.search(
                r"\d{2,4}[-–]\d{3,4}(?!\d)",
                line,
            ):
                continue

            if _ADDRESS_RE.search(
                line
            ):
                continue

            if any(
                kw in line.lower()
                for kw in _MERCHANT_SKIP
            ):
                continue

            if not re.search(
                r"[가-힣a-zA-Z]",
                line,
            ):
                continue

            if "/" in line or "／" in line:

                candidate = re.split(
                    r"[/／]",
                    line,
                )[0].strip()

                cname = re.sub(
                    r"[^\w\s가-힣]",
                    "",
                    candidate,
                ).strip()

                if (
                    len(cname) >= 2
                    and re.search(
                        r"[가-힣a-zA-Z]",
                        cname,
                    )
                ):
                    korean_candidate = cname
                    break

                continue

            name = re.sub(
                r"[^\w\s가-힣]",
                "",
                line,
            ).strip()

            if re.search(
                r"^\d[\d,]*원$",
                name,
            ):
                continue

            if len(name) < 2:
                continue

            if len(
                name.split()
            ) >= 4:
                continue

            name_no_space = (
                name.replace(
                    " ",
                    "",
                )
                .lower()
            )

            if (
                name_no_space
                in _NON_ITEM_NAMES
            ):
                continue

            if any(
                kw.replace(" ", "")
                in name_no_space
                for kw in (
                    _TOTAL_PRIORITY
                    + _TOTAL_SECONDARY
                )
            ):
                continue

            if re.search(
                r"[가-힣]",
                name,
            ):
                korean_candidate = name
                break

    return (
        korean_candidate
        or english_candidate
        or None
    )


# ── 주소 추출 ─────────────────────────────────────────────────────────────────

def extract_payment_location(
    text: str,
) -> Optional[str]:

    lines = text.splitlines()

    for i, line in enumerate(
        lines
    ):

        line = line.strip()

        if re.match(
            r"^(주소|소재지|위치|소|주|위|address)\s*[:：]",
            line,
            re.I,
        ):

            addr = re.sub(
                r"^[^:：]+[:：]\s*",
                "",
                line,
            ).strip()

            if len(addr) >= 5:

                if i + 1 < len(lines):

                    next_line = (
                        lines[i + 1]
                        .strip()
                    )

                    if (
                        re.search(
                            r"(번길|번지|\d+호|\d+동)",
                            next_line,
                        )
                        and len(next_line) < 30
                    ):
                        addr = (
                            addr
                            + next_line
                        )

                return addr

        elif (
            _ADDRESS_RE.search(line)
            and len(line) >= 8
        ):

            if re.search(
                r"(\d{3}-\d{4}-\d{4}|\d{3}-\d{2}-\d{5})",
                line,
            ):
                continue

            addr = line

            if i + 1 < len(lines):

                next_line = (
                    lines[i + 1]
                    .strip()
                )

                if (
                    re.search(
                        r"(번길|번지|\d+호|\d+동)",
                        next_line,
                    )
                    and len(next_line) < 30
                ):
                    addr = (
                        addr
                        + next_line
                    )

            return addr

    return None


# ── 날짜 추출 ─────────────────────────────────────────────────────────────────

def extract_payment_date(
    text: str,
) -> Optional[str]:

    date_labels = [
        "일시",
        "날짜",
        "거래일",
        "결제일",
        "승인일시",
        "date",
        "거래일시",
    ]

    lines = text.splitlines()

    for line in lines:

        lower = line.lower()

        if any(
            label in lower
            for label in date_labels
        ):

            m = _DATE_RE.search(
                line
            )

            if m:
                return _normalize_date(
                    m.group()
                )

    for line in lines:

        if is_noise_line(
            line
        ):
            continue

        if re.search(
            r"\d{8}-\d{2}-\d{4}",
            line,
        ):
            continue

        m = _DATE_RE.search(
            line
        )

        if m:
            return _normalize_date(
                m.group()
            )

    return None


def _normalize_date(
    raw: str,
) -> str:

    m = re.match(
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        raw,
    )

    if m:

        return (
            f"{m.group(1)}-"
            f"{m.group(2).zfill(2)}-"
            f"{m.group(3).zfill(2)}"
        )

    parts = re.split(
        r"[-./]",
        raw,
    )

    if len(parts) == 3:

        y = parts[0].strip()
        mo = parts[1].strip()
        d = parts[2][:2].strip()

        if len(y) == 2:
            y = "20" + y

        return (
            f"{y}-"
            f"{mo.zfill(2)}-"
            f"{d.zfill(2)}"
        )

    return raw


# ── 금액 파싱 ─────────────────────────────────────────────────────────────────

def extract_amounts(
    line: str,
) -> List[int]:

    line = re.sub(
        r"(\d{1,3})\s*([,.])\s*(\d{3})",
        r"\1,\3",
        line,
    )

    numbers = re.findall(
        r"\d{1,3}(?:,\d{3})+|\d+",
        line,
    )

    result = []

    for number in numbers:

        value = int(
            number.replace(
                ",",
                "",
            )
        )

        if value >= 100:
            result.append(
                value
            )

    return result


def extract_total_amount(
    text: str,
) -> Optional[int]:

    lines = text.splitlines()

    merged: List[str] = []

    k = 0

    while k < len(lines):

        s = lines[k].strip()

        if s == "계":

            merged.append(
                "합계"
            )

            k += 1
            continue

        if (
            re.fullmatch(
                r"[가-힣]{1,2}",
                s,
            )
            and k + 1 < len(lines)
        ):

            nxt = (
                lines[k + 1]
                .strip()
            )

            if re.fullmatch(
                r"[가-힣]{1,2}",
                nxt,
            ):

                merged.append(
                    s + nxt
                )

                k += 2
                continue

        merged.append(
            s
        )

        k += 1

    lines = merged

    def _safe_amounts(
        line: str,
    ) -> List[int]:

        if (
            is_noise_line(line)
            or _MASKED_NUMBER_RE.search(
                line
            )
        ):
            return []

        return extract_amounts(
            line
        )

    for keywords in [
        _TOTAL_PRIORITY,
        _TOTAL_SECONDARY,
    ]:

        for i, line in enumerate(
            lines
        ):

            lower = line.lower()

            lower_nospace = (
                lower.replace(
                    " ",
                    "",
                )
            )

            if any(
                kw in lower
                or kw.replace(
                    " ",
                    "",
                ) in lower_nospace
                for kw in keywords
            ):

                if (
                    is_discount_line(line)
                    or is_subtotal_line(line)
                ):
                    continue

                if lower.strip() == "total":
                    continue

                amounts = extract_amounts(
                    line
                )

                if amounts:
                    return amounts[-1]

                forward_amounts: List[int] = []

                for j in range(
                    i + 1,
                    min(
                        i + 11,
                        len(lines),
                    ),
                ):

                    forward_amounts.extend(
                        _safe_amounts(
                            lines[j].strip()
                        )
                    )

                if forward_amounts:
                    return max(
                        forward_amounts
                    )

                for j in range(
                    i - 1,
                    max(
                        i - 11,
                        -1,
                    ),
                    -1,
                ):

                    amounts = _safe_amounts(
                        lines[j].strip()
                    )

                    if amounts:
                        return amounts[-1]

    all_amounts = []

    for line in lines:

        all_amounts.extend(
            _safe_amounts(
                line.strip()
            )
        )

    return (
        max(all_amounts)
        if all_amounts
        else None
    )


# ── 품목명 정규화 ─────────────────────────────────────────────────────────────

def normalize_item_name(
    name: str,
) -> str:

    for ch in [
        "G)",
        "ㄴ",
        "->",
        "(",
        ")",
    ]:

        name = name.replace(
            ch,
            " "
            if ch in ["(", ")"]
            else "",
        )

    name = re.sub(
        r"[^가-힣a-zA-Z0-9\s]",
        "",
        name,
    )

    return re.sub(
        r"\s+",
        " ",
        name,
    ).strip()


def is_valid_item_name(
    name: str,
) -> bool:
    """
    실제 상품명으로 사용할 수 있는지 판별한다.

    결제/합계/세금/헤더 등의 문자열은
    이 단계에서 상품 후보에서 제거한다.
    """

    if not name:
        return False

    name = name.strip()

    if len(name) < 2:
        return False

    # 숫자만
    if re.fullmatch(
        r"\d+",
        name,
    ):
        return False

    # 금액만
    if re.fullmatch(
        r"[\d,]+\s*원?",
        name,
    ):
        return False

    name_no_space = (
        name.replace(
            " ",
            "",
        )
        .lower()
    )

    # 정확한 비상품명
    if (
        name_no_space
        in _NON_ITEM_NAMES
    ):
        return False

    # 소계 계열
    if any(
        keyword.replace(
            " ",
            "",
        )
        in name_no_space
        for keyword
        in _SUBTOTAL_KEYWORDS
    ):
        return False

    # 총액/결제 계열
    if any(
        keyword.replace(
            " ",
            "",
        )
        in name_no_space
        for keyword
        in (
            _TOTAL_PRIORITY
            + _TOTAL_SECONDARY
        )
    ):
        return False

    # ○○가액 형태
    if name_no_space.endswith(
        "가액"
    ):
        return False

    # 세금/정산 문자열
    settlement_keywords = [
        "부가세",
        "부가가치세",
        "과세물품",
        "면세물품",
        "과세금액",
        "면세금액",
        "공급가액",
        "공급금액",
        "결제대상",
        "결제금액",
        "승인금액",
        "판매총액",
        "받을금액",
    ]

    if any(
        keyword in name_no_space
        for keyword
        in settlement_keywords
    ):
        return False

    return True


# ── 노이즈 / 옵션 라인 ────────────────────────────────────────────────────────

def is_noise_line(
    line: str,
) -> bool:

    noise_kw = [
        # 세금 / 정산
        "부가세",
        "부가가치세",
        "과세",
        "면세",
        "공급가",
        "공급가액",
        "공급금액",
        "과세금액",
        "면세금액",
        "과세물품",
        "면세물품",
        "세액",
        "세금",
        "영세율",
        "과표",
        "vat",
        "tax",

        # 결제 / 합계 - 추가 강화
        "결제대상금액",
        "결제 대상 금액",
        "결제금액",
        "결제 금액",
        "승인금액",
        "청구금액",
        "받을금액",
        "받은금액",
        "판매총액",
        "총금액",
        "총액",
        "총합계",
        "소계",
        "중간합계",

        # 상품 테이블 헤더
        "상품명",
        "품목명",
        "품 명",
        "단가 수량 금액",
        "단가수량금액",

        # 카드 결제 정보
        "승인",
        "승인번호",
        "승인일시",
        "카드",
        "카드번호",
        "카드사",
        "유효기간",
        "현금",
        "일시불",
        "할부",
        "개월",
        "ic신용",
        "ic직불",
        "마그네틱",
        "비접촉",
        "rf",
        "단말기",
        "단말번호",
        "전표번호",
        "진표번호",
        "거래번호",
        "거래일시",
        "거래금액",
        "결제방법",
        "결제수단",
        "매입",

        # VAN / PG
        "ksnet",
        "kcp",
        "kicc",
        "van사",
        "나이스",
        "nice",
        "kg이니시스",
        "이니시스",
        "inicis",
        "갤럭시아",
        "스마트로",
        "smartro",
        "헥토",
        "다날",
        "세틀뱅크",
        "페이레터",

        # 간편결제
        "tosspay",
        "kakaopay",
        "naverpay",
        "삼성페이",
        "토스페이",
        "카카오페이",
        "네이버페이",
        "페이코",
        "payco",
        "쓱페이",
        "ssupay",
        "lpay",
        "l페이",
        "제로페이",
        "zeropay",
        "애플페이",

        # 카드사
        "토스뱅크",
        "하나카드",
        "국민카드",
        "신한카드",
        "삼성카드",
        "현대카드",
        "롯데카드",
        "우리카드",
        "bc카드",
        "농협카드",
        "기업카드",
        "씨티카드",
        "카카오뱅크",
        "케이뱅크",
        "알림",

        # 사용금액
        "사용금액",
        "용금액",
        "사용한도",
        "이용금액",
        "이용한도",
        "잔여한도",

        # 사업자 정보
        "사업자",
        "사업자번호",
        "사업자등록번호",
        "대표",
        "대표자",
        "주소",
        "소재지",
        "전화",
        "tel",
        "fax",
        "팩스",
        "가맹",
        "가맹번호",

        # OCR 오인식
        "가세",
        "루가세",

        # 영수증 메타
        "영수증",
        "영수증번호",
        "가맹점용",
        "고객용",
        "회원용",
        "주문번호",
        "주문일시",
        "결제완료",
        "결제승인",
        "표준v",
        "간이과세",
        "일반과세",
        "매장",
        "receipt",
        "invoice",
        "pos:",

        # 고객 / 포인트
        "고객",
        "고객번호",
        "고객센터",
        "콜센터",
        "포인트",
        "마일리지",
        "스탬프",
        "교환",
        "환불",
        "반품",
        "잔액",
        "거스름돈",
        "거스름",
        "적립",
        "차감",
        "문의",
        "멤버십",
        "회원",
        "회원번호",

        # 공지
        "보상금",
        "포상",
        "신고포상",
        "연락",
        "신고",
        "홍보",
        "이용안내",
        "주의사항",

        # 서비스 / 배달
        "봉사료",
        "service",
        "배달비",
        "배달료",
        "배달팁",
        "포장비",

        # 기존 OCR 오인식
        "받은금",
        "oh",
        "fsy",
        "wurd",
        "프라자",
        "을은",
        "코나아이",
        "합받받",
        "품가액",
        "표자",
        "코업",
        "업자번호",
        "제품받는곳",
        "모니터에",
    ]

    lower = line.lower()

    lower_nospace = (
        lower.replace(
            " ",
            "",
        )
    )

    if re.match(
        r"^\*\d+",
        line.strip(),
    ):
        return True

    return any(
        keyword in lower
        or keyword.replace(
            " ",
            "",
        ) in lower_nospace
        for keyword in noise_kw
    )


def is_discount_line(
    line: str,
) -> bool:

    discount_kw = [
        "할인",
        "쿠폰",
        "적립",
        "차감",
        "감면",
        "dc",
    ]

    lower = line.lower()

    if any(
        kw in lower
        for kw in discount_kw
    ):
        return True

    if (
        _DISCOUNT_RE.search(line)
        and not re.search(
            r"(?<!\-)\d{1,3}(?:,\d{3})+(?!\s*원?\s*[-−])",
            line,
        )
    ):
        return True

    return False


def is_option_line(
    line: str,
) -> bool:

    option_kw = [
        "컵",
        "뚜껑",
        "빨대",
        "무료 봉투",
        "캐리어",
        "포장",
        "옵션",
        "무료",
        "증정",
        "봉투 0",
    ]

    normalized = normalize_item_name(
        line
    )

    return any(
        kw in normalized
        for kw in option_kw
    )


def is_subtotal_line(
    line: str,
) -> bool:

    lower = (
        line.lower()
        .replace(
            " ",
            "",
        )
    )

    return any(
        kw.replace(
            " ",
            "",
        ) in lower
        for kw in _SUBTOTAL_KEYWORDS
    )


# ── 가격 선택 ─────────────────────────────────────────────────────────────────

def choose_item_price(
    amounts: List[int],
    total_amount: Optional[int],
) -> Optional[int]:

    if not amounts:
        return None

    upper = (
        total_amount * 10
        if total_amount
        else 10_000_000
    )

    candidates = [
        amount
        for amount in amounts
        if amount <= upper
    ]

    if total_amount:

        candidates = [
            amount
            for amount in candidates
            if amount <= total_amount
        ]

    return (
        min(candidates)
        if candidates
        else None
    )


# ── 품목 추출 ─────────────────────────────────────────────────────────────────

def extract_items(
    text: str,
    total_amount: Optional[int],
) -> List[Dict]:

    lines = text.splitlines()

    items = []

    last_item_price = None

    current_item_name = None

    item_name_line_index = None

    pending_names: List[str] = []

    break_line_idx: Optional[int] = None

    column_format_abandoned = False

    # 상품 영역 시작점
    items_start_idx = 0

    for _idx, _line in enumerate(
        lines
    ):

        if re.search(
            r"^(상품명|품\s*명|품목명)(\s|$)",
            _line.strip(),
        ):

            items_start_idx = (
                _idx + 1
            )

            break

    for i, line in enumerate(
        lines
    ):

        stripped = line.strip()

        if i < items_start_idx:
            continue

        if not stripped:
            continue

        # =========================================================
        # 결제 / 합계 라인이 나오면 상품 영역 종료
        # =========================================================

        lower = stripped.lower()

        lower_nospace = (
            lower.replace(
                " ",
                "",
            )
        )

        if lower.strip() in {
            "total",
            "qty item",
            "qty",
            "item",
        }:
            continue

        if any(
            keyword in lower
            or keyword.replace(
                " ",
                "",
            ) in lower_nospace
            for keyword
            in (
                _TOTAL_PRIORITY
                + _TOTAL_SECONDARY
            )
        ):

            break_line_idx = i
            break

        # 날짜
        if _DATE_RE.search(
            stripped
        ):
            continue

        if is_subtotal_line(
            stripped
        ):

            current_item_name = None
            continue

        if is_noise_line(
            stripped
        ):
            continue

        if is_discount_line(
            stripped
        ):
            continue

        if is_option_line(
            stripped
        ):
            continue

        # 주소
        if (
            _ADDRESS_RE.search(
                stripped
            )
            and not re.match(
                r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\)",
                stripped,
            )
        ):
            continue

        if re.search(
            r"\d+호$",
            stripped,
        ):
            continue

        if "%" in stripped:
            continue

        if re.fullmatch(
            r"\[.+\]",
            stripped,
        ):
            continue

        # =========================================================
        # 수량 × 단가 = 총액
        # =========================================================

        qty_m = _QTY_PRICE_RE.search(
            stripped
        )

        if (
            qty_m
            and current_item_name
        ):

            total_price = int(
                qty_m.group(2)
                .replace(
                    ",",
                    "",
                )
            )

            if (
                total_price > 0
                and (
                    total_amount is None
                    or total_price <= total_amount
                )
                and is_valid_item_name(
                    current_item_name
                )
            ):

                items.append(
                    {
                        "name":
                            current_item_name,

                        "price":
                            total_price,
                    }
                )

                current_item_name = None

            continue

        has_korean = bool(
            re.search(
                r"[가-힣]",
                stripped,
            )
        )

        comma_nums = re.findall(
            r"\d{1,3}(?:,\d{3})+",
            stripped,
        )

        amounts = (
            extract_amounts(
                stripped
            )
            if (
                comma_nums
                or not has_korean
            )
            else []
        )

        # =========================================================
        # 금액 없는 라인 → 이름 후보
        # =========================================================

        if not amounts:

            if "/" in stripped:
                continue

            name = normalize_item_name(
                stripped
            )

            name = re.sub(
                r"\s+\d+$",
                "",
                name,
            ).strip()

            if is_valid_item_name(
                name
            ):

                current_item_name = name

                item_name_line_index = i

                if (
                    items_start_idx > 0
                    and not items
                    and not column_format_abandoned
                ):

                    pending_names.append(
                        name
                    )

            continue

        # =========================================================
        # 금액 포함 라인
        # =========================================================

        name_part = re.sub(
            r"\d{1,3}(?:,\d{3})+|\d+",
            "",
            stripped,
        )

        name_part = re.sub(
            r"[×xX✕*=＝]",
            "",
            name_part,
        )

        item_name = normalize_item_name(
            name_part
        )

        if is_valid_item_name(
            item_name
        ):

            final_name = item_name

        elif (
            current_item_name
            and is_valid_item_name(
                current_item_name
            )
        ):

            final_name = current_item_name

        else:
            continue

        price = choose_item_price(
            amounts,
            total_amount,
        )

        if (
            price is None
            or price <= 0
        ):

            if re.search(
                r"[가-힣a-zA-Z]",
                stripped,
            ):
                current_item_name = None

            continue

        using_carried_name = (
            not is_valid_item_name(
                item_name
            )
            and current_item_name
            is not None
        )

        gap = (
            i - item_name_line_index
            if item_name_line_index
            is not None
            else 99
        )

        if (
            using_carried_name
            and price == last_item_price
            and gap > 1
        ):
            continue

        # =========================================================
        # 최종 방어
        # =========================================================

        if not is_valid_item_name(
            final_name
        ):
            continue

        pending_names = []

        column_format_abandoned = True

        items.append(
            {
                "name":
                    final_name,

                "price":
                    price,
            }
        )

        last_item_price = price

        current_item_name = None

    # =============================================================
    # 컬럼 포맷 복구
    # =============================================================

    if (
        not items
        and pending_names
        and break_line_idx
        is not None
    ):

        item_prices: List[int] = []

        prev_a: Optional[int] = None

        found_enough = False

        for line in lines[
            break_line_idx:
        ]:

            if found_enough:
                break

            s = line.strip()

            if (
                is_noise_line(s)
                or is_discount_line(s)
            ):
                continue

            for amount in extract_amounts(
                s
            ):

                if (
                    total_amount
                    and amount > total_amount
                ):
                    continue

                if amount != prev_a:

                    item_prices.append(
                        amount
                    )

                    prev_a = amount

                    if (
                        len(item_prices)
                        >= len(
                            pending_names
                        )
                    ):

                        found_enough = True
                        break

        for j, name in enumerate(
            pending_names
        ):

            if (
                j < len(item_prices)
                and is_valid_item_name(
                    name
                )
            ):

                items.append(
                    {
                        "name":
                            name,

                        "price":
                            item_prices[j],
                    }
                )

    # =============================================================
    # 마지막 안전 필터
    # =============================================================

    filtered_items = []

    for item in items:

        name = item.get(
            "name",
            "",
        )

        price = item.get(
            "price",
        )

        if not is_valid_item_name(
            name
        ):
            continue

        if (
            price is None
            or price <= 0
        ):
            continue

        filtered_items.append(
            item
        )

    return filtered_items


# ── 통합 파싱 ─────────────────────────────────────────────────────────────────

def parse_receipt_text(
    raw_text: str,
) -> Dict:

    cleaned = clean_ocr_text(
        raw_text
    )

    total_amount = extract_total_amount(
        cleaned
    )

    return {
        "merchant_name":
            extract_merchant_name(
                cleaned
            ),

        "payment_location":
            extract_payment_location(
                cleaned
            ),

        "payment_date":
            extract_payment_date(
                cleaned
            ),

        "total_amount":
            total_amount,

        "items":
            extract_items(
                cleaned,
                total_amount,
            ),
    }