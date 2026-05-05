from pydantic import BaseModel


class MerchantClassifyRequest(BaseModel):
    merchant_name: str


class MerchantClassifyResponse(BaseModel):
    merchant_name: str
    industry_name: str | None
