from collections import defaultdict
from pathlib import Path

import chromadb


CHROMA_DIR = "chroma_db"


def main():
    print("\n========================================")
    print(" Chroma RAG Document Check")
    print("========================================\n")

    client = chromadb.PersistentClient(
        path=CHROMA_DIR
    )

    collections = client.list_collections()

    if not collections:
        print("Chroma collection이 없습니다.")
        return

    print(
        "Collection:",
        [c.name for c in collections]
    )
    print()

    for collection_info in collections:

        collection = client.get_collection(
            collection_info.name
        )

        data = collection.get(
            include=["metadatas"]
        )

        metadatas = data.get(
            "metadatas",
            []
        )

        print("=" * 60)
        print(
            f"[COLLECTION] "
            f"{collection_info.name}"
        )
        print(
            f"전체 Chunk 수: "
            f"{len(metadatas)}"
        )
        print("=" * 60)

        category_sources = defaultdict(
            lambda: defaultdict(int)
        )

        no_category_count = 0
        no_source_count = 0

        for metadata in metadatas:

            if metadata is None:
                continue

            category = metadata.get(
                "category"
            )

            source = metadata.get(
                "source"
            )

            if not category:
                category = "NO_CATEGORY"
                no_category_count += 1

            if not source:
                source = "NO_SOURCE"
                no_source_count += 1

            category_sources[
                category
            ][source] += 1

        for category in sorted(
            category_sources
        ):

            sources = (
                category_sources[
                    category
                ]
            )

            chunk_count = sum(
                sources.values()
            )

            print()
            print(
                f"[CATEGORY] {category}"
            )

            print(
                f"PDF 수: {len(sources)}"
            )

            print(
                f"Chunk 수: {chunk_count}"
            )

            for source, count in sorted(
                sources.items()
            ):

                if source == "NO_SOURCE":
                    filename = source
                else:
                    filename = Path(
                        source
                    ).name

                print(
                    f"  - {filename}"
                    f" -> {count} chunks"
                )

        print()
        print(
            "category 없는 chunk:",
            no_category_count
        )

        print(
            "source 없는 chunk:",
            no_source_count
        )

        print()

    print("========================================")
    print("검사 완료")
    print("========================================")


if __name__ == "__main__":
    main()