"""
관리비 고지서 분석 API 응답 스키마.
"""

from typing import Any
from pydantic import BaseModel, Field


class UtilityUsageInfo(BaseModel):
    usage: float | None = None
    usage_unit: str | None = None
    cost: float | None = None


class UtilitySimpleUsageInfo(BaseModel):
    usage: float | None = None
    usage_unit: str | None = None


class HotWaterInfo(BaseModel):
    usage: float | None = None
    source: str | None = None
    energy_unit: str | None = None
    included_in_total: bool | None = None
    carbon_calculation_supported: bool = False


class SewerInfo(BaseModel):
    usage: float | None = None
    usage_unit: str | None = None
    carbon_calculation_supported: bool = False


class UtilityBillData(BaseModel):
    electricity: UtilityUsageInfo
    gas: UtilityUsageInfo
    water: UtilityUsageInfo
    district_heating: UtilitySimpleUsageInfo
    hot_water: HotWaterInfo
    sewer: SewerInfo
    management_fee_total: float | None = None


class ParsedUtilityBill(BaseModel):
    status: str
    data: UtilityBillData

    # 규칙 기반 Parser 결과의 품질 지표.
    parser_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # True이면 원본 이미지를 Gemini Vision으로 다시 구조화할 수 있다.
    fallback_recommended: bool = False

    warnings: list[str] = Field(default_factory=list)


class CarbonItem(BaseModel):
    item: str
    status: str
    input_value: float | None = None
    factor: float | None = None
    factor_unit: str | None = None
    carbon_basis: str | None = None
    carbon_kg: float | None = None
    calculation_type: str | None = None
    source: str | None = None
    fallback_used: bool | None = None
    priority_used: int | None = None
    fallback_from: str | None = None
    message: str | None = None


class UtilityCarbonResult(BaseModel):
    status: str
    items: list[CarbonItem] = Field(default_factory=list)
    total_carbon_kg: float = 0.0
    carbon_bases: list[str] = Field(default_factory=list)
    detailed_calculation_used: bool = False
    total_fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class UtilityBillAnalyzeResponse(BaseModel):
    status: str
    ocr_engine: str
    raw_text: str | None = None
    parsed_bill: ParsedUtilityBill | None = None
    carbon_result: UtilityCarbonResult | None = None
    warnings: list[str] = Field(default_factory=list)


class UtilityBillErrorResponse(BaseModel):
    status: str = "ocr_failed"
    ocr_engine: str = "none"
    raw_text: str | None = None
    parsed_bill: Any | None = None
    carbon_result: Any | None = None
    warnings: list[str] = Field(default_factory=list)
