"""
챌린지 추천 이유(reason) 문구 생성.

어떤 챌린지를 줄지는 challenge_selector(규칙)가 이미 정했다. 여기서는 "왜 이 챌린지인지"를
설명하는 한두 문장만 만든다.

- 기본은 LLM(Gemini)이 사용자 유형에 맞춰 자연스럽게 쓴다 (한 번 호출로 3개를 같이 생성).
- LLM이 실패하거나(키 없음/시간 초과/형식 오류) 문구가 검증을 통과하지 못하면
  챌린지별로 고정 문구(폴백)로 대체한다. 그래서 추천 응답은 항상 정상이다.
- LLM이 숫자를 지어내지 못하게, 응답 문구에 들어간 숫자가 입력으로 준 사실(레벨, 포인트, 횟수 등)에
  있는 값이 아니면 그 문구는 버리고 폴백을 쓴다. 난이도·포인트·절감량은 카탈로그의 고정값이다.
"""
import json
import logging
import os
import re
import threading
from typing import Callable

from app.services.profile_service import LEVEL_LABELS

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
# 호출 한 번이 보통 1~2초이고, 이 시간을 넘기면 고정 문구로 대체한다
LLM_TIMEOUT_MS = 6000
MAX_REASON_CHARS = 120

MAX_NAME_CHARS = 20

SOURCE_LLM = "llm"
SOURCE_FALLBACK = "fallback"

_client = None
_client_lock = threading.Lock()


def llm_enabled() -> bool:
    """CHALLENGE_LLM_ENABLED=false 이면 LLM을 쓰지 않고 항상 고정 문구를 쓴다."""
    return os.getenv("CHALLENGE_LLM_ENABLED", "true").strip().lower() != "false"


# ---------------------------------------------------------------- 폴백(고정 문구)

def sanitize_user_name(name) -> str | None:
    """
    사용자 이름을 문구에 안전하게 쓸 수 있게 다듬는다 (줄바꿈·특수문자 제거, 최대 MAX_NAME_CHARS자).
    이름이 프롬프트에 그대로 들어가므로 한글·영문·숫자·공백·점·밑줄·하이픈만 남긴다. 비면 None.
    """
    if not isinstance(name, str):
        return None
    cleaned = re.sub(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ ._-]", " ", name)
    cleaned = " ".join(cleaned.split())[:MAX_NAME_CHARS].strip()
    return cleaned or None


def build_fallback_reason(challenge: dict, user_name: str | None = None) -> str:
    """LLM 없이도 쓸 수 있는 고정 설명 문구 (같은 입력이면 같은 문구). 이름이 있으면 앞에 붙인다."""
    greeting = f"{user_name}님, " if user_name else ""
    selection = challenge.get("selection", {})
    role = selection.get("role")
    level = selection.get("area_level")
    area_label = challenge["area_label"]
    title = challenge["title"]
    level_label = LEVEL_LABELS.get(level) if isinstance(level, int) else None

    if role == "aux_life":
        return f"{greeting}생활 속 작은 습관이에요. 부담 없이 '{title}'로 가볍게 시작해 보세요."
    if role == "top_area" and level_label:
        return f"{greeting}{area_label} 영역이 '{level_label}'로 나와서 줄일 여지가 가장 커요. '{title}'부터 시작해 보세요."
    if level_label:
        return f"{greeting}{area_label} 영역도 '{level_label}' 수준이에요. 부담 없이 '{title}'로 이어가 보세요."
    return f"{greeting}{area_label} 영역에서 '{title}'로 작은 변화를 시작해 보세요."


# ---------------------------------------------------------------- LLM 호출

def _get_client():
    """Gemini 클라이언트를 한 번만 만든다. 팀의 피드백 서비스처럼 Vertex(GEMINI_PROJECT_ID)가 있으면 그걸, 없으면 API 키를 쓴다."""
    global _client
    with _client_lock:
        if _client is None:
            from dotenv import load_dotenv
            from google import genai
            from google.genai import types

            load_dotenv()
            http_options = types.HttpOptions(timeout=LLM_TIMEOUT_MS)
            project_id = os.getenv("GEMINI_PROJECT_ID")
            if project_id:
                _client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location=os.getenv("GEMINI_LOCATION", "us-central1"),
                    http_options=http_options,
                )
            else:
                _client = genai.Client(http_options=http_options)  # GEMINI_API_KEY / GOOGLE_API_KEY 사용
        return _client


def warm_up() -> None:
    """
    서버 기동 때 Gemini 클라이언트를 미리 만들어 둔다.
    클라이언트 준비(인증 등)에 첫 호출에서 약 9초가 걸려서, 미리 해 두지 않으면 첫 추천 요청이 느려진다.
    실패해도 서버 기동이나 추천에는 영향이 없다 (그때는 고정 문구를 쓴다).
    """
    if not llm_enabled():
        return
    try:
        _get_client()
    except Exception as e:
        logger.warning("챌린지 LLM warm-up 실패 (고정 문구로 동작): %s: %s", type(e).__name__, str(e)[:100])


def _call_gemini(prompt: str) -> str:
    from google.genai import types

    response = _get_client().models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.4,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text or ""


def _facts_lines(challenges: list[dict]) -> list[str]:
    """프롬프트에 넣는 사실 목록. 여기 적힌 숫자만 LLM 응답에 쓸 수 있다."""
    lines = []
    for c in challenges:
        level = c.get("selection", {}).get("area_level")
        level_text = f"{c['area_label']} 수준: {LEVEL_LABELS.get(level)}" if isinstance(level, int) else c["area_label"]
        lines.append(f"- id: {c['challenge_id']} | {level_text} | 챌린지: {c['title']} | 포인트 {c['points']}")
    return lines


def build_prompt(challenges: list[dict], persona: dict | None = None, user_name: str | None = None) -> str:
    """
    LLM에 줄 프롬프트. GSTI 유형 이름은 재미로 보는 결과라서 문구에 쓰지 않는다 (persona는 받지만 쓰지 않음).
    사용자 이름이 있으면 '{이름}님'으로 부를 수 있게 알려 준다.
    """
    if user_name:
        user_text = f"이름: {user_name}"
        name_rule = f"사용자를 부를 때는 '{user_name}님'이라고 하고, 모든 문구에 넣지 말고 필요할 때만 쓴다."
    else:
        user_text = "이름을 모름"
        name_rule = "사용자의 이름은 모르니 이름을 지어내지 않는다."
    facts = "\n".join(_facts_lines(challenges))
    return f"""너는 탄소 발자국 앱 'GreenStep'의 챌린지 추천 도우미다.

[사용자]
{user_text}

[추천할 챌린지 (아래 사실은 바꾸지 말 것)]
{facts}

작성 규칙:
1. 챌린지마다 이 사용자에게 왜 맞는지 한국어 한두 문장({MAX_REASON_CHARS}자 이내)으로 쓴다. 친근한 말투(~요)를 쓴다.
2. 위에 적힌 숫자 외에 새로운 숫자(절감량, 퍼센트, 횟수, 일수 등)를 절대 만들지 않는다.
3. 챌린지 이름과 포인트를 바꾸지 않는다. 의학적·과학적 단정은 하지 않는다.
4. {name_rule} 성향·유형 이름이나 그것을 빗댄 별명(식물·동물 비유 등)은 언급하지 않는다.
5. 출력은 JSON 객체 하나만 쓴다. 키는 id, 값은 문구다. 예: {{"MOVE_2": "문구", "FOOD_1": "문구"}}"""


# ---------------------------------------------------------------- 검증

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _allowed_numbers(challenges: list[dict], user_name: str | None = None) -> set[str]:
    """프롬프트 사실에 등장하는 숫자들 (id의 숫자 포함). 이름에 숫자가 있으면(예: user1) 그것도 허용한다."""
    return set(_NUMBER.findall(" ".join(_facts_lines(challenges)) + " " + (user_name or "")))


def _parse_json(text: str) -> dict:
    """응답에서 JSON 객체를 꺼낸다 (코드 펜스가 붙어 와도 처리)."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아님")
    return data


def _valid_reason(text, allowed: set[str]) -> bool:
    if not isinstance(text, str):
        return False
    text = text.strip()
    if not text or len(text) > MAX_REASON_CHARS or "\n" in text:
        return False
    return all(number in allowed for number in _NUMBER.findall(text))


# ---------------------------------------------------------------- 메인

def generate_reasons(
    challenges: list[dict],
    persona: dict | None = None,
    llm_call: Callable[[str], str] | None = None,
    user_name: str | None = None,
) -> list[dict]:
    """
    선택된 챌린지마다 추천 이유를 만든다. 반환: challenges와 같은 순서의 [{"reason", "reason_source"}].
    LLM이 실패하거나 문구가 검증을 통과하지 못하면 그 항목은 고정 문구를 쓴다.
    llm_call은 테스트에서 가짜 LLM을 끼우기 위한 자리다 (기본은 Gemini 호출).
    """
    user_name = sanitize_user_name(user_name)
    fallback = [{"reason": build_fallback_reason(c, user_name), "reason_source": SOURCE_FALLBACK} for c in challenges]
    if not challenges or not llm_enabled():
        return fallback

    try:
        raw = (llm_call or _call_gemini)(build_prompt(challenges, persona, user_name))
        data = _parse_json(raw)
    except Exception as e:
        # 키 없음, 시간 초과, 형식 오류 등 어떤 실패든 추천 자체는 막지 않는다
        logger.warning("챌린지 추천 이유 LLM 실패 (고정 문구 사용): %s: %s", type(e).__name__, str(e)[:100])
        return fallback

    allowed = _allowed_numbers(challenges, user_name)
    result = []
    for c, fb in zip(challenges, fallback):
        text = data.get(c["challenge_id"])
        if _valid_reason(text, allowed):
            result.append({"reason": text.strip(), "reason_source": SOURCE_LLM})
        else:
            result.append(fb)
    return result
