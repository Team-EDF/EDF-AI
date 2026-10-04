"""
챌린지 추천 (프로필 -> 맞춤 챌린지 3개).

흐름: 프로필 -> challenge_selector(규칙으로 3개 선택) -> challenge_explainer(추천 이유 문구)
- 어떤 챌린지를 줄지, 포인트·난이도·인증방식·예상 절감량은 모두 규칙/카탈로그의 고정값이다.
- LLM은 "추천 이유" 문구만 쓰고, 실패하면 고정 문구로 대체된다 (응답은 항상 정상).

지금은 설문 기반 프로필(source="survey")만 받는다. user_id만 주고 서버가 실데이터로 프로필을
다시 계산하는 기능은 실데이터 전환(2단계) 구현 후에 추가한다.

참고: 이전에 있던 'LLM이 챌린지 행동을 자유롭게 쓰는' 프로토타입(이번 달/지난 달 category_stats
추이 조회 포함)은 카탈로그 방식으로 바꾸면서 제거했다. 실데이터 전환 때 필요하면 git 기록
(커밋 13e5943)에서 get_category_trend를 참고한다.
"""
from typing import Callable

from app.services.challenge_explainer import generate_reasons
from app.services.challenge_selector import select_challenges

INTRO_MESSAGE = "AI가 생활패턴을 분석했어요."


def recommend_challenges(
    profile: dict | None,
    exclude_ids: list[str] | None = None,
    llm_call: Callable[[str], str] | None = None,
    user_name: str | None = None,
) -> dict:
    """
    프로필로 챌린지 3개와 추천 이유를 만든다.

    profile 예시: {"source": "survey", "areas": [{"key": "move", "level": 4}, ...],
                   "persona": {"type_name": "...", "preferred_difficulty": 2}}
    user_name이 있으면 추천 이유 문구에서 "{이름}님"으로 부른다 (GSTI 유형 이름은 문구에 쓰지 않는다).
    프로필이 없으면 ValueError (user_id만으로 추천하는 기능은 아직 지원하지 않는다).
    """
    if not profile or not profile.get("areas"):
        raise ValueError("profile.areas가 필요합니다. (user_id만으로 추천하는 기능은 실데이터 전환 후 지원)")

    selected = select_challenges(profile, exclude_ids)
    reasons = generate_reasons(selected, profile.get("persona"), llm_call=llm_call, user_name=user_name)

    challenges = []
    for item, reason in zip(selected, reasons):
        challenge = {key: value for key, value in item.items() if key != "selection"}
        challenge["slot"] = item["selection"]["slot"]
        challenge["reason"] = reason["reason"]
        challenge["reason_source"] = reason["reason_source"]
        challenges.append(challenge)

    return {
        "source": profile.get("source") or "survey",
        "intro": INTRO_MESSAGE,
        "challenges": challenges,
    }
