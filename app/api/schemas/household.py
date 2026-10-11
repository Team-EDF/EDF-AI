"""가정 에너지 고지서 판독 및 탄소배출량 API 응답 스키마."""

from typing import Optional

from pydantic import BaseModel, Field


class BillValues(BaseModel):
    electricity_kwh: Optional[float] = None
    electricity_krw: Optional[int] = None
    water_m3: Optional[float] = None
    water_krw: Optional[int] = None
    gas_m3: Optional[float] = None
    gas_krw: Optional[int] = None

    # 원본 난방 사용량/단위: Gcal로 환산할 수 없는 m3 등도 보존한다.
    heat_usage: Optional[float] = Field(default=None, description="고지서에 인쇄된 난방 사용량")
    heat_unit: Optional[str] = Field(default=None, description="난방 원본 단위 (Gcal, Mcal, GJ, MJ, kWh, MWh, m3 등)")
    heat_gcal: Optional[float] = Field(default=None, description="열량 단위로 환산 가능한 경우의 Gcal 값")
    heat_krw: Optional[int] = None


class BillReadResponse(BaseModel):
    readable: bool
    code: str  # OK | NOT_A_BILL | UNREADABLE | EDITED | MONTH_UNKNOWN | MONTH_OUT_OF_RANGE | NO_VALUES
    message: str
    bill_month: Optional[str] = None
    values: Optional[BillValues] = None
    total_krw: Optional[int] = None
    fingerprint: Optional[str] = None
    note: Optional[str] = None


class CarbonRequest(BillValues):
    pass


class CarbonItem(BaseModel):
    key: str  # electricity | water | gas | heat
    label: str
    usage: Optional[float] = None
    unit: str
    krw: Optional[int] = None
    carbon_kg: float
    basis: str  # usage | spend | none


class CarbonResponse(BaseModel):
    total_kg: float
    items: list[CarbonItem]
    estimated: bool = Field(description="금액으로 추정한 항목이 있으면 true")
    note: Optional[str] = None


class HouseholdAnalyzeResponse(BaseModel):
    bill: BillReadResponse
    carbon: CarbonResponse | None = None
