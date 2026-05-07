CATEGORY_KEYWORDS = {
    "카페/음료": [
        "아메리카노", "카페라떼", "라떼", "바닐라라떼",
        "콜드브루", "커피", "에스프레소", "아이스티",
        "복숭아아이스티", "자몽허니블랙티", "블랙티",
        "밀크티", "녹차", "홍차", "프라푸치노",
        "스무디", "주스", "에이드", "콜라", "사이다"
    ],
    "식품": [
        "김밥", "삼각김밥", "도시락", "라면", "컵라면",
        "샌드위치", "샐러드", "빵", "케이크", "과자",
        "우유", "사과", "바나나", "계란"
    ],
    "생활용품": [
        "세제", "휴지", "샴푸", "비누", "치약",
        "칫솔", "물티슈", "종량제", "건전지"
    ],
    "교통": [
        "택시", "버스", "지하철", "주유", "휘발유",
        "경유", "주차", "하이패스"
    ],
    "의류": [
        "셔츠", "바지", "신발", "운동화", "양말",
        "자켓", "코트", "패딩"
    ]
}


def classify_category(item_name: str) -> str:
    normalized_name = item_name.replace(" ", "").lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            normalized_keyword = keyword.replace(" ", "").lower()

            if normalized_keyword in normalized_name:
                return category

    return "기타"