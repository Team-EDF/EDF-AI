"""
챌린지 추천(설명 문구 + 추천 흐름 + API) 테스트. DB와 실제 Gemini 호출은 쓰지 않는다.

실행: pytest tests/test_challenge_recommend.py
(pytest가 없으면: python -c "import tests.test_challenge_recommend as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]")
"""
import json
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import challenges as challenges_route
from app.services.challenge_explainer import (
    SOURCE_FALLBACK,
    SOURCE_LLM,
    build_fallback_reason,
    build_prompt,
    generate_reasons,
)
from app.services.challenge_selector import select_challenges
from app.services.challenge_service import INTRO_MESSAGE, recommend_challenges

PROFILE = {
    "source": "survey",
    "areas": [
        {"key": "move", "level": 4},
        {"key": "food", "level": 3},
        {"key": "cafe", "level": 2},
        {"key": "shop", "level": 3},
    ],
    "persona": {"type_code": "DLSA", "type_name": "쭉쭉 자라는 전나무", "tagline": "바쁘게 달리고 알뜰히 사는 전나무", "preferred_difficulty": 2},
}
EXPECTED_IDS = ["MOVE_2", "FOOD_1", "SHOP_1"]


class _env:
    """테스트 동안만 환경변수를 바꾼다."""

    def __init__(self, **values):
        self.values, self.old = values, {}

    def __enter__(self):
        for key, value in self.values.items():
            self.old[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *exc):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _selected():
    return select_challenges(PROFILE)


def _llm_json(**overrides) -> str:
    base = {
        "MOVE_2": "이동 영역이 가장 커서 대중교통부터 시작하면 좋아요.",
        "FOOD_1": "식탁도 조금씩 가볍게 바꿔 볼 수 있어요.",
        "SHOP_1": "구매를 한 번 미루는 것부터 가볍게 해 보세요.",
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


# ---------------------------------------------------------------- 폴백 문구

def test_fallback_reason_mentions_area_and_title_and_is_deterministic():
    for challenge in _selected():
        text = build_fallback_reason(challenge)
        assert challenge["area_label"] in text and challenge["title"] in text
        assert text == build_fallback_reason(challenge)


def test_fallback_reason_differs_by_role():
    first, second, third = _selected()
    assert "가장 커요" in build_fallback_reason(first)
    assert "가장 커요" not in build_fallback_reason(second)
    life = select_challenges({"areas": [{"key": "move", "level": 1}], "persona": {}})[0]
    assert life["selection"]["role"] == "aux_life" and "생활 속" in build_fallback_reason(life)


# ---------------------------------------------------------------- LLM 문구 검증

def test_valid_llm_output_is_used():
    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: _llm_json())
    assert [r["reason_source"] for r in result] == [SOURCE_LLM] * 3
    assert result[0]["reason"].startswith("이동 영역이 가장 커서")


def test_code_fence_around_json_is_accepted():
    fenced = "```json\n" + _llm_json() + "\n```"
    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: fenced)
    assert all(r["reason_source"] == SOURCE_LLM for r in result)


def test_invalid_json_falls_back_for_everything():
    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: "죄송해요, 만들지 못했어요")
    assert all(r["reason_source"] == SOURCE_FALLBACK for r in result)
    assert all(r["reason"] for r in result)


def test_missing_key_falls_back_only_for_that_item():
    data = json.loads(_llm_json())
    del data["FOOD_1"]
    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: json.dumps(data, ensure_ascii=False))
    assert [r["reason_source"] for r in result] == [SOURCE_LLM, SOURCE_FALLBACK, SOURCE_LLM]


def test_invented_numbers_are_rejected():
    # 입력으로 준 적 없는 숫자(12kg, 40%)를 지어내면 그 문구는 버린다
    raw = _llm_json(MOVE_2="이 챌린지로 한 달에 12kg을 줄일 수 있어요.", SHOP_1="구매를 미루면 40% 절약돼요.")
    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: raw)
    assert [r["reason_source"] for r in result] == [SOURCE_FALLBACK, SOURCE_LLM, SOURCE_FALLBACK]


def test_numbers_that_were_given_are_allowed():
    # 입력 사실에 있는 숫자(포인트 70, 주 2회)는 써도 된다
    raw = _llm_json(MOVE_2="주 2회만 타도 70포인트를 받을 수 있어요.")
    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: raw)
    assert result[0]["reason_source"] == SOURCE_LLM


def test_too_long_or_multiline_or_empty_are_rejected():
    raw = _llm_json(MOVE_2="가" * 200, FOOD_1="첫 줄\n둘째 줄", SHOP_1="   ")
    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: raw)
    assert all(r["reason_source"] == SOURCE_FALLBACK for r in result)


def test_llm_exception_never_breaks_recommendation():
    def boom(prompt):
        raise TimeoutError("시간 초과")

    result = generate_reasons(_selected(), PROFILE["persona"], llm_call=boom)
    assert len(result) == 3 and all(r["reason_source"] == SOURCE_FALLBACK for r in result)


def test_llm_disabled_flag_skips_the_call():
    calls = []
    with _env(CHALLENGE_LLM_ENABLED="false"):
        result = generate_reasons(_selected(), PROFILE["persona"], llm_call=lambda prompt: calls.append(prompt) or _llm_json())
    assert calls == []
    assert all(r["reason_source"] == SOURCE_FALLBACK for r in result)


def test_prompt_contains_facts_and_number_rule():
    prompt = build_prompt(_selected(), PROFILE["persona"])
    for text in ("MOVE_2", "대중교통 주 2회 이용하기", "포인트 70", "새로운 숫자"):
        assert text in prompt
    # GSTI 유형 이름은 재미로 보는 결과라 추천 문구 프롬프트에 넣지 않는다
    assert "쭉쭉 자라는 전나무" not in prompt


# ---------------------------------------------------------------- 추천 흐름

def test_recommend_returns_three_with_intro_and_reasons():
    with _env(CHALLENGE_LLM_ENABLED="false"):
        result = recommend_challenges(PROFILE)
    assert result["intro"] == INTRO_MESSAGE and result["source"] == "survey"
    assert [c["challenge_id"] for c in result["challenges"]] == EXPECTED_IDS
    assert [c["slot"] for c in result["challenges"]] == [1, 2, 3]
    for c in result["challenges"]:
        assert c["reason"] and c["reason_source"] == SOURCE_FALLBACK
        assert "selection" not in c
        assert c["points"] > 0


def test_recommend_keeps_catalog_values_even_when_llm_talks():
    # LLM 문구와 상관없이 포인트/난이도/절감량은 카탈로그 값 그대로다
    result = recommend_challenges(PROFILE, llm_call=lambda prompt: _llm_json())
    first = result["challenges"][0]
    assert (first["points"], first["difficulty"], first["est_saving_kg"]) == (70, 2, 7.92)
    assert first["reason_source"] == SOURCE_LLM


def test_recommend_respects_exclusions():
    with _env(CHALLENGE_LLM_ENABLED="false"):
        result = recommend_challenges(PROFILE, exclude_ids=["MOVE_2", "FOOD_1"])
    ids = [c["challenge_id"] for c in result["challenges"]]
    assert "MOVE_2" not in ids and "FOOD_1" not in ids and len(ids) == 3


def test_recommend_without_profile_raises():
    for bad in (None, {}, {"areas": []}):
        try:
            recommend_challenges(bad)
        except ValueError:
            continue
        raise AssertionError(f"ValueError가 나와야 함: {bad!r}")


# ---------------------------------------------------------------- API (DB 없이 라우터만)

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(challenges_route.router, prefix="/api")
    return TestClient(app)


def test_api_recommend_ok_and_accepts_full_profile_response():
    full_profile = dict(PROFILE)
    full_profile.update({"focus_area": "move", "baseline_carbon_kg": 333.7, "message": "..."})  # /api/profile 응답의 다른 필드
    full_profile["areas"] = [dict(a, label="x", level_label="y", carbon_kg=1.0, spend_krw=1) for a in PROFILE["areas"]]
    with _env(CHALLENGE_LLM_ENABLED="false"):
        r = _client().post("/api/challenges/recommend", json={"profile": full_profile, "exclude_challenge_ids": []})
    assert r.status_code == 200
    body = r.json()
    assert [c["challenge_id"] for c in body["challenges"]] == EXPECTED_IDS
    assert body["intro"] == INTRO_MESSAGE
    assert {"slot", "reason", "reason_source", "points", "verification", "est_saving_kg"} <= set(body["challenges"][0])


def test_api_recommend_user_id_only_is_400_with_guidance():
    r = _client().post("/api/challenges/recommend", json={"user_id": 1})
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_api_recommend_bad_input_is_422():
    client = _client()
    assert client.post("/api/challenges/recommend", json={"profile": {"areas": [{"key": "move"}]}}).status_code == 422
    assert client.post("/api/challenges/recommend", json={"profile": {"areas": "move"}}).status_code == 422


# ---------------------------------------------------------------- 사용자 이름 (GSTI 유형 이름 대신)

def test_user_name_is_sanitized():
    from app.services.challenge_explainer import MAX_NAME_CHARS, sanitize_user_name

    assert sanitize_user_name("테스트 유저") == "테스트 유저"
    assert sanitize_user_name("  테스트\n유저<script>!! ") == "테스트 유저 script"   # 줄바꿈·특수문자 제거
    assert len(sanitize_user_name("가" * 100)) == MAX_NAME_CHARS
    assert sanitize_user_name("") is None and sanitize_user_name("!!!") is None and sanitize_user_name(None) is None


def test_prompt_uses_name_and_never_the_type_name():
    from app.services.challenge_explainer import build_prompt

    challenges = [_challenge_for_prompt()]
    persona = {"type_name": "느긋한 고구마", "tagline": "천천히 익어가는 고구마"}
    prompt = build_prompt(challenges, persona, "테스트 유저")
    assert "이름: 테스트 유저" in prompt and "'테스트 유저님'" in prompt
    assert "느긋한 고구마" not in prompt and "천천히 익어가는" not in prompt
    anonymous = build_prompt(challenges, persona, None)
    assert "이름을 모름" in anonymous and "님'이라고" not in anonymous


def test_fallback_reason_includes_name_when_given():
    from app.services.challenge_explainer import build_fallback_reason

    challenge = _challenge_for_prompt()
    assert build_fallback_reason(challenge, "테스트 유저").startswith("테스트 유저님, ")
    assert not build_fallback_reason(challenge).startswith("테스트 유저님")
    assert "님," not in build_fallback_reason(challenge)


def test_generate_reasons_accepts_digits_in_name_and_passes_name_to_llm():
    from app.services.challenge_explainer import SOURCE_LLM, generate_reasons

    challenges = [_challenge_for_prompt()]
    seen = {}

    def fake_llm(prompt):
        seen["prompt"] = prompt
        return '{"MOVE_2": "user7님, 이동 영역을 줄일 여지가 커요."}'   # 이름 속 숫자(7)는 지어낸 숫자가 아니다

    result = generate_reasons(challenges, {"type_name": "느긋한 고구마"}, llm_call=fake_llm, user_name="user7")
    assert result[0]["reason_source"] == SOURCE_LLM and result[0]["reason"].startswith("user7님")
    assert "이름: user7" in seen["prompt"] and "고구마" not in seen["prompt"]
    # 이름에 없는 숫자는 여전히 막는다
    result = generate_reasons(
        challenges, None, user_name="user7",
        llm_call=lambda prompt: '{"MOVE_2": "user7님, 탄소를 35% 줄여요."}',
    )
    assert result[0]["reason_source"] == "fallback" and result[0]["reason"].startswith("user7님, ")


def test_recommend_passes_user_name_to_reason():
    profile = {
        "source": "survey",
        "areas": [{"key": "move", "level": 4}, {"key": "food", "level": 2}, {"key": "cafe", "level": 1}, {"key": "shop", "level": 2}],
        "persona": {"type_name": "느긋한 고구마", "preferred_difficulty": 2},
    }
    from app.services.challenge_service import recommend_challenges

    result = recommend_challenges(profile, llm_call=lambda prompt: "not json", user_name="테스트 유저")
    assert result["challenges"] and all(c["reason"].startswith("테스트 유저님, ") for c in result["challenges"])
    assert all("고구마" not in c["reason"] for c in result["challenges"])


def _challenge_for_prompt():
    return {
        "challenge_id": "MOVE_2", "area_label": "이동", "title": "대중교통 2회", "points": 70,
        "selection": {"area_level": 4, "role": "top_area", "slot": 1},
    }
