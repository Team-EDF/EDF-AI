from collections import Counter

from app.services.rag_service import search_rag


# ============================================================
# Cafe RAG 검색 품질 테스트
# ============================================================

TEST_QUERIES = [

    # --------------------------------------------------------
    # 1. 라떼 / 식물성 대체유
    # --------------------------------------------------------
    "라떼를 마실 때 탄소배출을 줄이려면 어떻게 해야 할까?",

    "우유 대신 식물성 음료를 선택하면 "
    "탄소배출 감소에 도움이 될까?",

    "우유와 오트밀크의 환경영향은 어떤 차이가 있을까?",

    "두유와 아몬드 음료는 환경영향 측면에서 "
    "어떤 차이가 있을까?",

    # --------------------------------------------------------
    # 2. 다회용컵 / 텀블러
    # --------------------------------------------------------
    "다회용컵은 몇 번 이상 사용해야 "
    "일회용컵보다 환경적으로 유리할까?",

    "일회용컵 대신 텀블러를 사용하면 "
    "탄소배출을 줄일 수 있을까?",

    "다회용컵을 세척하면서 계속 사용하는 것이 "
    "정말 환경에 도움이 될까?",

    # --------------------------------------------------------
    # 3. 일회용컵 / 테이크아웃
    # --------------------------------------------------------
    "테이크아웃 커피의 일회용컵 사용으로 발생하는 "
    "환경영향을 줄이려면 어떻게 해야 할까?",

    "카페에서 일회용 플라스틱컵 사용을 줄이는 것이 "
    "환경에 어떤 도움이 될까?",

    # --------------------------------------------------------
    # 4. 커피 자체의 탄소발자국
    # --------------------------------------------------------
    "커피 한 잔의 탄소발자국은 "
    "어떤 과정에서 발생할까?",

    "커피 원두의 생산과 유통 과정에서 발생하는 "
    "탄소배출을 줄이는 방법은 무엇일까?",

    # --------------------------------------------------------
    # 5. 종합 친환경 카페 소비
    # --------------------------------------------------------
    "카페를 이용할 때 실천할 수 있는 "
    "친환경 소비 방법을 알려줘.",
]


# ============================================================
# 파일명 추출
# ============================================================

def get_filename(source: str | None) -> str:

    if not source:
        return "NO_SOURCE"

    return (
        source
        .replace("\\", "/")
        .split("/")[-1]
    )


# ============================================================
# 메인 테스트
# ============================================================

def main():

    source_counter = Counter()
    rank1_counter = Counter()

    query_count = len(TEST_QUERIES)

    print()
    print("=" * 75)
    print(" Cafe RAG Search Quality Test")
    print(f" Total Queries: {query_count}")
    print("=" * 75)

    for index, query in enumerate(
        TEST_QUERIES,
        start=1,
    ):

        print()
        print("=" * 75)
        print(
            f"[QUERY {index}/{query_count}] "
            f"{query}"
        )
        print("=" * 75)

        try:
            result = search_rag(
                query=query,
                k=5,
                preferred_category="cafe",
            )

        except Exception as error:

            print(
                "[ERROR] "
                f"{type(error).__name__}: "
                f"{error}"
            )

            continue

        sources = result.get(
            "sources",
            [],
        )

        if not sources:

            print("검색 결과 없음")
            continue

        for rank, source_info in enumerate(
            sources,
            start=1,
        ):

            filename = get_filename(
                source_info.get("source")
            )

            page = source_info.get("page")
            category = source_info.get("category")

            source_counter[filename] += 1

            if rank == 1:
                rank1_counter[filename] += 1

            print(
                f"{rank}. "
                f"{filename} "
                f"(page={page}, "
                f"category={category})"
            )

    # ========================================================
    # 전체 검색 결과 분포
    # ========================================================

    print()
    print()
    print("=" * 75)
    print(" 전체 검색 결과 PDF 분포")
    print("=" * 75)

    total_results = sum(
        source_counter.values()
    )

    if total_results == 0:

        print("검색된 PDF가 없습니다.")

    else:

        for filename, count in (
            source_counter.most_common()
        ):

            percentage = (
                count
                / total_results
                * 100
            )

            print(
                f"{filename}: "
                f"{count}회 "
                f"({percentage:.1f}%)"
            )

    # ========================================================
    # Rank 1 분포
    # ========================================================

    print()
    print("=" * 75)
    print(" Rank 1 PDF 분포")
    print("=" * 75)

    total_rank1 = sum(
        rank1_counter.values()
    )

    if total_rank1 == 0:

        print("Rank 1 결과가 없습니다.")

    else:

        for filename, count in (
            rank1_counter.most_common()
        ):

            percentage = (
                count
                / total_rank1
                * 100
            )

            print(
                f"{filename}: "
                f"{count}회 "
                f"({percentage:.1f}%)"
            )

    print()
    print("=" * 75)
    print(" Cafe RAG Test Complete")
    print("=" * 75)


if __name__ == "__main__":
    main()