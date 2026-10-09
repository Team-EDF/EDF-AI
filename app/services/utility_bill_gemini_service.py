"""Gemini Vision extraction for Korean apartment management-fee statements.

Comparison-only service: does not calculate emissions or change the existing OCR API.
Requires: google-genai, pydantic v2; GEMINI_API_KEY environment variable.
"""

import json
import os
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, model_validator


class BillItem(BaseModel):
    usage: float | None = None
    usage_unit: str | None = None
    cost: int | None = None
    evidence: str | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> "BillItem":
        if self.usage is not None and self.usage < 0:
            raise ValueError("사용량은 음수가 될 수 없습니다.")
        if self.cost is not None and self.cost < 0:
            raise ValueError("항목별 청구액은 음수가 될 수 없습니다.")
        if self.usage is None:
            self.usage_unit = None
        return self


class HeatingItem(BillItem):
    """Only individual household variable heating cost goes into `cost`."""

    basic_heating_cost: int | None = None
    common_heating_cost: int | None = None
    basic_heating_evidence: str | None = None
    common_heating_evidence: str | None = None


class GeminiUtilityBill(BaseModel):
    electricity: BillItem = Field(default_factory=BillItem)
    gas: BillItem = Field(default_factory=BillItem)
    water: BillItem = Field(default_factory=BillItem)
    district_heating: HeatingItem = Field(default_factory=HeatingItem)
    hot_water: BillItem = Field(default_factory=BillItem)
    sewer: BillItem = Field(default_factory=BillItem)
    management_fee_total: int | None = None
    management_fee_total_evidence: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_total(self) -> "GeminiUtilityBill":
        if self.management_fee_total is not None and self.management_fee_total < 0:
            raise ValueError("당월 관리비 총액은 음수가 될 수 없습니다.")
        if self.management_fee_total is not None and not self.management_fee_total_evidence:
            self.warnings.append("관리비 총액의 원문 근거가 없어 총액을 제외했습니다.")
            self.management_fee_total = None
        return self


PROMPT = """당신은 한국 아파트 관리비 고지서의 '정보 추출기'입니다.
이미지에서 눈으로 확인되는 글자만 추출하세요. 계산하거나 합산하거나 빈 값을 추측하지 마세요.
반드시 제공된 JSON 스키마에 맞춰 반환하세요.

[항목과 청구금액 연결]
- 실제 '당월 세대별 청구 내역'의 같은 행 또는 명확히 연결된 열을 사용하세요.
- 전년 동월 비교표, 에너지 사용 현황, 계량기 지침, 예시 표, 공용 사용료,
  미납액, 연체료, 납기 후 금액을 세대별 당월 청구액으로 혼동하지 마세요.
- electricity.cost에는 세대전기료만 넣으세요. 공동전기료, 승강기전기료,
  TV수신료를 더하지 마세요.
- water.cost에는 세대수도료만 넣으세요. 공동수도료, 하수도료를 더하지 마세요.
- gas.cost에는 해당 세대의 도시가스 사용료만 넣으세요.
- district_heating.cost에는 '세대 난방비/난방 사용료'만 넣으세요.
  '기본난방비'는 basic_heating_cost, '공동난방비'는 common_heating_cost에
  각각 별도 추출하세요. 이 세 금액을 절대로 합산하지 마세요.
- hot_water.cost에는 세대 온수/급탕 사용료만 넣으세요.
- 사용량(usage)과 요금(cost)은 서로 다른 숫자입니다.

[단위]
- 원본에서 확인되는 단위를 그대로 usage_unit에 기입하세요.
- kWh, MWh, Gcal, m³, Nm³, m², ㎡, t를 임의로 상호 변환하지 마세요.
- 숫자가 39,040원이라면 39.040 kWh처럼 사용량을 만들어내지 마세요.
- 사용량의 단위가 보이지 않으면 usage_unit은 null입니다.
- 원본에서 사용량 자체를 확인할 수 없다면 usage와 usage_unit 모두 null입니다.

[총액]
- management_fee_total은 '당월부과액', '당월 관리비', '납부할 금액' 등
  이미지에 명시적으로 표시된 해당 월의 총액만 추출하세요.
- 일부만 잘린 이미지, 개별 항목만 나온 이미지에 총액이 없다면 반드시 null입니다.
- 항목 금액을 더해 총액을 만들어내지 마세요.
- 납부할 금액에 미납금이 포함되면 당월 부과액과 구분하세요.
- 총액을 추출했다면 management_fee_total_evidence에 원문 표시를 적으세요.

[근거]
- evidence에는 사용량/금액이 실제 표시된 짧은 원문을 적으세요.
- evidence는 추론 설명이 아니라 이미지에 보이는 문자여야 합니다.
- 값이 잘 안 보이거나 항목 연결이 모호하면 null로 두고 warnings에 이유를 적으세요.
"""


class GeminiUtilityBillService:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("GEMINI_UTILITY_MODEL", "gemini-2.5-flash")

    def analyze(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        if not image_bytes:
            raise ValueError("이미지 데이터가 비어 있습니다.")
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("지원하는 이미지 형식은 JPEG, PNG, WEBP입니다.")
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 필요합니다.")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=[PROMPT, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GeminiUtilityBill,
                temperature=0,
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini가 분석 결과를 반환하지 않았습니다.")

        parsed = GeminiUtilityBill.model_validate(json.loads(response.text))
        usage = response.usage_metadata
        return {
            "status": "success",
            "engine": "gemini_vision",
            "model": self.model,
            "parsed_bill": parsed.model_dump(),
            "token_usage": {
                "input_tokens": getattr(usage, "prompt_token_count", None),
                "output_tokens": getattr(usage, "candidates_token_count", None),
                "thought_tokens": getattr(usage, "thoughts_token_count", None),
                "total_tokens": getattr(usage, "total_token_count", None),
            },
        }
