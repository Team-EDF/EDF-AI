import re
from typing import Dict, List, Optional

# ── 컴파일된 패턴 ──────────────────────────────────────────────────────────────

# 주소 판별: 광역시·도 or 구·동·로·길 포함
_ADDRESS_RE = re.compile(
    r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
    r"|([가-힣]{2,}(시|도|구|군)\s?[가-힣]+(시|구|군|읍|면|동|로|길))"
    r"|(로\s?\d+|길\s?\d+|번지|번길)"
    r"|\d+호\s*[\(（\[]"   # "3호(영덕동," 같은 주소 연속 줄
    r"|\d+호\s*[가-힣]+동" # "103호영덕동" 패턴
)

# 날짜: YYYY-MM-DD / YY.MM.DD / 2024년5월31일 등
_DATE_RE = re.compile(
    # 1. YYYY/MM/DD, YYYY-MM-DD, YYYY.MM.DD (구분자 앞뒤 공백 허용)
    r"(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})"
    # 2. YY/MM/DD, YY-MM-DD, YY.MM.DD (구분자 앞뒤 공백 허용)
    r"|(\d{2}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})"
    # 3. 2024년 05월 31일 (한글 포맷)
    r"|(\d{4}년\s*\d{1,2}월\s*\d{1,2}일)"
)

# 가맹점명 앞에 붙는 레이블
_MERCHANT_LABEL_RE = re.compile(
    r"^(상호|가맹점명?|점포명?|매장명?|사업체명)\s*[:：]\s*(.+)"
)

# 수량 × 단가 = 총액 형식 (예: 아메리카노 1 × 4,500 = 4,500)
_QTY_PRICE_RE = re.compile(
    r"(\d+)\s*[×xX✕*]\s*[\d,]+\s*[=＝]\s*([\d,]+)"
)

# 할인·차감 금액 (음수 표기): -500 / − 1,000 / 할인 -500
_DISCOUNT_RE = re.compile(r"[-−＿]\s*[\d,]+")

# 가맹점명 레이블 앞에서 제외할 키워드
_MERCHANT_SKIP = {"영수증", "receipt", "tax invoice", "간이", "세금계산서", "invoice", "주문", "받아가세요"}

# 소계·중간합계 키워드 (품목으로 잡히면 안 됨)
_SUBTOTAL_KEYWORDS = {"소계", "소합계", "중간합계", "sub total", "subtotal", "소 계", "합계수량", "수량합계"}

# 총액 우선·후순위 키워드
_TOTAL_PRIORITY = ["결제금액", "받을금액", "받은금액", "총금액", "총액", "실결제", "청구금액", "합계금액"]
_TOTAL_SECONDARY = ["합계", "합게", "총매출액", "매출액", "total", "금액:", "계:"]  # 합게 = 합계 OCR 오인식

# 카드번호·마스킹 패턴 (5:5327-50**, 0949570154 등): 총액 탐색 시 건너뜀
_MASKED_NUMBER_RE = re.compile(r"\*{2,}|\d{4}[-]\d{2,}|\d{8,}|\d{2,4}[-]\d{3,4}[-]\d{4}")


# ── 텍스트 전처리 ──────────────────────────────────────────────────────────────

def clean_ocr_text(raw_text: str) -> str:
    lines = raw_text.splitlines()
    cleaned = []
    for line in lines:
        line = line.strip()
        line = re.sub(r"\d{3}-\d{2}-\d{5}", "", line).strip()
        line = re.sub(r"\s+", " ", line)
        if line:
            cleaned.append(line)
    return "\n".join(cleaned)


# ── 가맹점명 추출 ──────────────────────────────────────────────────────────────

def extract_merchant_name(text: str) -> Optional[str]:
    """레이블 우선 → 상단 휴리스틱 순서로 가맹점명 추출."""
    lines = text.splitlines()

    # 1) "상호:", "가맹점명:" 등 명시적 레이블이 있으면 최우선
    for line in lines[:20]:
        m = _MERCHANT_LABEL_RE.match(line.strip())
        if m:
            name = m.group(2).strip()
            name = re.sub(r"[^\w\s가-힣]", "", name).strip()
            if len(name) >= 2:
                return name

    # 2) 상단 8줄에서 후보 수집 → 한글 포함 이름 우선, 없으면 영어 이름
    korean_candidate = None
    english_candidate = None

    for line in lines[:8]:
        line = line.strip()
        if not line or len(line) < 2 or len(line) > 40:
            continue
        if re.fullmatch(r"[\d\s\-/.,:()]+", line):
            continue
        if is_noise_line(line):  # 카드사명 등 노이즈 라인 제외
            continue
        if _DATE_RE.search(line):
            continue
        if re.search(r"\d{2,4}[-–]\d{3,4}(?!\d)", line):
            continue
        if _ADDRESS_RE.search(line):
            continue
        if any(kw in line.lower() for kw in _MERCHANT_SKIP):
            continue
        if not re.search(r"[가-힣a-zA-Z]", line):
            continue
        # "가게명 / 대표자명" 형태에서 가게명만 추출
        if "/" in line or "／" in line:
            candidate = re.split(r"[/／]", line)[0].strip()
            cname = re.sub(r"[^\w\s가-힣]", "", candidate).strip()
            if len(cname) >= 2 and re.search(r"[가-힣a-zA-Z]", cname):
                return cname
            continue
        name = re.sub(r"[^\w\s가-힣]", "", line).strip()
        if re.search(r"^\d[\d,]*원$", name):
            continue
        if len(name) < 2:
            continue
        # 슬로건 필터: 공백이 2개 이상이고 단어가 4개 이상이면 광고 문구로 판단
        if len(name.split()) >= 4:
            continue

        if any(kw in name.replace(" ", "") for kw in ["합계", "총액", "총합계", "결제금액", "판매금액"]):
            continue
        # 한글 포함 → 우선 후보
        if re.search(r"[가-힣]", name):
            if korean_candidate is None:
                korean_candidate = name
        else:
            # 영어 전용 (브랜드 로고 등) → 차선 후보
            if english_candidate is None and re.search(r"[a-z]", name):
                english_candidate = name

    if korean_candidate is None:
        for line in lines[8:25]:
            line = line.strip()
            if not line or len(line) < 2 or len(line) > 40:
                continue
            if re.fullmatch(r"[\d\s\-/.,:()]+", line):
                continue
            if is_noise_line(line):
                continue
            if _DATE_RE.search(line):
                continue
            if re.search(r"\d{2,4}[-–]\d{3,4}(?!\d)", line):
                continue
            if _ADDRESS_RE.search(line):
                continue
            if any(kw in line.lower() for kw in _MERCHANT_SKIP):
                continue
            if not re.search(r"[가-힣a-zA-Z]", line):
                continue
            if "/" in line or "／" in line:
                candidate = re.split(r"[/／]", line)[0].strip()
                cname = re.sub(r"[^\w\s가-힣]", "", candidate).strip()
                if len(cname) >= 2 and re.search(r"[가-힣a-zA-Z]", cname):
                    korean_candidate = cname
                    break
                continue
            name = re.sub(r"[^\w\s가-힣]", "", line).strip()
            if re.search(r"^\d[\d,]*원$", name):
                continue
            if len(name) < 2:
                continue
            if len(name.split()) >= 4:
                continue
            if any(kw in name.replace(" ", "") for kw in ["합계", "총액", "총합계", "결제금액"]):
                continue
            if re.search(r"[가-힣]", name):
                korean_candidate = name
                break  # 첫 번째 한글 후보에서 즉시 종료


    return korean_candidate or english_candidate or None


# ── 주소 추출 ─────────────────────────────────────────────────────────────────

def extract_payment_location(text: str) -> Optional[str]:
    """'주소:' 레이블 우선 → 주소 패턴 순으로 추출. 다음 줄이 번지/호수면 합쳐서 반환."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        # 레이블 있는 경우 (OCR이 "소재지:"를 "소:"로 잘라내는 경우 포함)
        if re.match(r"^(주소|소재지|위치|소|주|위|address)\s*[:：]", line, re.I):
            addr = re.sub(r"^[^:：]+[:：]\s*", "", line).strip()
            if len(addr) >= 5:
                # 다음 줄이 번지·호수 연속이면 합치기 (예: 207 + 7번길 33 → 2077번길 33)
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if re.search(r"(번길|번지|\d+호|\d+동)", next_line) and len(next_line) < 30:
                        addr = addr + next_line
                return addr
        # 레이블 없이 주소 패턴이 있는 경우
        elif _ADDRESS_RE.search(line) and len(line) >= 8:
            if re.search(r"(\d{3}-\d{4}-\d{4}|\d{3}-\d{2}-\d{5})", line):
                continue
            addr = line
            # 다음 줄이 번지·호수 연속이면 합치기 (예: "번길 33, C동 105호")
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if re.search(r"(번길|번지|\d+호|\d+동)", next_line) and len(next_line) < 30:
                    addr = addr + next_line
            return addr
    return None


# ── 날짜 추출 ─────────────────────────────────────────────────────────────────

def extract_payment_date(text: str) -> Optional[str]:
    """날짜 레이블 우선 → 날짜 패턴 순으로 YYYY-MM-DD 형식 반환."""
    date_labels = ["일시", "날짜", "거래일", "결제일", "승인일시", "date", "거래일시"]
    lines = text.splitlines()

    for line in lines:
        lower = line.lower()
        if any(label in lower for label in date_labels):
            m = _DATE_RE.search(line)
            if m:
                return _normalize_date(m.group())

    # 노이즈 라인(사업자번호 "567-10-02121/ Tel:..." 등)은 날짜 오인식 방지를 위해 제외
    for line in lines:
        if is_noise_line(line):
            continue
        if re.search(r"\d{8}-\d{2}-\d{4}", line):
            continue
        m = _DATE_RE.search(line)
        if m:
            return _normalize_date(m.group())
    return None


def _normalize_date(raw: str) -> str:
    # "2024년 5월 31일" 형식
    m = re.match(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", raw)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    # 구분자 형식
    parts = re.split(r"[-./]", raw)
    if len(parts) == 3:
        y, mo, d = parts[0], parts[1], parts[2][:2]  # 시간 제거
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return raw


# ── 금액 파싱 헬퍼 ────────────────────────────────────────────────────────────

def extract_amounts(line: str) -> List[int]:
    """100 이상 양수 금액만 반환. OCR이 3,000을 3.000 또는 3. 000으로 인식하는 경우도 처리."""
    # OCR이 "6. 000" / "6, 000" 처럼 구분자 뒤에 공백을 삽입하는 경우 제거
    line = re.sub(r"(\d{1,3})\s*([,.])\s*(\d{3})", r"\1,\3", line)
    numbers = re.findall(r"\d{1,3}(?:,\d{3})+|\d+", line)
    result = []
    for n in numbers:
        value = int(n.replace(",", ""))
        if value >= 100:
            result.append(value)
    return result


def extract_total_amount(text: str) -> Optional[int]:
    """결제금액 우선, 합계 후순위로 총액 추출.

    탐색 순서:
    1. 키워드와 같은 줄에서 금액 추출
    2. 키워드 이후 3줄 forward 탐색 (노이즈·카드번호 필터 적용)
    3. 키워드 이전 10줄 backward 탐색 — IC카드 영수증처럼 금액이 레이블보다
       먼저 인쇄된 포맷 대응 (노이즈·카드번호 필터 적용)
    """
    lines = text.splitlines()

    # OCR이 "합"+"계"처럼 1-2자 한글을 별도 줄로 분리한 경우 인접 줄과 병합
    # "계" 단독 줄 = "합 계"에서 "합"이 완전히 탈락한 OCR 오류 → "합계"로 복원
    merged: List[str] = []
    k = 0
    while k < len(lines):
        s = lines[k].strip()
        if s == "계":
            merged.append("합계")
            k += 1
            continue
        if re.fullmatch(r"[가-힣]{1,2}", s) and k + 1 < len(lines):
            nxt = lines[k + 1].strip()
            if re.fullmatch(r"[가-힣]{1,2}", nxt):
                merged.append(s + nxt)
                k += 2
                continue
        merged.append(s)
        k += 1
    lines = merged

    def _safe_amounts(line: str) -> List[int]:
        """카드번호·마스킹·노이즈 라인은 빈 리스트 반환."""
        if is_noise_line(line) or _MASKED_NUMBER_RE.search(line):
            return []
        return extract_amounts(line)

    for keywords in [_TOTAL_PRIORITY, _TOTAL_SECONDARY]:
        for i, line in enumerate(lines):
            lower = line.lower()
            lower_nospace = lower.replace(" ", "")
            if any(kw in lower or kw.replace(" ", "") in lower_nospace for kw in keywords):
                # 할인합계·합계수량 등 소계/할인 복합 라인은 총액으로 인정하지 않음
                if is_discount_line(line) or is_subtotal_line(line):
                    continue
                if lower.strip() == "total":  # 컬럼 헤더 "TOTAL" 단독 → 스킵
                    continue
                # 1) 같은 줄
                amounts = extract_amounts(line)
                if amounts:
                    return amounts[-1]
                # 2) 이후 5줄: 공급가액→부가세→합계 순서로 나열되는 IC카드 포맷 대응.
                # 첫 금액에서 즉시 반환하지 않고 연속된 금액을 모두 수집한 뒤
                # 마지막 값(= 부가세 포함 합계)을 반환한다.
                forward_amounts: List[int] = []
                for j in range(i + 1, min(i + 11, len(lines))):
                    forward_amounts.extend(_safe_amounts(lines[j].strip()))
                if forward_amounts:
                    return max(forward_amounts)
                # 3) 이전 10줄 역탐색 (금액이 레이블보다 먼저 나오는 IC카드 포맷)
                for j in range(i - 1, max(i - 11, -1), -1):
                    amounts = _safe_amounts(lines[j].strip())
                    if amounts:
                        return amounts[-1]
    # 수정: 키워드 없을 때 _safe_amounts 중 최대값 fallback
    all_amounts = []
    for line in lines:
        all_amounts.extend(_safe_amounts(line.strip()))
    return max(all_amounts) if all_amounts else None


# ── 품목명 정규화 ─────────────────────────────────────────────────────────────

def normalize_item_name(name: str) -> str:
    for ch in ["G)", "ㄴ", "->", "(", ")"]:
        name = name.replace(ch, " " if ch in ["(", ")"] else "")
    name = re.sub(r"[^가-힣a-zA-Z0-9\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def is_valid_item_name(name: str) -> bool:
    if not name or len(name) < 2:
        return False
    if re.fullmatch(r"\d+", name):
        return False
    # 기존 re.fullmatch(r"\d+", name) 아래에 추가
    if re.fullmatch(r"[\d,]+\s*원?", name):  # "545 원", "6000원" 등 금액 패턴
        return False

    # OCR이 "합 계"처럼 공백을 삽입하는 경우를 위해 공백 제거 후 비교
    name_no_space = name.replace(" ", "").lower()
    # 기존 exclusion set 체크 다음에 추가
    if name_no_space.endswith("가액"):  # 물품가액·공급가액 류 OCR 잔재
        return False
    if name_no_space in {"합계", "결제금액", "소계", "총액", "받을금액", "받은금액", "총금액", "거래금액", "공급금액", "공급가액", "부가세", "과세금액",
                         "no", "no.", "금액", "상품명", "단가수량", "단가", "수량"} | {k.replace(" ", "") for k in _SUBTOTAL_KEYWORDS}:
        return False
    return True


# ── 노이즈 / 옵션 라인 판별 ──────────────────────────────────────────────────

def is_noise_line(line: str) -> bool:
    noise_kw = [
        # ── 세금·정산 ──────────────────────────────────────────────
        "부가세", "과세", "면세", "공급가", "공급가액", "공급금액",
        "과세금액", "면세금액", "과세물품", "면세물품",
        "세액", "세금", "영세율", "과표",
        "vat", "tax",

        # ── 카드 결제 정보 ──────────────────────────────────────────
        "승인", "승인번호", "승인금액", "승인일시",
        "카드", "카드번호", "카드사", "유효기간",
        "현금", "일시불", "할부", "개월",
        "ic신용", "ic직불", "마그네틱", "비접촉", "rf",
        "단말기", "단말번호",
        "전표번호", "진표번호",
        "거래번호", "거래일시", "거래금액",
        "결제방법", "결제수단",
        "매입",

        # ── VAN사 / PG사 ─────────────────────────────────────────────
        "ksnet", "kcp", "kicc", "van사",
        "나이스", "nice", "kg이니시스", "이니시스", "inicis",
        "갤럭시아", "스마트로", "smartro",
        "헥토", "다날", "세틀뱅크", "페이레터",

        # ── 간편결제 ────────────────────────────────────────────────
        "tosspay", "kakaopay", "naverpay", "삼성페이",
        "토스페이", "카카오페이", "네이버페이",
        "페이코", "payco", "쓱페이", "ssupay",
        "lpay", "l페이", "제로페이", "zeropay",
        "애플페이",

        # ── 카드사 이름 ─────────────────────────────────────────────
        "토스뱅크", "하나카드", "국민카드", "신한카드",
        "삼성카드", "현대카드", "롯데카드", "우리카드",
        "bc카드", "농협카드", "기업카드", "씨티카드",
        "카카오뱅크", "케이뱅크",
        "알림",

        # ── 사용금액 관련 ───────────────────────────────────────────
        "사용금액", "용금액", "사용한도", "이용금액",
        "이용한도", "잔여한도",

        # ── 금액 세부 항목 (총액이 아닌 내역) ──────────────────────
        "거래금액", "공급금액", "공급가액", "과세금액", "판매금액",

        # ── 사업자 정보 ─────────────────────────────────────────────
        "사업자", "사업자번호", "사업자등록번호",
        "대표", "대표자",
        "주소", "소재지",
        "전화", "tel", "fax", "팩스",
        "가맹", "가맹번호",

        # ── 부가세 OCR 오인식 변형 ──────────────────────────────────
        "가세", "루가세",

        # ── 영수증 메타 ─────────────────────────────────────────────
        "영수증", "영수증번호", "가맹점용", "고객용", "회원용",
        "주문번호", "주문일시",
        "결제완료", "결제승인",
        "표준v", "간이과세", "일반과세",
        "매장", "receipt", "invoice",

        # ── 고객·포인트·멤버십 ──────────────────────────────────────
        "고객", "고객번호", "고객센터", "콜센터",
        "포인트", "마일리지", "스탬프",
        "교환", "환불", "반품",
        "잔액", "거스름돈", "거스름",
        "적립", "차감",  # "사용" 제거 — "재사용봉투" 오필터 방지 ("사용금액","사용한도" 등은 별도 등록)
        "문의",
        "멤버십", "회원", "회원번호",

        # ── 홍보·공지 문구 ──────────────────────────────────────────
        "보상금", "포상", "신고포상",
        "연락", "신고",
        "홍보", "이용안내", "주의사항",

        # ── 서비스·배달 ─────────────────────────────────────────────
        "봉사료", "service",
        "배달비", "배달료", "배달팁", "포장비",
        # ── OCR 오인식 단어모음 ─────────────────────────────────────────────
        "받은금", "oh", "fsy", "wurd", "프라자", "을은", "코나아이", "합받받", "품가액", "표자", "코업", "업자번호", "사업자", "대표자", "대표",
        "제품받는곳", "모니터에"
    ]
    lower = line.lower()
    lower_nospace = lower.replace(" ", "")
    # 바코드 라인 필터: *숫자로 시작하는 패턴 (영수증 하단 바코드)
    if re.match(r"^\*\d+", line.strip()):
        return True

    # 공백 제거 비교 추가: OCR이 "금    액"처럼 공백을 삽입해도 잡히도록
    return any(kw in lower or kw in lower_nospace for kw in noise_kw)


def is_discount_line(line: str) -> bool:
    """할인·차감 라인 (음수 금액 포함) 판별."""
    discount_kw = ["할인", "쿠폰", "적립", "차감", "감면", "dc"]
    lower = line.lower()
    if any(kw in lower for kw in discount_kw):
        return True
    # "-1,000" 형태의 음수 금액만 있는 라인
    if _DISCOUNT_RE.search(line) and not re.search(r"(?<!\-)\d{1,3}(?:,\d{3})+(?!\s*원?\s*[-−])", line):
        return True
    return False


def is_option_line(line: str) -> bool:
    option_kw = ["컵", "뚜껑", "빨대", "무료 봉투", "캐리어", "포장", "옵션", "무료", "증정", "봉투 0"]
    normalized = normalize_item_name(line)
    return any(kw in normalized for kw in option_kw)


def is_subtotal_line(line: str) -> bool:
    """소계·중간합계 라인 판별."""
    lower = line.lower().replace(" ", "")
    return any(kw.replace(" ", "") in lower for kw in _SUBTOTAL_KEYWORDS)


# ── 가격 선택 ─────────────────────────────────────────────────────────────────

def choose_item_price(amounts: List[int], total_amount: Optional[int]) -> Optional[int]:
    """total_amount 이하, 그리고 비정상적으로 큰 금액 제외 후 가장 작은 금액 반환."""
    if not amounts:
        return None
    # 총액 대비 10배 초과 금액은 카드번호·거래번호 오인식으로 판단해 제거
    upper = (total_amount * 10) if total_amount else 10_000_000
    candidates = [a for a in amounts if a <= upper]
    if total_amount:
        candidates = [a for a in candidates if a <= total_amount]
    return min(candidates) if candidates else None


# ── 품목 추출 (핵심) ──────────────────────────────────────────────────────────

def extract_items(text: str, total_amount: Optional[int]) -> List[Dict]:
    """
    품목명 + 가격 추출. 처리하는 포맷:
      - "품목명  가격" 한 줄
      - 품목명 / 가격이 각각 다른 줄
      - "수량 × 단가 = 총액" 형식
      - 할인·소계·옵션 라인 제거
      - 컬럼 포맷: 품목명 열이 먼저 나오고 합계 이후 가격 열이 나오는 POS 영수증
    """
    lines = text.splitlines()
    items = []
    last_item_price = None  # 직전 아이템 가격 추적 (컬럼 중복 방지용)
    current_item_name = None
    item_name_line_index = None
    # 컬럼 포맷(품목명 먼저, 합계 다음, 가격 나중) 복구용
    pending_names: List[str] = []   # 가격 없이 수집된 품목명들
    break_line_idx: Optional[int] = None  # break 발생 라인
    column_format_abandoned = False  # True = 가격과 이름이 같은 줄/인접 → 일반 포맷

    items_start_idx = 0
    for _idx, _line in enumerate(lines):
        # "상품명" 단독 OR "상품명  단가 수량 금액" 형태 컬럼 헤더도 감지
        if re.search(r"^(상품명|품\s*명|품목명)(\s|$)", _line.strip()):
            items_start_idx = _idx + 1
            break

    for i, line in enumerate(lines):
        stripped = line.strip()
        if i < items_start_idx:  # ← 이 줄 추가
            continue

        # 총액/합계 라인 → 이후 current_item_name 리셋
        # OCR이 "합 계"처럼 공백을 삽입하는 경우를 위해 공백 제거 후도 비교
        lower = stripped.lower()
        lower_nospace = lower.replace(" ", "")
        if lower.strip() in {"total", "qty item", "qty", "item"}:
            continue
        if any(kw in lower for kw in _TOTAL_PRIORITY + _TOTAL_SECONDARY) or \
                any(kw.replace(" ", "") in lower_nospace for kw in _TOTAL_PRIORITY + _TOTAL_SECONDARY):
            break_line_idx = i
            break

        # 날짜 패턴이 있는 라인 skip (날짜가 품목으로 잡히는 방지)
        if _DATE_RE.search(stripped):
            continue

        if is_subtotal_line(stripped):
            current_item_name = None
            continue
        if is_noise_line(stripped):
            continue
        if is_discount_line(stripped):
            continue
        if is_option_line(stripped):
            continue
        # 주소 패턴 라인 skip (상세주소가 품목으로 잡히는 방지)
        # 단, "서울)365우유" 처럼 지역명이 마트 브랜드 접두어로 쓰인 경우는 주소가 아니므로 제외
        if _ADDRESS_RE.search(stripped) and not re.match(
                r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\)",
                stripped):
            continue
        # 숫자+호로 끝나는 라인은 주소(건물호수) 처리
        if re.search(r"\d+호$", stripped):
            continue
        # "%" 포함 라인 skip — "9121.1% 91 uz 2011-0777" 같은 헤더·판촉 잡음
        if "%" in stripped:
            continue
        # "[IC신용구매]" 등 대괄호로 감싼 라인 skip
        if re.fullmatch(r"\[.+\]", stripped):
            continue

        # ── 수량 × 단가 = 총액 형식 처리 ──────────────────────────────────────
        qty_m = _QTY_PRICE_RE.search(stripped)
        if qty_m and current_item_name:
            total_price = int(qty_m.group(2).replace(",", ""))
            if total_price > 0 and (total_amount is None or total_price <= total_amount):
                items.append({"name": current_item_name, "price": total_price})
                current_item_name = None
            continue

        has_korean = bool(re.search(r"[가-힣]", stripped))
        comma_nums = re.findall(r"\d{1,3}(?:,\d{3})+", stripped)
        amounts = extract_amounts(stripped) if (comma_nums or not has_korean) else []

        if not amounts:
            # 금액 없는 라인 → 품목명 후보로 저장
            # "/"가 포함된 라인은 가맹점명·사업자번호 구분자이므로 품목명으로 취급하지 않음
            if "/" in stripped:
                continue
            name = normalize_item_name(stripped)
            name = re.sub(r"\s+\d+$", "", name).strip()  # 끝 단독 숫자(단가) 제거
            if is_valid_item_name(name):
                current_item_name = name
                item_name_line_index = i
                # 컬럼 포맷 감지: items_start_idx가 설정됐고, 아직 가격을 못 찾은 경우 이름 수집
                if items_start_idx > 0 and not items and not column_format_abandoned:
                    pending_names.append(name)
            continue

        # 금액 있는 라인 → 품목명 추출 시도
        name_part = re.sub(r"\d{1,3}(?:,\d{3})+|\d+", "", stripped)
        name_part = re.sub(r"[×xX✕*=＝]", "", name_part)
        item_name = normalize_item_name(name_part)

        if is_valid_item_name(item_name):
            final_name = item_name
        elif current_item_name:
            final_name = current_item_name
        else:
            continue

        price = choose_item_price(amounts, total_amount)
        if price is None or price <= 0:
            if re.search(r"[가-힣a-zA-Z]", stripped):
                current_item_name = None
            continue

        # ↓ 여기에 추가 (517줄 continue 바로 다음, 519줄 items.append 바로 위)
        using_carried_name = not is_valid_item_name(item_name) and current_item_name is not None
        gap = (i - item_name_line_index) if item_name_line_index is not None else 99
        if using_carried_name and price == last_item_price and gap > 1:
            continue

        pending_names = []
        column_format_abandoned = True

        items.append({"name": final_name, "price": price})
        last_item_price = price
        current_item_name = None

    # ── 컬럼 포맷 복구 ────────────────────────────────────────────────────────────
    # 품목명은 있는데 가격을 못 찾은 경우 (합계 라인 이후에 가격이 나오는 POS 포맷)
    # 예: "품목명1 / 품목명2 / 매출합계 / 단가 수량 / 금액 / 8,900 / 5,500 / ..."
    if not items and pending_names and break_line_idx is not None:
        item_prices: List[int] = []
        prev_a: Optional[int] = None
        found_enough = False
        for line in lines[break_line_idx:]:
            if found_enough:
                break
            s = line.strip()
            if is_noise_line(s) or is_discount_line(s):
                continue
            for a in extract_amounts(s):
                # 총액 이상은 합계/결제금액 → 제외
                if total_amount and a > total_amount:
                    continue
                # 연속 중복 제거: 단가=금액인 경우(수량 1) 같은 값이 두 번 나옴
                if a != prev_a:
                    item_prices.append(a)
                    prev_a = a
                    if len(item_prices) >= len(pending_names):
                        found_enough = True
                        break
        for j, name in enumerate(pending_names):
            if j < len(item_prices):
                items.append({"name": name, "price": item_prices[j]})

    return items


# ── 통합 파싱 ─────────────────────────────────────────────────────────────────

def parse_receipt_text(raw_text: str) -> Dict:
    """OCR raw text → 영수증 전체 구조 반환."""
    cleaned = clean_ocr_text(raw_text)
    total_amount = extract_total_amount(cleaned)
    return {
        "merchant_name": extract_merchant_name(cleaned),
        "payment_location": extract_payment_location(cleaned),
        "payment_date": extract_payment_date(cleaned),
        "total_amount": total_amount,
        "items": extract_items(cleaned, total_amount),
    }
