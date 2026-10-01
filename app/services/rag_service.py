import re
import math
import hashlib
from difflib import SequenceMatcher
from collections import Counter, defaultdict

import numpy as np

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


CHROMA_DIR = "chroma_db"

EMBEDDING_MODEL = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)

VALID_CATEGORIES = {
    "cafe",
    "food",
    "echo_guide",
}


# ============================================================
# Embedding
# ============================================================

_embeddings: HuggingFaceEmbeddings | None = None


def get_embeddings():
    """
    RAG 검색용 임베딩 모델을 최초 한 번만 로드하고 재사용한다.

    매 요청마다 새로 생성하면 HuggingFace Hub 접근이
    요청마다 반복되어 지연/실패(타임아웃)의 원인이 된다.
    """

    global _embeddings

    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL
        )

    return _embeddings


# ============================================================
# Vector Store
# ============================================================

_vector_store: Chroma | None = None


def get_vector_store(
    embeddings=None,
):
    """
    기존 Chroma 벡터스토어를 최초 한 번만 불러오고 재사용한다.
    """

    global _vector_store

    if embeddings is None:
        embeddings = get_embeddings()

    if _vector_store is None:
        _vector_store = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=embeddings,
        )

    return _vector_store


# ============================================================
# Noise Chunk 판별
# ============================================================

def analyze_noise_chunk(
    text: str,
) -> tuple[bool, str]:
    """
    친환경 피드백의 근거로 사용하기 어려운 noise chunk를 판별한다.

    핵심 원칙:
    1. 빈 텍스트 / 지나치게 짧은 텍스트 제거
    2. References, Bibliography 등 명확한 참고문헌 섹션 제거
    3. 저자 + 연도 + 학술지/학회 + DOI/페이지 정보가 밀집된
       bibliography/reference entry 형태 제거
    4. 정상 연구 본문의 citation은 최대한 보존
    5. '부록', 'Appendix'라는 이유만으로는 제거하지 않음
       (부록의 표/시나리오/분석 결과도 RAG 근거가 될 수 있음)

    반환:
        (is_noise, reason)
    """

    if not text:
        return True, "empty_chunk"

    stripped = text.strip()
    normalized = stripped.lower()

    if len(normalized) < 120:
        return True, "too_short"

    lines = [
        line.strip()
        for line in stripped.splitlines()
        if line.strip()
    ]

    # ========================================================
    # 1. 명확한 참고문헌 / 목차 heading
    # ========================================================

    section_heading_patterns = [
        r"^\s*references\s*$",
        r"^\s*bibliography\s*$",
        r"^\s*reference\s+list\s*$",
        r"^\s*literature\s+cited\s*$",
        r"^\s*works\s+cited\s*$",
        r"^\s*참고\s*문헌\s*$",
        r"^\s*목차\s*$",
        r"^\s*table\s+of\s+contents\s*$",
        r"^\s*그림\s*목차\s*$",
        r"^\s*표\s*목차\s*$",
    ]

    # PDF chunk 시작부에서 heading을 검사한다.
    # '부록/Appendix'는 의도적으로 포함하지 않는다.
    for line in lines[:5]:
        for pattern in section_heading_patterns:
            if re.match(
                pattern,
                line,
                flags=re.IGNORECASE,
            ):
                return True, f"section_heading:{line[:60]}"

    # ========================================================
    # 2. 전체 chunk의 reference 관련 신호
    # ========================================================

    year_count = len(
        re.findall(
            r"\b(?:19|20)\d{2}[a-z]?\b",
            normalized,
        )
    )

    doi_count = len(
        re.findall(
            r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+",
            normalized,
        )
    )

    url_count = len(
        re.findall(
            r"https?://\S+|www\.\S+",
            normalized,
        )
    )

    et_al_count = normalized.count("et al.")

    bracket_citation_count = len(
        re.findall(
            r"\[(?:\d+(?:\s*[-,;]\s*\d+)*)\]",
            normalized,
        )
    )

    publication_signals = [
        "journal of ",
        "international journal",
        "proceedings of ",
        "conference on ",
        "conference of ",
        "transactions on ",
        "springer",
        "elsevier",
        "wiley",
        "academic press",
        "university press",
        "issn",
        "isbn",
        "eds.",
        " ed.",
        "in:",
    ]

    publication_signal_count = sum(
        normalized.count(signal)
        for signal in publication_signals
    )

    volume_page_patterns = [
        r"\bvol\.?\s*\d+",
        r"\bvolume\s+\d+",
        r"\bno\.?\s*\d+",
        r"\bissue\s+\d+",
        r"\bpp\.?\s*\d+",
        r"\bpages?\s+\d+",
        r"\b\d+\s*\(\d+\)\s*[:,]\s*\d+",
        r"\b\d+\s*:\s*\d+\s*[-–]\s*\d+",
    ]

    volume_page_count = sum(
        len(
            re.findall(
                pattern,
                normalized,
            )
        )
        for pattern in volume_page_patterns
    )

    # ========================================================
    # 3. 줄 단위 bibliography/reference entry 탐지
    # ========================================================

    bibliography_like_lines = 0
    author_year_line_count = 0
    reference_number_line_count = 0

    # 참고문헌에서 자주 나타나는 저자 시작 형태
    author_start_patterns = [
        # Kendall A and Brodt SB.
        r"^[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4}"
        r"(?:\s+and\s+[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4})",

        # Blonk H, Kool A, Luske B...
        r"^[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4}\s*,\s*"
        r"[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4}",

        # Smith, J.
        r"^[A-Z][A-Za-z'’\-]+,\s*[A-Z](?:\.[A-Z]?)?\.?",
    ]

    for line in lines:
        line_lower = line.lower()

        has_year = bool(
            re.search(
                r"\b(?:19|20)\d{2}[a-z]?\b",
                line_lower,
            )
        )

        has_doi_or_url = bool(
            re.search(
                r"(?:doi(?:\.org)?[:/\s]|https?://|www\.)",
                line_lower,
            )
        )

        has_publication_term = any(
            signal in line_lower
            for signal in [
                "journal",
                "proceedings",
                "conference",
                "vol.",
                "volume ",
                "issue ",
                "pp.",
                "pages ",
                "springer",
                "elsevier",
                "wiley",
                "et al.",
                "eds.",
                "in:",
            ]
        )

        has_volume_page = any(
            re.search(
                pattern,
                line_lower,
            )
            for pattern in volume_page_patterns
        )

        has_author_start = any(
            re.search(
                pattern,
                line,
            )
            for pattern in author_start_patterns
        )

        has_reference_number = bool(
            re.match(
                r"^\s*(?:\[\d+\]|\d{1,3}[.)])\s+",
                line,
            )
        )

        if has_reference_number:
            reference_number_line_count += 1

        if has_author_start and has_year:
            author_year_line_count += 1

        signal_count = sum([
            has_year,
            has_doi_or_url,
            has_publication_term,
            has_volume_page,
            has_author_start,
            has_reference_number,
        ])

        if signal_count >= 3:
            bibliography_like_lines += 1

    line_count = max(
        len(lines),
        1,
    )

    bibliography_ratio = (
        bibliography_like_lines
        / line_count
    )

    author_year_ratio = (
        author_year_line_count
        / line_count
    )

    # ========================================================
    # 4. 한 줄/짧은 reference entry 직접 탐지
    # ========================================================
    #
    # 예:
    # Kendall A and Brodt SB. Comparing Alternative ...
    # In: Schenck R, Huizenga D, eds. Proceedings ...
    #
    # 줄바꿈 때문에 author/year가 서로 다른 줄에 존재하더라도
    # 전체 chunk를 기준으로 잡을 수 있게 별도로 검사한다.
    # ========================================================

    compact_text = " ".join(lines)

    strong_author_entry = bool(
        re.search(
            r"\b[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4}"
            r"\s+(?:and|&)\s+"
            r"[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4}\.",
            compact_text,
        )
    )

    multi_author_entry = bool(
        re.search(
            r"\b[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4}\s*,\s*"
            r"[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,4}",
            compact_text,
        )
    )

    proceedings_or_journal = any(
        signal in normalized
        for signal in [
            "proceedings",
            "journal of ",
            "international journal",
            "conference",
            "transactions on ",
        ]
    )

    editorial_signal = any(
        signal in normalized
        for signal in [
            "in:",
            "eds.",
            " ed.",
        ]
    )

    # 저자 형태 + 출판정보 + 연도/페이지 중 여러 신호가 함께 있으면
    # 단일 bibliography entry일 가능성이 높다.
    single_reference_entry_score = 0

    if strong_author_entry or multi_author_entry:
        single_reference_entry_score += 2

    if proceedings_or_journal:
        single_reference_entry_score += 2

    if editorial_signal:
        single_reference_entry_score += 1

    if year_count >= 1:
        single_reference_entry_score += 1

    if volume_page_count >= 1:
        single_reference_entry_score += 1

    if doi_count >= 1:
        single_reference_entry_score += 1

    if (
        single_reference_entry_score >= 5
        and (
            strong_author_entry
            or multi_author_entry
        )
        and proceedings_or_journal
    ):
        return (
            True,
            "single_reference_entry:"
            f"score={single_reference_entry_score}",
        )

    # ========================================================
    # 5. Reference density 점수
    # ========================================================

    reference_score = 0

    if year_count >= 5:
        reference_score += 2
    elif year_count >= 3:
        reference_score += 1

    if doi_count >= 2:
        reference_score += 2
    elif doi_count == 1:
        reference_score += 1

    if url_count >= 3:
        reference_score += 2
    elif url_count >= 1:
        reference_score += 1

    if et_al_count >= 3:
        reference_score += 2
    elif et_al_count >= 1:
        reference_score += 1

    if publication_signal_count >= 3:
        reference_score += 2
    elif publication_signal_count >= 1:
        reference_score += 1

    if volume_page_count >= 3:
        reference_score += 2
    elif volume_page_count >= 1:
        reference_score += 1

    # 정상 본문에도 [1], [2] citation은 흔하므로
    # citation 자체에는 낮은 가중치만 부여한다.
    if bracket_citation_count >= 8:
        reference_score += 1

    if bibliography_like_lines >= 3:
        reference_score += 2

    if bibliography_ratio >= 0.50:
        reference_score += 3
    elif bibliography_ratio >= 0.30:
        reference_score += 2

    if author_year_line_count >= 3:
        reference_score += 2

    if author_year_ratio >= 0.40:
        reference_score += 2

    if reference_number_line_count >= 4:
        reference_score += 1

    # ========================================================
    # 6. 최종 판정
    # ========================================================

    if (
        bibliography_like_lines >= 3
        and bibliography_ratio >= 0.40
    ):
        return (
            True,
            "bibliography_density:"
            f"lines={bibliography_like_lines}/{line_count},"
            f"ratio={bibliography_ratio:.2f}",
        )

    if (
        author_year_line_count >= 3
        and (
            publication_signal_count >= 1
            or volume_page_count >= 2
            or doi_count >= 1
        )
    ):
        return (
            True,
            "author_year_reference_density:"
            f"author_year_lines={author_year_line_count},"
            f"score={reference_score}",
        )

    if (
        reference_score >= 8
        and (
            bibliography_like_lines >= 2
            or author_year_line_count >= 2
        )
    ):
        return (
            True,
            f"reference_score:{reference_score}",
        )

    return False, "accepted"


def is_noise_chunk(
    text: str,
) -> bool:
    """
    기존 호출부와의 호환성을 유지하는 wrapper.
    """

    is_noise, _ = analyze_noise_chunk(
        text
    )

    return is_noise

# ============================================================
# Query Category 판별
# ============================================================

def detect_query_category(
    query: str,
) -> str | None:
    """
    query의 키워드를 분석해
    우선 검색할 RAG category를 결정한다.
    """

    normalized = query.lower()

    cafe_keywords = [
        "카페",
        "커피",
        "텀블러",
        "다회용컵",
        "다회용 컵",
        "일회용컵",
        "일회용 컵",
        "개인컵",
        "개인 컵",
        "테이크아웃",
        "커피전문점",
        "커피 전문점",
        "에스프레소",
        "라떼",
        "카푸치노",
        "coffee",
        "cafe",
        "tumbler",
        "reusable cup",
        "single-use cup",
        "single use cup",
        "takeaway",
        "espresso",
        "latte",
        "cappuccino",
    ]

    food_keywords = [
        "식품",
        "음식",
        "식단",
        "식생활",
        "식재료",
        "육류",
        "고기",
        "소고기",
        "쇠고기",
        "돼지고기",
        "닭고기",
        "햄",
        "소시지",
        "가공육",
        "축산물",
        "농축산물",
        "채식",
        "비건",
        "콩",
        "콩류",
        "두부",
        "단백질",
        "식물성",
        "대체육",
        "우유",
        "유제품",
        "식물성 우유",
        "음식물 쓰레기",
        "음식물쓰레기",
        "식품 폐기",
        "식품폐기",
        "배달음식",
        "배달 음식",
        "카놀라유",
        "식용유",
        "식물성기름",
        "식물성 기름",
        "food",
        "diet",
        "meat",
        "beef",
        "pork",
        "chicken",
        "vegetarian",
        "vegan",
        "plant-based",
        "plant based",
        "protein",
        "dairy",
        "milk",
        "food waste",
        "canola oil",
        "rapeseed oil",
        "vegetable oil",
    ]

    eco_keywords = [
        "일상생활",
        "일상 생활",
        "생활습관",
        "생활 습관",
        "친환경 행동",
        "친환경 생활",
        "탄소중립",
        "탄소 절감 행동",
        "탄소감축 행동",
        "탄소 감축 행동",
        "소비 습관",
        "소비습관",
        "에너지",
        "전기",
        "난방",
        "냉방",
        "자동차",
        "대중교통",
        "교통",
        "자전거",
        "도보",
        "재활용",
        "재사용",
        "중고",
        "절약",
        "lifestyle",
        "household",
        "transport",
        "energy",
        "recycling",
        "reuse",
        "carbon reduction",
    ]

    scores = {
        "cafe": 0,
        "food": 0,
        "echo_guide": 0,
    }

    for keyword in cafe_keywords:
        if keyword in normalized:
            scores["cafe"] += 1

    for keyword in food_keywords:
        if keyword in normalized:
            scores["food"] += 1

    for keyword in eco_keywords:
        if keyword in normalized:
            scores["echo_guide"] += 1

    highest_category = max(
        scores,
        key=scores.get,
    )

    if scores[highest_category] == 0:
        return None

    return highest_category

# ============================================================
# Cafe 세부 Intent 판별
# ============================================================

def detect_cafe_intent(query: str) -> str:
    """Cafe 질문을 검색 목적별로 분류한다.

    반환값:
    - cup: 텀블러/다회용컵/일회용컵
    - milk: 우유/식물성 대체유/라떼
    - coffee_product: 원두/생산/유통/LCA/커피 자체 탄소발자국
    - cafe_behavior: 카페 이용 시 친환경 소비 행동
    - general: 그 외 cafe 질문
    """
    normalized = query.lower()

    intent_keywords = {
        "cup": [
            "텀블러", "개인컵", "개인 컵", "다회용컵", "다회용 컵",
            "일회용컵", "일회용 컵", "테이크아웃컵", "테이크아웃 컵",
            "reusable cup", "single-use cup", "single use cup",
            "disposable cup", "tumbler",
        ],
        "milk": [
            "우유", "대체유", "식물성 우유", "식물성 음료", "오트",
            "귀리우유", "귀리 우유", "두유", "아몬드", "라떼",
            "milk", "dairy", "plant-based milk", "plant based milk",
            "oat milk", "soy milk", "almond milk", "latte",
        ],
        "coffee_product": [
            "원두", "커피콩", "커피 콩", "생산", "재배", "가공", "로스팅",
            "유통", "운송", "공급망", "전과정", "lca", "탄소발자국",
            "탄소 발자국", "coffee bean", "coffee production",
            "coffee processing", "coffee roasting", "coffee transport",
            "coffee supply chain", "life cycle assessment", "coffee carbon footprint",
        ],
        "cafe_behavior": [
            "카페 이용", "카페를 이용", "카페에서", "커피전문점", "커피 전문점",
            "친환경 소비", "친환경 행동", "실천", "소비 습관", "소비습관",
            "어떻게 이용", "환경을 위해", "sustainable cafe",
            "consumer behavior", "sustainable consumption",
        ],
    }

    # 구체적인 제품/수단 질문을 일반 행동 질문보다 우선한다.
    for intent in ("cup", "milk", "coffee_product", "cafe_behavior"):
        if any(keyword in normalized for keyword in intent_keywords[intent]):
            return intent

    return "general"



# ============================================================
# Food 세부 Intent 판별
# ============================================================

def detect_food_intent(query: str) -> str:
    """Food 질문을 검색 목적별로 분류한다.

    반환값:
    - meat_substitution: 육류를 다른 식품으로 대체
    - food_waste: 음식물/식품 폐기 감축
    - produce: 과일·채소·농산물의 저탄소 구매
    - general_food: 그 외 식품 소비 질문
    """
    normalized = query.lower()

    waste_terms = [
        "음식물 쓰레기", "음식물쓰레기", "식품 폐기", "식품폐기",
        "남은 음식", "잔반", "food waste", "food loss",
    ]
    produce_terms = [
        "과일", "채소", "농산물", "제철", "지역산", "로컬푸드",
        "local food", "seasonal food", "fruit", "vegetable", "produce",
    ]
    meat_terms = [
        "소고기", "쇠고기", "돼지고기", "닭고기", "육류", "고기",
        "붉은 고기", "가공육", "beef", "pork", "meat",
    ]
    substitution_terms = [
        "대체", "대신", "바꾸", "대체육", "식물성", "콩", "두부",
        "substitut", "alternative", "replacement", "plant-based", "legume",
    ]

    if any(term in normalized for term in waste_terms):
        return "food_waste"
    if any(term in normalized for term in produce_terms):
        return "produce"
    if (
        any(term in normalized for term in meat_terms)
        and any(term in normalized for term in substitution_terms)
    ):
        return "meat_substitution"
    if any(term in normalized for term in meat_terms):
        return "meat_substitution"

    return "general_food"


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    normalized = text.lower()
    return sum(1 for keyword in keywords if keyword in normalized)


def calculate_cafe_behavior_adjustment(text: str, cafe_intent: str | None):
    """카페 행동 질문에서 소비자가 직접 실천할 수 있는 근거를 우대한다."""
    if cafe_intent != "cafe_behavior":
        return 0.0, []

    action_keywords = [
        "텀블러", "개인컵", "개인 컵", "다회용컵", "다회용 컵",
        "일회용컵", "일회용 컵", "재사용", "반납", "세척", "줄이",
        "사용", "선택", "이용", "reusable", "reuse", "single-use",
        "consumer", "behavior",
    ]
    consumer_action_keywords = [
        "개인텀블러", "개인 텀블러", "개인컵", "개인 컵", "다회용컵",
        "다회용 컵", "반납", "재사용", "소비자", "고객", "이용자",
        "reusable cup", "personal cup", "consumer",
    ]
    evidence_keywords = [
        "탄소", "온실가스", "환경", "감축", "감소", "저감", "효과",
        "carbon", "greenhouse gas", "emission", "environmental", "lca",
    ]
    weak_keywords = [
        "인식조사", "설문조사", "인터뷰", "연구방법", "연구 방법",
        "가설", "회귀분석", "분석방법",
    ]
    business_policy_keywords = [
        "점주", "매장 운영", "인건비", "세금", "세제", "지원금",
        "사업자", "프랜차이즈", "정책", "제도", "보조금",
    ]

    action_hits = _count_keyword_hits(text, action_keywords)
    consumer_hits = _count_keyword_hits(text, consumer_action_keywords)
    evidence_hits = _count_keyword_hits(text, evidence_keywords)
    weak_hits = _count_keyword_hits(text, weak_keywords)
    business_hits = _count_keyword_hits(text, business_policy_keywords)

    adjustment = 0.0
    reasons = []

    if action_hits >= 3:
        adjustment += 0.055
        reasons.append(f"action+0.055({action_hits})")
    elif action_hits == 2:
        adjustment += 0.040
        reasons.append("action+0.040(2)")
    elif action_hits == 1:
        adjustment += 0.022
        reasons.append("action+0.022(1)")
    else:
        adjustment -= 0.025
        reasons.append("no_action-0.025")

    if consumer_hits >= 2:
        adjustment += 0.030
        reasons.append(f"consumer_action+0.030({consumer_hits})")
    elif consumer_hits == 1:
        adjustment += 0.018
        reasons.append("consumer_action+0.018(1)")

    if action_hits >= 1 and evidence_hits >= 2:
        adjustment += 0.025
        reasons.append(f"evidence+0.025({evidence_hits})")
    elif evidence_hits >= 1:
        adjustment += 0.010
        reasons.append(f"evidence+0.010({evidence_hits})")

    if business_hits >= 3 and consumer_hits == 0:
        adjustment -= 0.040
        reasons.append(f"business_policy-0.040({business_hits})")
    elif business_hits >= 2 and consumer_hits == 0:
        adjustment -= 0.025
        reasons.append(f"business_policy-0.025({business_hits})")

    if weak_hits >= 2 and action_hits == 0:
        adjustment -= 0.045
        reasons.append(f"weak-0.045({weak_hits})")
    elif weak_hits >= 1 and action_hits == 0:
        adjustment -= 0.025
        reasons.append(f"weak-0.025({weak_hits})")

    adjustment = max(-0.08, min(adjustment, 0.085))
    return adjustment, reasons


def calculate_food_adjustment(text: str, food_intent: str | None):
    """Food 질문의 세부 intent에 직접 답하는 chunk를 우대한다."""
    if not food_intent:
        return 0.0, []

    normalized = text.lower()
    adjustment = 0.0
    reasons = []

    # 보고서 표지/서지성 페이지는 의미 유사도가 높더라도 답변 근거로 약하다.
    cover_keywords = [
        "연구책임자", "연구진", "발간등록번호", "isbn",
        "research report", "an analysis of", "저자",
    ]
    cover_hits = _count_keyword_hits(normalized, cover_keywords)
    if cover_hits >= 2:
        adjustment -= 0.060
        reasons.append(f"cover-0.060({cover_hits})")

    if food_intent == "meat_substitution":
        meat_keywords = [
            "소고기", "쇠고기", "육류", "고기", "beef", "meat",
            "animal protein", "animal-based",
        ]
        alternative_keywords = [
            "대체", "대신", "콩", "콩류", "두부", "식물성", "대체육",
            "legume", "pulses", "tofu", "plant-based", "plant based",
            "meat substitute", "meat alternative", "replacement", "nuts",
        ]
        meat_hits = _count_keyword_hits(normalized, meat_keywords)
        alt_hits = _count_keyword_hits(normalized, alternative_keywords)
        if meat_hits >= 1 and alt_hits >= 2:
            adjustment += 0.075
            reasons.append(f"meat_substitution+0.075({meat_hits},{alt_hits})")
        elif alt_hits >= 1:
            adjustment += 0.045
            reasons.append(f"meat_alternative+0.045({alt_hits})")

    elif food_intent == "food_waste":
        waste_keywords = [
            "음식물 쓰레기", "음식물쓰레기", "식품 폐기", "식품폐기",
            "식품 폐기물", "food waste", "food loss", "waste reduction",
            "폐기 감축", "폐기 감소",
        ]
        quantitative_keywords = [
            "co2-eq", "co2eq", "온실가스 감축량", "감축할 수", "감량",
            "%", "톤co2", "ton co2", "reduction",
        ]
        waste_hits = _count_keyword_hits(normalized, waste_keywords)
        quantitative_hits = _count_keyword_hits(normalized, quantitative_keywords)
        if waste_hits >= 2:
            adjustment += 0.055
            reasons.append(f"food_waste+0.055({waste_hits})")
        elif waste_hits == 1:
            adjustment += 0.035
            reasons.append("food_waste+0.035(1)")
        if waste_hits >= 1 and quantitative_hits >= 2:
            adjustment += 0.030
            reasons.append(f"waste_evidence+0.030({quantitative_hits})")

    elif food_intent == "produce":
        produce_keywords = [
            "과일", "채소", "농산물", "fruit", "vegetable", "produce",
        ]
        purchase_action_keywords = [
            "제철", "지역산", "지역 농산물", "로컬푸드", "가까운 곳",
            "푸드 마일리지", "푸드마일리지", "운송거리", "수송", "운반",
            "저탄소 농산물", "저탄소 식품", "local food", "locally produced",
            "seasonal", "food miles", "transport distance", "consumer choice",
        ]
        carbon_keywords = [
            "탄소", "온실가스", "이산화탄소", "배출", "저탄소",
            "carbon", "greenhouse gas", "emission",
        ]
        produce_hits = _count_keyword_hits(normalized, produce_keywords)
        action_hits = _count_keyword_hits(normalized, purchase_action_keywords)
        carbon_hits = _count_keyword_hits(normalized, carbon_keywords)
        if action_hits >= 2:
            adjustment += 0.065
            reasons.append(f"produce_action+0.065({action_hits})")
        elif action_hits == 1:
            adjustment += 0.040
            reasons.append("produce_action+0.040(1)")
        if produce_hits >= 1 and action_hits >= 1:
            adjustment += 0.020
            reasons.append(f"produce_match+0.020({produce_hits})")
        if action_hits >= 1 and carbon_hits >= 1:
            adjustment += 0.015
            reasons.append(f"produce_carbon+0.015({carbon_hits})")

    adjustment = max(-0.08, min(adjustment, 0.095))
    return adjustment, reasons

# ============================================================
# English Retrieval Query 생성
# ============================================================

def build_english_retrieval_query(
    query: str,
    category: str | None,
) -> str:
    """
    한국어 query를 영어 RAG 논문 검색에 적합한 형태로 확장한다.

    핵심 전략:
    1. 품목/대상 키워드를 영어 개념으로 변환
    2. 질문의 행동/감축 의도를 영어 검색어로 변환
    3. food 질문은 세부 주제별 retrieval term을 추가
    4. 모든 food 질문에 동일한 범용 LCA 키워드를
       과도하게 추가하지 않는다.
    5. 영어 논문과 한국어 논문을 모두 검색할 수 있도록
       기존 Dual Query 구조와 함께 사용한다.
    """

    normalized = query.lower()

    # ========================================================
    # 1. 품목 / 대상 Keyword Mapping
    # ========================================================

    keyword_map = {

        # ----------------------------------------------------
        # Food - 육류
        # ----------------------------------------------------

        "소고기": [
            "beef",
            "red meat",
            "animal protein",
        ],

        "쇠고기": [
            "beef",
            "red meat",
            "animal protein",
        ],

        "돼지고기": [
            "pork",
            "meat",
            "animal protein",
        ],

        "닭고기": [
            "chicken",
            "poultry",
            "animal protein",
        ],

        "육류": [
            "meat",
            "animal-based food",
            "animal protein",
        ],

        "고기": [
            "meat",
            "animal-based food",
        ],

        "붉은 고기": [
            "red meat",
            "beef",
        ],

        "가공육": [
            "processed meat",
            "meat consumption",
        ],

        # ----------------------------------------------------
        # Food - 식물성 단백질 / 대체식품
        # ----------------------------------------------------

        "콩류": [
            "pulses",
            "legumes",
            "plant-based protein",
        ],

        "콩": [
            "pulses",
            "legumes",
            "plant-based protein",
        ],

        "두부": [
            "tofu",
            "soy",
            "plant-based protein",
        ],

        "식물성 단백질": [
            "plant-based protein",
            "plant protein",
        ],

        "대체육": [
            "meat substitute",
            "plant-based meat",
            "meat alternative",
        ],

        "채식": [
            "plant-based diet",
            "vegetarian diet",
        ],

        "비건": [
            "vegan diet",
            "plant-based diet",
        ],

        # ----------------------------------------------------
        # Food - 유제품
        # ----------------------------------------------------

        "우유": [
            "milk",
            "dairy",
        ],

        "유제품": [
            "dairy",
            "dairy products",
        ],

        "식물성 우유": [
            "plant-based milk",
            "dairy alternative",
        ],

        # ----------------------------------------------------
        # Food - 식용유
        # ----------------------------------------------------

        "카놀라유": [
            "canola oil",
            "rapeseed oil",
            "vegetable oil",
            "edible oil",
        ],

        "식물성기름": [
            "vegetable oil",
            "plant oil",
            "edible oil",
        ],

        "식물성 기름": [
            "vegetable oil",
            "plant oil",
            "edible oil",
        ],

        "식용유": [
            "cooking oil",
            "vegetable oil",
            "edible oil",
        ],

        # ----------------------------------------------------
        # Food - 과일 / 채소
        # ----------------------------------------------------

        "과일": [
            "fruit",
            "fruit consumption",
        ],

        "채소": [
            "vegetables",
            "vegetable consumption",
        ],

        "농산물": [
            "agricultural products",
            "food products",
        ],

        "지역 농산물": [
            "local food",
            "locally produced food",
            "local agricultural products",
        ],

        "제철": [
            "seasonal food",
            "seasonal produce",
        ],

        # ----------------------------------------------------
        # Food - 가공식품
        # ----------------------------------------------------

        "가공식품": [
            "processed food",
            "processed food consumption",
        ],

        "가공 식품": [
            "processed food",
            "processed food consumption",
        ],

        # ----------------------------------------------------
        # Food - 음식물 폐기
        # ----------------------------------------------------

        "음식물 쓰레기": [
            "food waste",
            "household food waste",
        ],

        "음식물쓰레기": [
            "food waste",
            "household food waste",
        ],

        "식품 폐기": [
            "food waste",
            "food loss",
        ],

        "식품폐기": [
            "food waste",
            "food loss",
        ],

        "남은 음식": [
            "food leftovers",
            "food waste prevention",
        ],

        # ----------------------------------------------------
        # Food - 식생활 / 구매
        # ----------------------------------------------------

        "식생활": [
            "dietary behavior",
            "food consumption behavior",
        ],

        "식단": [
            "diet",
            "dietary pattern",
        ],

        "식품": [
            "food consumption",
        ],

        "음식": [
            "food consumption",
        ],

        "구매": [
            "consumer choice",
            "purchasing behavior",
        ],

        "마트": [
            "food purchasing",
            "consumer food choice",
        ],

        # ----------------------------------------------------
        # Cafe
        # ----------------------------------------------------

        "커피": [
            "coffee",
            "coffee consumption",
        ],

        "에스프레소": [
            "espresso",
            "coffee",
        ],

        "라떼": [
            "latte",
            "coffee",
        ],

        "텀블러": [
            "reusable cup",
            "tumbler",
        ],

        "개인컵": [
            "reusable cup",
            "personal cup",
        ],

        "개인 컵": [
            "reusable cup",
            "personal cup",
        ],

        "일회용컵": [
            "single-use cup",
            "disposable cup",
        ],

        "일회용 컵": [
            "single-use cup",
            "disposable cup",
        ],

        "다회용컵": [
            "reusable cup",
        ],

        "다회용 컵": [
            "reusable cup",
        ],

        # ----------------------------------------------------
        # Eco Guide
        # ----------------------------------------------------

        "자동차": [
            "car",
            "private vehicle",
        ],

        "대중교통": [
            "public transport",
            "public transportation",
        ],

        "자전거": [
            "bicycle",
            "cycling",
        ],

        "도보": [
            "walking",
        ],

        "재활용": [
            "recycling",
        ],

        "재사용": [
            "reuse",
            "reusable",
        ],

        "전기": [
            "electricity consumption",
        ],

        "에너지": [
            "energy consumption",
        ],

        "난방": [
            "heating",
            "household energy",
        ],

        "냉방": [
            "cooling",
            "household energy",
        ],
    }

    # ========================================================
    # 2. 질문 의도 Mapping
    # ========================================================

    intent_map = {

        "탄소배출": [
            "carbon emissions",
            "carbon footprint",
            "greenhouse gas emissions",
        ],

        "탄소 배출": [
            "carbon emissions",
            "carbon footprint",
            "greenhouse gas emissions",
        ],

        "탄소발자국": [
            "carbon footprint",
        ],

        "탄소 발자국": [
            "carbon footprint",
        ],

        "온실가스": [
            "greenhouse gas emissions",
        ],

        "줄이": [
            "emission reduction",
            "carbon reduction",
            "mitigation",
        ],

        "감소": [
            "emission reduction",
            "reduction potential",
        ],

        "감축": [
            "emission reduction",
            "mitigation",
            "reduction potential",
        ],

        "대신": [
            "substitution",
            "alternative",
            "replacement",
        ],

        "대체": [
            "substitution",
            "alternative",
            "replacement",
        ],

        "바꾸": [
            "dietary substitution",
            "replacement",
        ],

        "환경": [
            "environmental impact",
        ],

        "친환경": [
            "sustainable consumption",
            "environmental impact",
        ],

        "효과": [
            "environmental impact",
            "reduction potential",
        ],

        "선택": [
            "consumer choice",
            "sustainable food choice",
        ],

        "습관": [
            "consumer behavior",
            "behavior change",
        ],

        "실천": [
            "behavior change",
            "carbon reduction actions",
        ],
    }

    english_terms = []

    # ========================================================
    # 3. 기본 Keyword Mapping 적용
    # ========================================================

    for keyword, terms in keyword_map.items():

        if keyword in normalized:
            english_terms.extend(terms)

    for keyword, terms in intent_map.items():

        if keyword in normalized:
            english_terms.extend(terms)

    # ========================================================
    # 4. FOOD 세부 Intent Expansion
    # ========================================================

    if category == "food":

        # ----------------------------------------------------
        # A. 육류 소비 감축
        # ----------------------------------------------------

        meat_terms = [
            "소고기",
            "쇠고기",
            "돼지고기",
            "닭고기",
            "육류",
            "고기",
            "붉은 고기",
            "가공육",
        ]

        if any(
            term in normalized
            for term in meat_terms
        ):
            english_terms.extend([
                "meat consumption reduction",
                "dietary shift",
                "low-carbon diet",
                "plant-based substitution",
                "greenhouse gas mitigation",
            ])

        # ----------------------------------------------------
        # B. 식물성 단백질 / 대체육
        # ----------------------------------------------------

        plant_protein_terms = [
            "식물성 단백질",
            "콩",
            "콩류",
            "두부",
            "대체육",
            "채식",
            "비건",
        ]

        if any(
            term in normalized
            for term in plant_protein_terms
        ):
            english_terms.extend([
                "plant-based protein",
                "protein substitution",
                "animal protein replacement",
                "dietary transition",
                "environmental benefits",
            ])

        # ----------------------------------------------------
        # C. 식용유 / 카놀라유
        # ----------------------------------------------------

        oil_terms = [
            "카놀라유",
            "식용유",
            "식물성기름",
            "식물성 기름",
        ]

        if any(
            term in normalized
            for term in oil_terms
        ):
            english_terms.extend([
                "edible oil consumption",
                "vegetable oil environmental impact",
                "oil production carbon footprint",
                "sustainable oil consumption",
                "food carbon footprint",
            ])

        # ----------------------------------------------------
        # D. 과일 / 채소 / 지역 / 제철
        # ----------------------------------------------------

        produce_terms = [
            "과일",
            "채소",
            "농산물",
            "제철",
            "지역 농산물",
        ]

        if any(
            term in normalized
            for term in produce_terms
        ):
            english_terms.extend([
                "fruit and vegetable consumption",
                "seasonal food",
                "local food",
                "sustainable food consumption",
                "food supply chain emissions",
            ])

        # ----------------------------------------------------
        # E. 가공식품
        # ----------------------------------------------------

        processed_food_terms = [
            "가공식품",
            "가공 식품",
        ]

        if any(
            term in normalized
            for term in processed_food_terms
        ):
            english_terms.extend([
                "processed food environmental impact",
                "processed food consumption",
                "food processing emissions",
                "sustainable food choice",
            ])

        # ----------------------------------------------------
        # F. 음식물 쓰레기 / 폐기
        # ----------------------------------------------------

        waste_terms = [
            "음식물 쓰레기",
            "음식물쓰레기",
            "식품 폐기",
            "식품폐기",
            "남은 음식",
            "버리지",
        ]

        if any(
            term in normalized
            for term in waste_terms
        ):
            english_terms.extend([
                "food waste reduction",
                "food waste prevention",
                "household food waste",
                "greenhouse gas reduction",
                "consumer food waste behavior",
            ])

        # ----------------------------------------------------
        # G. 일반적인 저탄소 식생활 / 소비 행동
        # ----------------------------------------------------

        lifestyle_food_terms = [
            "식생활",
            "식단",
            "소비 습관",
            "소비습관",
            "장을 볼",
            "마트",
            "구매",
            "식품을 선택",
        ]

        if any(
            term in normalized
            for term in lifestyle_food_terms
        ):
            english_terms.extend([
                "sustainable diet",
                "low-carbon diet",
                "sustainable food consumption",
                "consumer food choice",
                "dietary behavior change",
                "carbon reduction actions",
            ])

        # ----------------------------------------------------
        # Food fallback
        #
        # 구체적인 food 의도가 전혀 잡히지 않았을 때만
        # 범용 키워드를 추가한다.
        # ----------------------------------------------------

        specific_food_signals = (
            meat_terms
            + plant_protein_terms
            + oil_terms
            + produce_terms
            + processed_food_terms
            + waste_terms
            + lifestyle_food_terms
        )

        has_specific_food_intent = any(
            term in normalized
            for term in specific_food_signals
        )

        if not has_specific_food_intent:
            english_terms.extend([
                "food consumption",
                "sustainable food consumption",
                "carbon footprint",
            ])

    # ========================================================
    # 5. CAFE Intent Expansion
    # ========================================================

    elif category == "cafe":

        cafe_intent = detect_cafe_intent(query)

        if cafe_intent == "cup":
            english_terms.extend([
                "reusable cup",
                "single-use cup reduction",
                "disposable cup reduction",
                "reusable cup life cycle assessment",
                "environmental payback reusable cup",
                "consumer reusable cup behavior",
                "carbon footprint reduction",
            ])

        elif cafe_intent == "milk":
            english_terms.extend([
                "dairy milk",
                "plant-based milk",
                "oat milk",
                "soy milk",
                "almond milk",
                "milk alternatives",
                "life cycle assessment milk alternatives",
                "greenhouse gas emissions milk",
                "low-carbon beverage choice",
            ])

        elif cafe_intent == "coffee_product":
            english_terms.extend([
                "coffee carbon footprint",
                "coffee life cycle assessment",
                "coffee production emissions",
                "coffee processing emissions",
                "coffee supply chain",
                "coffee transportation",
                "sustainable coffee production",
            ])

        elif cafe_intent == "cafe_behavior":
            english_terms.extend([
                "sustainable cafe behavior",
                "sustainable consumption behavior",
                "reusable cup behavior",
                "single-use cup reduction",
                "plant-based milk choice",
                "low-carbon beverage choice",
                "consumer behavior change",
                "carbon reduction actions",
            ])

        else:
            # 일반 cafe 질문에는 특정 커피 생산 LCA 논문으로
            # 검색이 과도하게 쏠리지 않도록 행동/선택 중심 용어를 사용한다.
            english_terms.extend([
                "sustainable cafe consumption",
                "sustainable coffee consumption behavior",
                "reusable cup",
                "low-carbon beverage choice",
                "consumer behavior",
                "environmental impact",
            ])

    # ========================================================
    # 6. ECO GUIDE Intent Expansion
    # ========================================================

    elif category == "echo_guide":

        english_terms.extend([
            "sustainable lifestyle",
            "carbon reduction actions",
            "behavior change",
            "household carbon footprint",
        ])

       # ========================================================
    # 7. 중복 제거
    # ========================================================

    unique_terms = list(
        dict.fromkeys(
            english_terms
        )
    )

    # ========================================================
    # 8. 영어 Query 생성
    # ========================================================

    return " ".join(
        unique_terms
    )


# ============================================================
# Cosine Similarity
# ============================================================

def cosine_similarity(
    vector_a,
    vector_b,
) -> float:
    """
    두 embedding vector의 cosine similarity를 계산한다.
    """

    vector_a = np.asarray(
        vector_a,
        dtype=np.float32,
    )

    vector_b = np.asarray(
        vector_b,
        dtype=np.float32,
    )

    denominator = (
        np.linalg.norm(vector_a)
        * np.linalg.norm(vector_b)
    )

    if denominator == 0:
        return 0.0

    return float(
        np.dot(
            vector_a,
            vector_b,
        )
        / denominator
    )


# ============================================================
# 후보 문서 전처리
# ============================================================

def prepare_candidate_documents(
    docs,
):
    """
    reranking 전에:

    1. noise 제거
    2. 완전히 동일한 chunk 제거
    3. 동일 source + page 반복 제거

    동일 PDF의 서로 다른 페이지는 허용한다.
    """

    filtered_docs = []

    used_chunks = set()
    used_source_pages = set()

    for doc in docs:

        is_noise, noise_reason = (
            analyze_noise_chunk(
                doc.page_content
            )
        )

        if is_noise:
            source = doc.metadata.get(
                "source"
            )

            page = doc.metadata.get(
                "page"
            )

            preview = (
                doc.page_content
                .replace("\n", " ")
                .strip()[:160]
            )

            print(
                "\n[RAG FILTER] removed"
                f"\nreason={noise_reason}"
                f"\nsource={source}"
                f"\npage={page}"
                f"\npreview={preview}"
            )

            continue

        source = doc.metadata.get(
            "source"
        )

        page = doc.metadata.get(
            "page"
        )

        normalized_content = (
            doc.page_content
            .strip()
        )

        chunk_key = (
            source,
            page,
            normalized_content,
        )

        if chunk_key in used_chunks:
            continue

        source_page_key = (
            source,
            page,
        )

        if source_page_key in used_source_pages:
            continue

        used_chunks.add(
            chunk_key
        )

        used_source_pages.add(
            source_page_key
        )

        filtered_docs.append(
            doc
        )

    return filtered_docs


# ============================================================
# 3차 개선: Hybrid Retrieval (BM25 + RRF)
# ============================================================

def _tokenize_for_bm25(text: str) -> list[str]:
    """한국어/영어/숫자를 함께 처리하는 가벼운 BM25 tokenizer."""
    if not text:
        return []
    normalized = text.lower()
    return re.findall(r"[가-힣]+|[a-z0-9]+(?:[-_.][a-z0-9]+)*", normalized)


def _document_key(doc) -> tuple:
    """검색 경로가 달라도 같은 chunk를 안정적으로 식별한다."""
    return (
        doc.metadata.get("source"),
        doc.metadata.get("page"),
        doc.page_content.strip(),
    )


def get_category_documents(vectorstore, category: str | None):
    """BM25 검색을 위해 Chroma에서 대상 category의 전체 chunk를 읽는다."""
    where = {"category": category} if category else None
    raw = vectorstore.get(
        where=where,
        include=["documents", "metadatas"],
    )

    documents = raw.get("documents") or []
    metadatas = raw.get("metadatas") or []

    try:
        from langchain_core.documents import Document
    except ImportError:
        from langchain.schema import Document

    docs = []
    for content, metadata in zip(documents, metadatas):
        if not content:
            continue
        docs.append(
            Document(
                page_content=content,
                metadata=metadata or {},
            )
        )
    return docs


def bm25_search_documents(
    docs,
    query: str,
    k: int,
):
    """외부 BM25 패키지 없이 후보 corpus에서 lexical 검색을 수행한다."""
    if not docs or not query.strip() or k <= 0:
        return []

    query_tokens = _tokenize_for_bm25(query)
    if not query_tokens:
        return []

    tokenized_docs = [_tokenize_for_bm25(doc.page_content) for doc in docs]
    lengths = [len(tokens) for tokens in tokenized_docs]
    avgdl = sum(lengths) / max(len(lengths), 1)
    if avgdl == 0:
        return []

    df = Counter()
    for tokens in tokenized_docs:
        df.update(set(tokens))

    n_docs = len(docs)
    query_counts = Counter(query_tokens)
    k1 = 1.5
    b = 0.75

    scored = []
    for doc, tokens, dl in zip(docs, tokenized_docs, lengths):
        if not tokens:
            continue

        tf = Counter(tokens)
        score = 0.0

        for term, qtf in query_counts.items():
            term_df = df.get(term, 0)
            if term_df == 0:
                continue

            idf = math.log(
                1.0 + (n_docs - term_df + 0.5) / (term_df + 0.5)
            )
            freq = tf.get(term, 0)
            if freq == 0:
                continue

            denominator = freq + k1 * (
                1.0 - b + b * dl / avgdl
            )
            score += (
                idf
                * (freq * (k1 + 1.0) / denominator)
                * min(qtf, 2)
            )

        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [doc for _, doc in scored[:k]]


def reciprocal_rank_fusion(
    ranked_lists,
    k: int,
    rrf_constant: int = 60,
):
    """Dense/lexical 검색 순위를 RRF로 통합하고 RRF 신호를 metadata에 보존한다."""
    scores = defaultdict(float)
    documents = {}
    appearances = defaultdict(int)

    for ranked_docs in ranked_lists:
        for rank, doc in enumerate(ranked_docs, start=1):
            key = _document_key(doc)
            documents[key] = doc
            scores[key] += 1.0 / (rrf_constant + rank)
            appearances[key] += 1

    ordered_keys = sorted(scores, key=scores.get, reverse=True)[:k]
    if not ordered_keys:
        return []

    max_rrf = max(scores[key] for key in ordered_keys) or 1.0
    fused_docs = []

    for rank, key in enumerate(ordered_keys, start=1):
        doc = documents[key]
        doc.metadata["_rag_rrf_score"] = float(scores[key])
        doc.metadata["_rag_rrf_norm"] = float(scores[key] / max_rrf)
        doc.metadata["_rag_rrf_rank"] = rank
        doc.metadata["_rag_retrieval_appearances"] = int(appearances[key])
        fused_docs.append(doc)

    return fused_docs


# ============================================================
# Near-Duplicate / Final Relevance Helpers
# ============================================================

def _normalize_for_duplicate(text: str) -> str:
    """파일명/페이지가 달라도 실질적으로 같은 chunk인지 비교하기 위한 정규화."""
    if not text:
        return ""
    normalized = text.lower()
    normalized = re.sub(r"https?://\S+|www\.\S+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^0-9a-z가-힣%]+", " ", normalized)
    return " ".join(normalized.split())


def _duplicate_fingerprint(text: str) -> str:
    normalized = _normalize_for_duplicate(text)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _token_set(text: str) -> set[str]:
    return set(_tokenize_for_bm25(_normalize_for_duplicate(text)))


def _near_duplicate_similarity(text_a: str, text_b: str) -> float:
    """서로 다른 source의 사실상 동일한 PDF chunk까지 탐지한다."""
    a = _normalize_for_duplicate(text_a)
    b = _normalize_for_duplicate(text_b)

    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 180:
        return 0.0

    seq_score = SequenceMatcher(None, a[:5000], b[:5000], autojunk=False).ratio()
    tokens_a = _token_set(a)
    tokens_b = _token_set(b)
    union = tokens_a | tokens_b
    jaccard = (len(tokens_a & tokens_b) / len(union)) if union else 0.0
    return max(seq_score, jaccard)


def remove_near_duplicate_scored_documents(
    ranked_documents,
    threshold: float = 0.88,
):
    """내용이 사실상 동일하면 더 높은 relevance 문서 하나만 유지한다."""
    unique = []
    fingerprints = set()

    for item in ranked_documents:
        doc = item["doc"]
        fingerprint = _duplicate_fingerprint(doc.page_content)

        if fingerprint in fingerprints:
            continue

        duplicate_of = None
        duplicate_similarity = 0.0

        for kept in unique:
            similarity = _near_duplicate_similarity(
                doc.page_content,
                kept["doc"].page_content,
            )
            if similarity >= threshold:
                duplicate_of = kept
                duplicate_similarity = similarity
                break

        if duplicate_of is not None:
            print(
                "[RAG DEDUP] removed near-duplicate "
                f"similarity={duplicate_similarity:.3f} "
                f"source={doc.metadata.get('source')} "
                f"page={doc.metadata.get('page')} "
                f"kept_source={duplicate_of['doc'].metadata.get('source')} "
                f"kept_page={duplicate_of['doc'].metadata.get('page')}"
            )
            continue

        fingerprints.add(fingerprint)
        unique.append(item)

    return unique


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _normalize_adjustment(adjustment: float) -> float:
    """기존 intent adjustment를 0~1 보조 신호로 변환한다."""
    return max(0.0, min(1.0, 0.5 + adjustment / 0.19))


# ============================================================
# 4차 개선: Query-aware Evidence Quality
# ============================================================

def detect_evidence_requirements(query: str) -> dict:
    """
    질문이 어떤 형태의 근거를 요구하는지 일반적으로 판별한다.

    특정 품목/주제를 하드코딩하지 않고 다음 요구를 탐지한다.
    - effect: 감소/감축/효과/영향 등 결과 근거
    - quantitative: 수치, 비율, CO2-eq 등 정량 근거
    - comparison: 대체/비교/대신 등 비교 근거
    - action: 실천/방법/어떻게 등 행동 근거
    """
    normalized = (query or "").lower()

    effect_terms = [
        "효과", "영향", "도움", "줄이", "감소", "감축", "저감",
        "배출", "탄소", "온실가스",
        "effect", "impact", "reduce", "reduction", "decrease",
        "emission", "carbon", "greenhouse gas", "mitigation",
    ]
    quantitative_terms = [
        "얼마", "얼마나", "몇", "비율", "수치", "정량", "%", "퍼센트",
        "kg", "톤", "co2", "co2-eq", "co2eq",
        "how much", "percent", "percentage", "quantitative",
    ]
    comparison_terms = [
        "대체", "대신", "비교", "바꾸", "보다", "차이",
        "alternative", "replacement", "substitut", "compare", "versus", " vs ",
    ]
    action_terms = [
        "어떻게", "방법", "실천", "행동", "습관", "선택", "해야",
        "how", "action", "behavior", "practice", "choice",
    ]

    return {
        "effect": any(term in normalized for term in effect_terms),
        "quantitative": any(term in normalized for term in quantitative_terms),
        "comparison": any(term in normalized for term in comparison_terms),
        "action": any(term in normalized for term in action_terms),
    }


def _find_term_positions(text: str, terms: list[str]) -> list[int]:
    """문자열 안에서 term들의 시작 위치를 모두 반환한다."""
    positions = []
    normalized = (text or "").lower()

    for term in terms:
        term = term.lower()
        start = 0
        while True:
            index = normalized.find(term, start)
            if index < 0:
                break
            positions.append(index)
            start = index + max(len(term), 1)

    return sorted(set(positions))


def _minimum_position_distance(
    positions_a: list[int],
    positions_b: list[int],
) -> int | None:
    """두 위치 집합 사이의 최소 문자 거리를 계산한다."""
    if not positions_a or not positions_b:
        return None

    i = 0
    j = 0
    minimum = None

    while i < len(positions_a) and j < len(positions_b):
        distance = abs(positions_a[i] - positions_b[j])
        minimum = distance if minimum is None else min(minimum, distance)

        if positions_a[i] < positions_b[j]:
            i += 1
        else:
            j += 1

    return minimum


def _extract_quantitative_spans(text: str) -> list[tuple[int, str]]:
    """정량 근거 후보의 위치와 문자열을 반환한다."""
    normalized = (text or "").lower()

    patterns = [
        r"\d+(?:\.\d+)?\s*%",
        r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:kg|g|t|ton|tons|톤)\s*(?:co2(?:-?eq)?|co₂(?:-?eq)?)?",
        r"\d+(?:\.\d+)?\s*(?:kgco2e|kgco2eq|tco2e|tco2eq|co2-eq|co2eq)",
        r"(?:약\s*)?\d+(?:\.\d+)?\s*(?:만|억)?\s*톤",
    ]

    spans = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            spans.append((match.start(), match.group(0)))

    return sorted(spans, key=lambda item: item[0])


def calculate_evidence_quality(
    query: str,
    text: str,
    category: str | None = None,
    cafe_intent: str | None = None,
    food_intent: str | None = None,
) -> tuple[float, list[str]]:
    """
    질문에 직접 답하는 근거의 품질을 0~1로 평가한다.

    핵심 개선:
    - 숫자가 chunk 어딘가에 존재한다는 이유만으로 높은 점수를 주지 않는다.
    - 질문 주제, 감축/효과 표현, 정량 수치가 서로 가까운 문맥에 있을 때
      Evidence Directness 보너스를 부여한다.
    - 특정 food_waste 문장 하나를 하드코딩하지 않고 일반적인 proximity를 사용한다.
    """
    query_norm = (query or "").lower()
    text_norm = (text or "").lower()
    requirements = detect_evidence_requirements(query)

    query_tokens = {
        token
        for token in _tokenize_for_bm25(query_norm)
        if len(token) >= 2
    }
    text_tokens = set(_tokenize_for_bm25(text_norm))
    overlap = len(query_tokens & text_tokens) / max(len(query_tokens), 1)

    score = min(overlap * 1.4, 0.24)
    reasons = [f"query_overlap={overlap:.3f}"]

    carbon_terms = [
        "탄소", "온실가스", "이산화탄소", "배출", "저탄소",
        "carbon", "greenhouse gas", "emission", "co2",
    ]
    reduction_terms = [
        "감축", "감소", "저감", "줄이", "감량", "절감", "감축량",
        "reduction", "reduce", "decrease", "mitigation", "saving",
    ]
    effect_terms = [
        "효과", "영향", "잠재량", "시나리오", "대안",
        "effect", "impact", "potential", "scenario", "alternative",
    ]
    action_terms = [
        "실천", "행동", "선택", "사용", "재사용", "대체", "소비자",
        "practice", "behavior", "choice", "reuse", "replace", "consumer",
    ]
    comparison_terms = [
        "대비", "비교", "대신", "대체", "보다",
        "compared", "versus", "alternative", "replacement", "substitut",
    ]

    # 질문의 내용어를 evidence topic anchor로 사용한다.
    stop_tokens = {
        "어떤", "어떻게", "얼마나", "도움", "대한", "관련", "기준",
        "what", "how", "does", "can", "the", "and", "for", "with",
    }
    topic_tokens = [
        token for token in query_tokens
        if token not in stop_tokens
        and token not in {"탄소배출", "탄소", "온실가스", "배출", "감축", "감소", "줄이면"}
    ]

    topic_positions = _find_term_positions(text_norm, topic_tokens)
    carbon_positions = _find_term_positions(text_norm, carbon_terms)
    reduction_positions = _find_term_positions(text_norm, reduction_terms)
    effect_positions = _find_term_positions(text_norm, effect_terms)
    quantitative_spans = _extract_quantitative_spans(text_norm)
    quantitative_positions = [position for position, _ in quantitative_spans]

    carbon_hits = len(carbon_positions)
    reduction_hits = len(reduction_positions)
    effect_hits = len(effect_positions)
    action_hits = _count_keyword_hits(text_norm, action_terms)
    comparison_hits = _count_keyword_hits(text_norm, comparison_terms)

    if requirements["effect"]:
        if carbon_hits >= 1 and reduction_hits >= 1:
            score += 0.18
            reasons.append("effect_carbon_reduction+0.18")
        elif reduction_hits >= 1 or effect_hits >= 1:
            score += 0.08
            reasons.append("effect_signal+0.08")

    # 단순 numeric count는 작은 기본 보너스만 준다.
    numeric_count = len(quantitative_spans)
    if numeric_count >= 1:
        numeric_bonus = 0.08 if numeric_count == 1 else 0.12
        score += numeric_bonus
        reasons.append(f"quantitative_presence+{numeric_bonus:.2f}({numeric_count})")

    # ------------------------------------------------------------
    # Evidence Directness
    # ------------------------------------------------------------
    # topic ↔ reduction/effect ↔ quantitative/carbon이 가까운 경우에만
    # 강한 정량 evidence 보너스를 부여한다.
    topic_to_reduction = _minimum_position_distance(
        topic_positions,
        reduction_positions + effect_positions,
    )
    topic_to_quant = _minimum_position_distance(
        topic_positions,
        quantitative_positions,
    )
    reduction_to_quant = _minimum_position_distance(
        reduction_positions + effect_positions,
        quantitative_positions,
    )
    reduction_to_carbon = _minimum_position_distance(
        reduction_positions + effect_positions,
        carbon_positions,
    )

    directness = 0.0

    if topic_to_reduction is not None:
        if topic_to_reduction <= 120:
            directness += 0.18
            reasons.append("topic_effect_near+0.18")
        elif topic_to_reduction <= 260:
            directness += 0.10
            reasons.append("topic_effect_mid+0.10")

    if reduction_to_carbon is not None:
        if reduction_to_carbon <= 120:
            directness += 0.14
            reasons.append("effect_carbon_near+0.14")
        elif reduction_to_carbon <= 260:
            directness += 0.07
            reasons.append("effect_carbon_mid+0.07")

    if quantitative_positions:
        # 가장 강한 조건: 주제와 수치, 감축/효과와 수치가 모두 가까움.
        if (
            topic_to_quant is not None
            and reduction_to_quant is not None
            and topic_to_quant <= 220
            and reduction_to_quant <= 160
        ):
            directness += 0.32
            reasons.append("direct_quantitative_evidence+0.32")
        elif (
            reduction_to_quant is not None
            and reduction_to_quant <= 160
        ):
            directness += 0.20
            reasons.append("effect_quantitative_near+0.20")
        elif (
            topic_to_quant is not None
            and topic_to_quant <= 220
        ):
            directness += 0.12
            reasons.append("topic_quantitative_near+0.12")

    # directness가 없는 숫자 다발은 표/요약의 우연한 수치일 가능성이 있으므로
    # 숫자 개수 자체로 추가 점수를 주지 않는다.
    score += min(directness, 0.50)

    if requirements["comparison"] and comparison_hits >= 1:
        score += 0.08
        reasons.append("comparison_evidence+0.08")

    if requirements["action"] and action_hits >= 1:
        score += 0.08
        reasons.append("action_evidence+0.08")

    intent_adjustment = 0.0

    if category == "cafe":
        intent_adjustment, _ = calculate_cafe_behavior_adjustment(
            text,
            cafe_intent,
        )
    elif category == "food":
        intent_adjustment, _ = calculate_food_adjustment(
            text,
            food_intent,
        )

    if intent_adjustment > 0:
        intent_bonus = min(intent_adjustment / 0.095, 1.0) * 0.14
        score += intent_bonus
        reasons.append(f"intent_directness+{intent_bonus:.3f}")
    elif intent_adjustment < 0:
        penalty = max(intent_adjustment / 0.08, -1.0) * 0.08
        score += penalty
        reasons.append(f"intent_penalty{penalty:.3f}")

    return max(0.0, min(score, 1.0)), reasons


# ============================================================
# 3차 개선: Cross-Encoder Semantic Reranking
# ============================================================

_CROSS_ENCODER = None
_CROSS_ENCODER_FAILED = False
CROSS_ENCODER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


def get_cross_encoder():
    """Cross-Encoder를 lazy-load한다. 실패 시 기존 reranker로 fallback한다."""
    global _CROSS_ENCODER, _CROSS_ENCODER_FAILED

    if _CROSS_ENCODER_FAILED:
        return None
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER

    try:
        from sentence_transformers import CrossEncoder

        _CROSS_ENCODER = CrossEncoder(CROSS_ENCODER_MODEL)
        return _CROSS_ENCODER
    except Exception as exc:
        _CROSS_ENCODER_FAILED = True
        print(
            "[RAG CROSS ENCODER] load failed; "
            f"fallback to embedding reranker: {exc}"
        )
        return None


def cross_encoder_rerank_documents(
    docs,
    query: str,
    k: int,
    category: str | None = None,
    cafe_intent: str | None = None,
    food_intent: str | None = None,
):
    """
    Cross-Encoder + RRF + intent + Evidence Quality로 최종 relevance를 계산한다.

    final relevance:
    - Cross-Encoder sigmoid: 45%
    - RRF normalized score: 18%
    - intent signal: 10%
    - query-aware evidence directness/quality: 27%

    핵심:
    의미 유사도만 높은 일반론보다 질문에 직접 답하는 효과/행동/정량 근거를
    최종 Top-K에서 우대한다.
    """
    if not docs:
        return None

    model = get_cross_encoder()
    if model is None:
        return None

    pairs = [(query, doc.page_content) for doc in docs]

    try:
        predictions = model.predict(
            pairs,
            batch_size=16,
            show_progress_bar=False,
        )
    except Exception as exc:
        print(
            "[RAG CROSS ENCODER] predict failed; "
            f"fallback to embedding reranker: {exc}"
        )
        return None

    scored_documents = []

    for doc, raw_score in zip(docs, predictions):
        cross_raw = float(np.asarray(raw_score).reshape(-1)[0])
        cross_norm = _sigmoid(cross_raw)

        adjustment = 0.0
        reasons = []

        if category == "cafe":
            adjustment, reasons = calculate_cafe_behavior_adjustment(
                doc.page_content,
                cafe_intent,
            )
        elif category == "food":
            adjustment, reasons = calculate_food_adjustment(
                doc.page_content,
                food_intent,
            )

        rrf_norm = float(doc.metadata.get("_rag_rrf_norm", 0.0))
        intent_norm = _normalize_adjustment(adjustment)

        evidence_score, evidence_reasons = calculate_evidence_quality(
            query=query,
            text=doc.page_content,
            category=category,
            cafe_intent=cafe_intent,
            food_intent=food_intent,
        )

        final_score = (
            cross_norm * 0.45
            + rrf_norm * 0.18
            + intent_norm * 0.10
            + evidence_score * 0.27
        )

        scored_documents.append(
            {
                "doc": doc,
                "score": final_score,
                "cross_encoder_score": cross_raw,
                "cross_encoder_norm": cross_norm,
                "rrf_score": float(doc.metadata.get("_rag_rrf_score", 0.0)),
                "rrf_norm": rrf_norm,
                "rrf_rank": doc.metadata.get("_rag_rrf_rank"),
                "retrieval_appearances": doc.metadata.get(
                    "_rag_retrieval_appearances", 0
                ),
                "behavior_adjustment": adjustment,
                "intent_norm": intent_norm,
                "behavior_reasons": reasons,
                "evidence_score": evidence_score,
                "evidence_reasons": evidence_reasons,
            }
        )

    scored_documents.sort(key=lambda item: item["score"], reverse=True)

    print("\n[RAG HYBRID RERANK] ================================")
    for index, item in enumerate(scored_documents[:10], start=1):
        doc = item["doc"]
        print(
            f"\n[RAG HYBRID RERANK] {index}"
            f"\nscore={item['score']:.4f}"
            f"\ncross_encoder_raw={item['cross_encoder_score']:.4f}"
            f"\ncross_encoder_norm={item['cross_encoder_norm']:.4f}"
            f"\nrrf_norm={item['rrf_norm']:.4f}"
            f"\nrrf_rank={item['rrf_rank']}"
            f"\nretrieval_appearances={item['retrieval_appearances']}"
            f"\nbehavior_adjustment={item['behavior_adjustment']:+.4f}"
            f"\nintent_norm={item['intent_norm']:.4f}"
            f"\nbehavior_reasons={item['behavior_reasons']}"
            f"\nevidence_score={item['evidence_score']:.4f}"
            f"\nevidence_reasons={item['evidence_reasons']}"
            f"\nsource={doc.metadata.get('source')}"
            f"\npage={doc.metadata.get('page')}"
            f"\npreview={doc.page_content.replace(chr(10), ' ').strip()[:180]}"
        )
    print("\n[RAG HYBRID RERANK] ================================\n")

    deduplicated = remove_near_duplicate_scored_documents(
        ranked_documents=scored_documents,
        threshold=0.88,
    )

    selected = select_diverse_documents(
        ranked_documents=deduplicated,
        k=k,
        max_per_source=2,
        relevance_margin=0.08,
    )

    return [item["doc"] for item in selected]


# ============================================================
# Dual Query 후보 병합
# ============================================================

def merge_search_results(
    korean_docs,
    english_docs,
):
    """
    한국어 검색 결과와 영어 검색 결과를
    교차 방식으로 병합한다.

    여기서는 최종 순위를 결정하지 않는다.
    """

    merged_docs = []

    used_chunks = set()

    max_length = max(
        len(korean_docs),
        len(english_docs),
    )

    for index in range(max_length):

        candidates = []

        if index < len(korean_docs):
            candidates.append(
                korean_docs[index]
            )

        if index < len(english_docs):
            candidates.append(
                english_docs[index]
            )

        for doc in candidates:

            source = doc.metadata.get(
                "source"
            )

            page = doc.metadata.get(
                "page"
            )

            content_key = (
                doc.page_content
                .strip()
            )

            chunk_key = (
                source,
                page,
                content_key,
            )

            if chunk_key in used_chunks:
                continue

            used_chunks.add(
                chunk_key
            )

            merged_docs.append(
                doc
            )

    return merged_docs


# ============================================================
# Source Diversity
# ============================================================

def select_diverse_documents(
    ranked_documents: list[dict],
    k: int,
    max_per_source: int = 2,
    relevance_margin: float = 0.08,
) -> list[dict]:
    """
    최종 Top-K를 역할별로 구성한다.

    1. 종합 relevance 최고 문서 1개 보장
    2. Evidence Quality/Directness 최고 문서 1개 보장
    3. 나머지는 종합점수 순으로 채움
    4. source diversity 유지
    """
    if not ranked_documents or k <= 0:
        return []

    ranked = sorted(
        ranked_documents,
        key=lambda item: item.get("score", 0.0),
        reverse=True,
    )

    selected = []
    selected_ids = set()
    source_counts = {}

    def document_id(item: dict) -> tuple:
        doc = item["doc"]
        return (
            doc.metadata.get("source"),
            doc.metadata.get("page"),
            doc.page_content[:160],
        )

    def source_key(item: dict) -> str:
        return str(item["doc"].metadata.get("source", ""))

    def can_add(item: dict, enforce_source_limit: bool = True) -> bool:
        if document_id(item) in selected_ids:
            return False

        if enforce_source_limit:
            source = source_key(item)
            if source_counts.get(source, 0) >= max_per_source:
                return False

        return True

    def add_item(item: dict) -> bool:
        if len(selected) >= k or document_id(item) in selected_ids:
            return False

        selected.append(item)
        selected_ids.add(document_id(item))

        source = source_key(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        return True

    # Slot 1: overall relevance 최고 문서
    best_overall = ranked[0]
    add_item(best_overall)

    print(
        "[RAG TOPK] slot=overall "
        f"score={best_overall.get('score', 0.0):.4f} "
        f"evidence={best_overall.get('evidence_score', 0.0):.4f} "
        f"source={best_overall['doc'].metadata.get('source')} "
        f"page={best_overall['doc'].metadata.get('page')}"
    )

    if len(selected) >= k:
        return selected

    # Slot 2: 직접적인 evidence가 가장 좋은 문서.
    # 동점이면 intent -> final score -> Cross-Encoder 순으로 선택한다.
    evidence_ranked = sorted(
        ranked,
        key=lambda item: (
            item.get("evidence_score", 0.0),
            item.get("intent_norm", 0.0),
            item.get("score", 0.0),
            item.get("cross_encoder_norm", 0.0),
        ),
        reverse=True,
    )

    for item in evidence_ranked:
        if not can_add(item, enforce_source_limit=True):
            continue

        if item.get("evidence_score", 0.0) < 0.65:
            break

        add_item(item)

        print(
            "[RAG TOPK] slot=evidence "
            f"score={item.get('score', 0.0):.4f} "
            f"evidence={item.get('evidence_score', 0.0):.4f} "
            f"intent={item.get('intent_norm', 0.0):.4f} "
            f"source={item['doc'].metadata.get('source')} "
            f"page={item['doc'].metadata.get('page')}"
        )
        break

    # Slot 3+: 우선 relevance margin 안에서 종합점수 순으로 채운다.
    best_score = ranked[0].get("score", 0.0)

    for item in ranked:
        if len(selected) >= k:
            break

        if not can_add(item, enforce_source_limit=True):
            continue

        if best_score - item.get("score", 0.0) > relevance_margin:
            continue

        add_item(item)

        print(
            "[RAG TOPK] slot=fill "
            f"score={item.get('score', 0.0):.4f} "
            f"evidence={item.get('evidence_score', 0.0):.4f} "
            f"source={item['doc'].metadata.get('source')} "
            f"page={item['doc'].metadata.get('page')}"
        )

    # margin으로 k를 못 채우면 source diversity만 유지하며 완화한다.
    if len(selected) < k:
        for item in ranked:
            if len(selected) >= k:
                break

            if not can_add(item, enforce_source_limit=True):
                continue

            add_item(item)

            print(
                "[RAG TOPK] slot=fill_relaxed "
                f"score={item.get('score', 0.0):.4f} "
                f"evidence={item.get('evidence_score', 0.0):.4f} "
                f"source={item['doc'].metadata.get('source')} "
                f"page={item['doc'].metadata.get('page')}"
            )

    # source limit 때문에도 부족한 경우에만 마지막으로 제한을 완화한다.
    if len(selected) < k:
        for item in ranked:
            if len(selected) >= k:
                break

            if not can_add(item, enforce_source_limit=False):
                continue

            add_item(item)

            print(
                "[RAG TOPK] slot=fill_source_relaxed "
                f"score={item.get('score', 0.0):.4f} "
                f"evidence={item.get('evidence_score', 0.0):.4f} "
                f"source={item['doc'].metadata.get('source')} "
                f"page={item['doc'].metadata.get('page')}"
            )

    return selected


def rerank_documents(
    docs,
    korean_query: str,
    english_query: str,
    embeddings,
    k: int,
    category: str | None = None,
    cafe_intent: str | None = None,
    food_intent: str | None = None,
):
    """
    한국어 query와 영어 retrieval query를 모두 이용해
    후보 문서를 재정렬한다.

    1. 한국어 query similarity
    2. 영어 query similarity
    3. 둘 중 높은 값을 최종 relevance score로 사용
    4. relevance 기준 정렬
    5. source diversity 적용
    """

    if not docs:
        return []

    # --------------------------------------------------------
    # Query Embedding
    # --------------------------------------------------------

    korean_query_embedding = (
        embeddings.embed_query(
            korean_query
        )
    )

    english_query_embedding = None

    if english_query.strip():

        english_query_embedding = (
            embeddings.embed_query(
                english_query
            )
        )

    # --------------------------------------------------------
    # Document Embedding
    # --------------------------------------------------------

    document_texts = [
        doc.page_content
        for doc in docs
    ]

    document_embeddings = (
        embeddings.embed_documents(
            document_texts
        )
    )

    scored_documents = []

    # --------------------------------------------------------
    # Similarity 계산
    # --------------------------------------------------------

    for doc, doc_embedding in zip(
        docs,
        document_embeddings,
    ):

        korean_score = cosine_similarity(
            korean_query_embedding,
            doc_embedding,
        )

        english_score = 0.0

        if english_query_embedding is not None:

            english_score = cosine_similarity(
                english_query_embedding,
                doc_embedding,
            )

        # 원문 질문의 의미를 우선하고 영어 확장 query는 보조 신호로 사용한다.
        # max()를 사용하면 확장 query와 특정 논문이 강하게 맞는 경우
        # 원질문과의 관련성이 낮아도 상위로 치우칠 수 있다.
        if english_query_embedding is not None:
            final_score = (
                korean_score * 0.65
                + english_score * 0.35
            )
        else:
            final_score = korean_score

        behavior_adjustment = 0.0
        behavior_reasons = []

        if category == "cafe":
            behavior_adjustment, behavior_reasons = (
                calculate_cafe_behavior_adjustment(
                    doc.page_content,
                    cafe_intent,
                )
            )
        elif category == "food":
            behavior_adjustment, behavior_reasons = (
                calculate_food_adjustment(
                    doc.page_content,
                    food_intent,
                )
            )

        final_score += behavior_adjustment

        scored_documents.append(
            {
                "doc":
                    doc,

                "score":
                    final_score,

                "korean_score":
                    korean_score,

                "english_score":
                    english_score,

                "behavior_adjustment":
                    behavior_adjustment,

                "behavior_reasons":
                    behavior_reasons,
            }
        )

    # --------------------------------------------------------
    # Relevance 기준 정렬
    # --------------------------------------------------------

    scored_documents.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    # --------------------------------------------------------
    # Reranking Debug
    # --------------------------------------------------------

    print(
        "\n[RAG RERANK] "
        "================================"
    )

    for index, item in enumerate(
        scored_documents[:10],
        start=1,
    ):

        doc = item["doc"]

        source = doc.metadata.get(
            "source"
        )

        page = doc.metadata.get(
            "page"
        )

        preview = (
            doc.page_content
            .replace("\n", " ")
            .strip()[:180]
        )

        print(
            f"\n[RAG RERANK] {index}"
            f"\nscore={item['score']:.4f}"
            f"\nko_score={item['korean_score']:.4f}"
            f"\nen_score={item['english_score']:.4f}"
            f"\nbehavior_adjustment={item['behavior_adjustment']:+.4f}"
            f"\nbehavior_reasons={item['behavior_reasons']}"
            f"\nsource={source}"
            f"\npage={page}"
            f"\npreview={preview}"
        )

    print(
        "\n[RAG RERANK] "
        "================================\n"
    )

    # --------------------------------------------------------
    # Source Diversity 적용
    # --------------------------------------------------------

    deduplicated_documents = remove_near_duplicate_scored_documents(
        ranked_documents=scored_documents,
        threshold=0.88,
    )

    selected_documents = (
        select_diverse_documents(
            ranked_documents=deduplicated_documents,
            k=k,
            max_per_source=2,
            relevance_margin=0.08,
        )
    )

    # --------------------------------------------------------
    # Diversity Debug
    # --------------------------------------------------------

    print(
        "\n[RAG DIVERSITY] "
        "================================"
    )

    for index, item in enumerate(
        selected_documents,
        start=1,
    ):

        doc = item["doc"]

        source = doc.metadata.get(
            "source"
        )

        page = doc.metadata.get(
            "page"
        )

        print(
            f"[RAG DIVERSITY] {index} "
            f"score={item['score']:.4f} "
            f"source={source} "
            f"page={page}"
        )

    print(
        "[RAG DIVERSITY] "
        "================================\n"
    )

    # --------------------------------------------------------
    # 최종 Document만 반환
    # --------------------------------------------------------

    return [
        item["doc"]
        for item in selected_documents
    ]


# ============================================================
# RAG Search
# ============================================================

def search_rag(
    query: str,
    k: int = 5,
    preferred_category: str | None = None,
):
    """
    사용자 질문과 유사한 RAG 문서를 검색한다.

    검색 과정:

    1. category 결정
    2. 한국어 원본 query 검색
    3. 영어 retrieval query 생성
    4. 영어 query 검색
    5. BM25 sparse 검색
    6. Dense + Sparse RRF 후보 통합
    7. noise / duplicate 제거
    8. Cross-Encoder + query-aware evidence quality reranking
    9. source diversity 적용
    10. 최종 Top-K 선정
    11. context 생성
    """

    # 같은 embedding 객체를
    # 검색 + reranking에서 재사용
    embeddings = get_embeddings()

    vectorstore = get_vector_store(
        embeddings=embeddings,
    )

    # ========================================================
    # Category 결정
    # ========================================================

    if preferred_category not in VALID_CATEGORIES:
        preferred_category = None

    if preferred_category is None:

        preferred_category = (
            detect_query_category(
                query
            )
        )

    print(
        "[RAG] "
        f"preferred_category="
        f"{preferred_category}"
    )

    # ========================================================
    # 후보 검색 개수
    # ========================================================

    candidate_k = max(
        k * 5,
        20,
    )

    # ========================================================
    # Dual Query 생성
    # ========================================================

    korean_query = query

    english_query = (
        build_english_retrieval_query(
            query=query,
            category=preferred_category,
        )
    )

    cafe_intent = None
    food_intent = None

    if preferred_category == "cafe":
        cafe_intent = detect_cafe_intent(query)
    elif preferred_category == "food":
        food_intent = detect_food_intent(query)

    # ========================================================
    # 공통 MMR 설정
    # ========================================================

    search_kwargs = {
        "k":
            candidate_k,

        "fetch_k":
            max(
                candidate_k * 3,
                60,
            ),

        "lambda_mult":
            0.6,
    }

    if preferred_category:

        search_kwargs["filter"] = {
            "category":
                preferred_category
        }

    # ========================================================
    # 한국어 Query 검색
    # ========================================================

    korean_docs = (
        vectorstore
        .max_marginal_relevance_search(
            korean_query,
            **search_kwargs,
        )
    )

    # ========================================================
    # 영어 Query 검색
    # ========================================================

    english_docs = []

    if english_query.strip():

        english_docs = (
            vectorstore
            .max_marginal_relevance_search(
                english_query,
                **search_kwargs,
            )
        )

    # ========================================================
    # 3차 개선: BM25 Sparse Retrieval
    # ========================================================

    category_docs = get_category_documents(
        vectorstore=vectorstore,
        category=preferred_category,
    )

    bm25_query = " ".join(
        part for part in [korean_query, english_query] if part.strip()
    )

    bm25_docs = bm25_search_documents(
        docs=category_docs,
        query=bm25_query,
        k=candidate_k,
    )

    # ========================================================
    # 3차 개선: Dense + Sparse RRF Fusion
    # ========================================================

    fused_docs = reciprocal_rank_fusion(
        ranked_lists=[
            korean_docs,
            english_docs,
            bm25_docs,
        ],
        k=max(candidate_k * 2, 40),
    )

    # ========================================================
    # Noise + Duplicate 제거
    # ========================================================

    candidate_docs = prepare_candidate_documents(
        docs=fused_docs,
    )

    # ========================================================
    # 3차 개선: Cross-Encoder Reranking + Source Diversity
    # 실패 시 기존 Dual Query reranker로 안전하게 fallback
    # ========================================================

    final_docs = cross_encoder_rerank_documents(
        docs=candidate_docs,
        query=korean_query,
        k=k,
        category=preferred_category,
        cafe_intent=cafe_intent,
        food_intent=food_intent,
    )

    if final_docs is None:
        final_docs = rerank_documents(
            docs=candidate_docs,
            korean_query=korean_query,
            english_query=english_query,
            embeddings=embeddings,
            k=k,
            category=preferred_category,
            cafe_intent=cafe_intent,
            food_intent=food_intent,
        )

    # ========================================================
    # 검색 결과 Debug
    # ========================================================

    print(
        "\n[RAG] "
        "=============================="
    )

    print(
        f"[RAG] korean_query="
        f"{korean_query}"
    )

    print(
        f"[RAG] english_query="
        f"{english_query}"
    )

    print(
        f"[RAG] category="
        f"{preferred_category}"
    )

    if cafe_intent is not None:
        print(
            f"[RAG] cafe_intent="
            f"{cafe_intent}"
        )

    if food_intent is not None:
        print(
            f"[RAG] food_intent="
            f"{food_intent}"
        )

    print(
        f"[RAG] korean_candidate_count="
        f"{len(korean_docs)}"
    )

    print(
        f"[RAG] english_candidate_count="
        f"{len(english_docs)}"
    )

    print(
        f"[RAG] bm25_candidate_count="
        f"{len(bm25_docs)}"
    )

    print(
        f"[RAG] fused_candidate_count="
        f"{len(fused_docs)}"
    )

    print(
        f"[RAG] cleaned_candidate_count="
        f"{len(candidate_docs)}"
    )

    print(
        f"[RAG] final_count="
        f"{len(final_docs)}"
    )

    for idx, doc in enumerate(
        final_docs,
        start=1,
    ):

        source = doc.metadata.get(
            "source"
        )

        page = doc.metadata.get(
            "page"
        )

        category = doc.metadata.get(
            "category"
        )

        preview = (
            doc.page_content
            .replace("\n", " ")
            .strip()[:300]
        )

        print(
            f"\n[RAG] RESULT {idx}\n"
            f"source={source}\n"
            f"page={page}\n"
            f"category={category}\n"
            f"preview={preview}"
        )

    print(
        "[RAG] "
        "==============================\n"
    )

    # ========================================================
    # Context 생성
    # ========================================================

    context_parts = []
    sources = []

    for idx, doc in enumerate(
        final_docs,
        start=1,
    ):

        source = doc.metadata.get(
            "source"
        )

        page = doc.metadata.get(
            "page"
        )

        doc_type = doc.metadata.get(
            "type"
        )

        category = doc.metadata.get(
            "category"
        )

        context_parts.append(
            f"[문서 {idx}]\n"
            f"카테고리: {category}\n"
            f"출처: {source}\n"
            f"페이지: {page}\n"
            f"내용:\n"
            f"{doc.page_content}"
        )

        sources.append(
            {
                "source":
                    source,

                "page":
                    page,

                "type":
                    doc_type,

                "category":
                    category,

                "preview":
                    doc.page_content[:200],
            }
        )

    return {
        "query_category":
            preferred_category,

        "context":
            "\n\n".join(
                context_parts
            ),

        "sources":
            sources,

        "english_query":
            english_query,
    }


# ============================================================
# Context Only
# ============================================================

def get_rag_context(
    query: str,
    k: int = 5,
    preferred_category: str | None = None,
) -> str:
    """
    다른 서비스에서 사용할 수 있도록
    RAG 검색 결과 중 context만 반환한다.
    """

    result = search_rag(
        query=query,
        k=k,
        preferred_category=preferred_category,
    )

    return result["context"]