"""
관리비 고지서 OCR 서비스.

관리비 고지서 이미지에서 Google Vision을 이용해
텍스트를 추출하고 UtilityBillParser로 구조화한다.

영수증 OCR의 범용 텍스트 추출 기능은 재사용하지만,
영수증 전용 parser / 품질 판정 로직은 사용하지 않는다.
"""

import logging
from typing import Any

from app.services.ocr_service import extract_text_from_image
from app.services.utility_bill_parser import utility_bill_parser
from app.services.utility_carbon_service import (
    utility_carbon_service,
)


logger = logging.getLogger(__name__)


class UtilityBillOcrService:
    """
    관리비 고지서 OCR 처리 서비스.
    """

    MIN_TEXT_LENGTH = 20

    def extract(
        self,
        image_bytes: bytes,
    ) -> dict[str, Any]:
        """
        관리비 고지서 이미지에서 텍스트를 추출하고
        관리비 항목을 구조화한다.
        """

        if not image_bytes:
            return {
                "status": "ocr_failed",
                "ocr_engine": "none",
                "raw_text": None,
                "parsed_bill": None,
                "warnings": [
                    "이미지 데이터가 없습니다."
                ],
            }

        try:
            raw_text = extract_text_from_image(
                image_bytes
            )

        except Exception as error:
            logger.exception(
                "[관리비 OCR] Google Vision 호출 실패"
            )

            return {
                "status": "ocr_failed",
                "ocr_engine": "google_vision",
                "raw_text": None,
                "parsed_bill": None,
                "warnings": [
                    f"Google Vision OCR 처리에 실패했습니다: "
                    f"{error}"
                ],
            }

        if not raw_text:
            return {
                "status": "ocr_failed",
                "ocr_engine": "google_vision",
                "raw_text": None,
                "parsed_bill": None,
                "warnings": [
                    "관리비 고지서에서 텍스트를 "
                    "추출하지 못했습니다."
                ],
            }

        text_length = len(
            raw_text
            .replace(" ", "")
            .replace("\n", "")
        )

        if text_length < self.MIN_TEXT_LENGTH:
            return {
                "status": "ocr_failed",
                "ocr_engine": "google_vision",
                "raw_text": raw_text,
                "parsed_bill": None,
                "warnings": [
                    "OCR로 추출된 텍스트가 너무 적습니다."
                ],
            }

        parsed_bill = utility_bill_parser.parse(
            raw_text
        )

        # Parser 결과를 기반으로 탄소배출량 계산
        carbon_result = (
            utility_carbon_service
            .calculate_from_parsed_bill(
                parsed_bill
            )
        )

        parser_warnings = parsed_bill.get(
            "warnings",
            [],
        )

        carbon_warnings = carbon_result.get(
            "warnings",
            [],
        )

        warnings = list(
            dict.fromkeys(
                parser_warnings + carbon_warnings
            )
        )

        logger.info(
            "[관리비 OCR] Google Vision 처리 완료 "
            "(text_length=%s, carbon=%s)",
            text_length,
            carbon_result.get(
                "total_carbon_kg"
            ),
        )

        return {
            "status": "success",
            "ocr_engine": "google_vision",
            "raw_text": raw_text,
            "parsed_bill": parsed_bill,
            "carbon_result": carbon_result,
            "warnings": warnings,
        }


utility_bill_ocr_service = UtilityBillOcrService()