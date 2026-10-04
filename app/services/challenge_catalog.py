"""
챌린지 카탈로그 로더.

app/data/challenge_catalog.json(5영역 x 3난이도 = 15개)을 읽어서 검증한 뒤 돌려준다.
포인트, 난이도, 인증방식, 예상 절감량은 모두 이 JSON의 고정값이고 LLM이 바꾸지 않는다.
(챌린지 선택 규칙은 challenge_service에서 이 카탈로그의 후보 중 3개를 고른다.)

JSON을 사람이 고치다가 값이 어긋나면 서버가 조용히 잘못된 포인트를 주지 않도록,
불러올 때 구조를 검사해서 문제가 있으면 바로 ValueError로 알려준다.
"""
import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "challenge_catalog.json"

AREAS = ("move", "cafe", "food", "shop", "life")
AREA_LABELS = {
    "move": "이동",
    "cafe": "카페",
    "food": "식품",
    "shop": "쇼핑",
    "life": "생활",
}
DIFFICULTIES = (1, 2, 3)
VERIFICATIONS = ("AUTO_TRANSIT", "SELF")
PERIODS = ("week",)

# 자율 체크 챌린지는 하루에 한 번만 체크할 수 있다 (어뷰징 방지).
SELF_DAILY_CHECK_LIMIT = 1

REQUIRED_FIELDS = (
    "challenge_id", "area", "difficulty", "title", "description",
    "target_count", "unit", "period", "verification", "points",
    "est_saving_kg", "saving_basis",
)


def _validate(raw: dict) -> None:
    """카탈로그 구조를 검사한다. 문제가 있으면 ValueError."""
    challenges = raw.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        raise ValueError("카탈로그에 challenges 목록이 없습니다.")
    if not raw.get("catalog_version"):
        raise ValueError("카탈로그에 catalog_version이 없습니다.")

    seen_ids: set[str] = set()
    seen_slots: set[tuple[str, int]] = set()

    for item in challenges:
        cid = item.get("challenge_id", "<id 없음>")

        missing = [field for field in REQUIRED_FIELDS if field not in item]
        if missing:
            raise ValueError(f"{cid}: 필수 항목 누락 {missing}")
        if cid in seen_ids:
            raise ValueError(f"{cid}: challenge_id가 중복입니다.")
        seen_ids.add(cid)

        if item["area"] not in AREAS:
            raise ValueError(f"{cid}: 알 수 없는 영역 {item['area']!r}")
        if item["difficulty"] not in DIFFICULTIES:
            raise ValueError(f"{cid}: difficulty는 1~3이어야 합니다 ({item['difficulty']!r})")
        if item["verification"] not in VERIFICATIONS:
            raise ValueError(f"{cid}: 알 수 없는 인증방식 {item['verification']!r}")
        if item["period"] not in PERIODS:
            raise ValueError(f"{cid}: 알 수 없는 period {item['period']!r}")
        if not isinstance(item["points"], int) or item["points"] <= 0:
            raise ValueError(f"{cid}: points는 1 이상의 정수여야 합니다 ({item['points']!r})")
        if not isinstance(item["target_count"], int) or item["target_count"] <= 0:
            raise ValueError(f"{cid}: target_count는 1 이상의 정수여야 합니다 ({item['target_count']!r})")
        if item["est_saving_kg"] is not None and item["est_saving_kg"] < 0:
            raise ValueError(f"{cid}: est_saving_kg는 0 이상이거나 null이어야 합니다.")
        if item["est_saving_kg"] is not None and not item["saving_basis"]:
            raise ValueError(f"{cid}: est_saving_kg 값이 있으면 saving_basis(근거)도 있어야 합니다.")

        slot = (item["area"], item["difficulty"])
        if slot in seen_slots:
            raise ValueError(f"{cid}: 같은 영역/난이도 칸이 이미 있습니다 {slot}")
        seen_slots.add(slot)

    expected_slots = {(area, level) for area in AREAS for level in DIFFICULTIES}
    if seen_slots != expected_slots:
        raise ValueError(f"영역 x 난이도 칸이 15개 모두 채워져야 합니다. 비어 있음: {sorted(expected_slots - seen_slots)}")


def _normalize(item: dict) -> dict:
    """응답/선택에 쓰는 형태로 보강한다 (영역 이름, 일일 체크 제한)."""
    normalized = dict(item)
    normalized["area_label"] = AREA_LABELS[item["area"]]
    # 자동 인증은 일일 제한이 없고, 자율 체크는 챌린지당 하루 1회
    normalized["daily_check_limit"] = SELF_DAILY_CHECK_LIMIT if item["verification"] == "SELF" else None
    normalized.setdefault("verification_next", None)
    return normalized


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    """카탈로그를 읽어 검증하고 캐시한다. {'catalog_version', 'challenges': [...]}"""
    with open(CATALOG_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    _validate(raw)
    return {
        "catalog_version": raw["catalog_version"],
        "challenges": [_normalize(item) for item in raw["challenges"]],
    }


def get_challenge(challenge_id: str) -> dict | None:
    """challenge_id로 챌린지 하나를 찾는다. 없으면 None."""
    for item in load_catalog()["challenges"]:
        if item["challenge_id"] == challenge_id:
            return item
    return None


def list_challenges(area: str | None = None, difficulty: int | None = None) -> list[dict]:
    """영역/난이도로 걸러서 목록을 돌려준다. (조건 없으면 15개 전체)"""
    result = load_catalog()["challenges"]
    if area is not None:
        result = [c for c in result if c["area"] == area]
    if difficulty is not None:
        result = [c for c in result if c["difficulty"] == difficulty]
    return result
