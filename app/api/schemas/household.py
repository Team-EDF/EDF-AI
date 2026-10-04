from typing import Optional

from pydantic import BaseModel, Field


class BillValues(BaseModel):
    electricity_kwh: Optional[float] = None
    electricity_krw: Optional[int] = None
    water_m3: Optional[float] = None
    water_krw: Optional[int] = None
    gas_m3: Optional[float] = None
    gas_krw: Optional[int] = None
    heat_gcal: Optional[float] = None
    heat_krw: Optional[int] = None


class BillReadResponse(BaseModel):
    readable: bool
    code: str                    # OK | NOT_A_BILL | UNREADABLE | EDITED | MONTH_UNKNOWN | MONTH_OUT_OF_RANGE | NO_VALUES
    message: str                 # 사용자에게 그대로 보여 줄 안내
    bill_month: Optional[str] = None      # 사용(검침) 월 YYYY-MM
    values: Optional[BillValues] = None
    total_krw: Optional[int] = None
    fingerprint: Optional[str] = None     # 같은 고지서 재사용 방지용 해시 (월 + 읽은 값)
    note: Optional[str] = None


class CarbonRequest(BillValues):
    pass


class CarbonItem(BaseModel):
    key: str                     # electricity | water | gas | heat
    label: str
    usage: Optional[float] = None
    unit: str
    krw: Optional[int] = None
    carbon_kg: float
    basis: str                   # usage(사용량 기준) | spend(금액으로 추정) | none


class CarbonResponse(BaseModel):
    total_kg: float
    items: list[CarbonItem]
    estimated: bool = Field(description="금액으로 추정한 항목이 있으면 true")
    note: Optional[str] = None
