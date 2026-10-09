import re
from typing import Any


class UtilityBillParser:
    """
    관리비 고지서 OCR 텍스트에서 에너지/수도 사용량 및 요금을 추출한다.

    특정 고지서 한 장의 숫자/배치를 하드코딩하지 않고,
    항목 별칭, 단위, 인접 행, 제외어를 이용해 일반적으로 파싱한다.
    """

    CONFIG = {
        "electricity": {
            "aliases": [
                "전기사용량", "전기 사용량",
                "전력사용량", "전력 사용량",
                "세대전기료", "세대전기", "세대 전기",
                "전기",
            ],
            "cost_aliases": [
                "세대전기료", "전기요금", "전기 요금",
                "전기료", "전기비", "세대전기", "세대 전기",
                "전기",
            ],
            "units": ["kWh"],
            "exclude": [
                "공동전기", "공동 전기",
                "승강기전기", "승강기 전기",
                "가로등전기", "가로등 전기",
                "TV수신료", "TV 수신료",
            ],
        },
        "gas": {
            "aliases": [
                "도시가스사용량", "도시가스 사용량",
                "가스사용량", "가스 사용량",
                "도시가스", "가스",
            ],
            "cost_aliases": [
                "도시가스사용료", "도시가스 사용료",
                "도시가스요금", "도시가스 요금",
                "도시가스료", "도시가스비",
                "가스요금", "가스료",
                "도시가스", "가스",
            ],
            "units": ["Nm³", "m³"],
            "exclude": ["공동가스", "공동 가스"],
        },
        "water": {
            "aliases": [
                "수도사용량", "수도 사용량",
                "상수도사용량", "상수도 사용량",
                "세대수도료", "세대수도", "세대 수도",
                "상수도", "수도",
            ],
            "cost_aliases": [
                "세대수도료", "수도요금", "수도 요금",
                "수도료", "수도비", "상수도료",
                "세대수도", "세대 수도",
                "상수도", "수도",
            ],
            "units": ["m³"],
            "exclude": [
                "공동수도", "공동 수도",
                "하수도", "하수",
                "물이용부담금",
            ],
        },
        "district_heating": {
            "aliases": [
                "지역난방사용량", "지역난방 사용량",
                "난방사용량", "난방 사용량",
                "세대난방비", "지역난방", "난방",
            ],
            "cost_aliases": [
                "세대난방비", "난방요금", "난방 요금",
                "난방비", "지역난방비",
                "지역난방", "난방",
            ],
            "units": ["Gcal", "MWh"],
            "exclude": [
                "공동난방", "공동 난방",
                "기본난방", "기본 난방",
            ],
        },
        "hot_water": {
            "aliases": [
                "급탕사용량", "급탕 사용량",
                "온수사용량", "온수 사용량",
                "세대급탕비", "급탕", "온수",
            ],
            "cost_aliases": [
                "세대급탕비", "급탕요금", "급탕 요금",
                "급탕비", "온수사용료", "온수 사용료",
                "온수요금", "온수 요금", "온수비",
                "급탕", "온수",
            ],
            "units": ["m³", "Gcal", "kWh", "Nm³", "MWh"],
            "exclude": [
                "공동급탕", "공동 급탕",
                "공동온수", "공동 온수",
            ],
        },
        "sewer": {
            "aliases": [
                "하수도사용량", "하수도 사용량",
                "하수사용량", "하수 사용량",
                "하수도", "하수",
            ],
            "cost_aliases": [
                "하수도료", "하수도요금", "하수도 요금",
                "하수료", "하수요금",
                "오폐수처리비", "오폐수 처리비",
            ],
            "units": ["m³"],
            "exclude": [],
        },
    }

    TOTAL_ALIASES = [
        "당월부과액계", "당월 부과액계",
        "당월부과액", "당월 부과액",
        "당월관리비", "당월 관리비",
        "관리비총액", "관리비 총액",
        "납부금액", "납부 금액",
        "납기내금액", "납기내 금액",
        "총납부금액", "총 납부금액",
        "납부하실금액", "납부하실 금액",
        "납부해서 금액",
    ]

    COMPARISON_WORDS = [
        "전월", "전년", "전년도", "전년동월",
        "비교", "대비", "평균", "작년", "지난달",
    ]

    NUMBER = r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"

    def parse(self, raw_text: str) -> dict[str, Any]:
        if not raw_text or not raw_text.strip():
            return self._empty_result("OCR 텍스트가 비어 있습니다.")

        text = self._normalize_text(raw_text)
        lines = [x.strip() for x in text.split("\n") if x.strip()]

        found: dict[str, dict[str, Any]] = {}

        for name, config in self.CONFIG.items():
            found[name] = {
                "usage_result": self._extract_usage(lines, config, name),
                "cost_result": self._extract_cost(lines, config),
            }

        total_result = self._extract_total(lines)

        def usage(name: str):
            result = found[name]["usage_result"]
            return result["value"] if result else None

        def unit(name: str):
            result = found[name]["usage_result"]
            return result["unit"] if result else None

        def cost(name: str):
            result = found[name]["cost_result"]
            return result["value"] if result else None

        hot_water = self._build_hot_water_info(
            text,
            usage("hot_water"),
            unit("hot_water"),
        )

        parsed = {
            "electricity": {
                "usage": usage("electricity"),
                "usage_unit": unit("electricity"),
                "cost": cost("electricity"),
            },
            "gas": {
                "usage": usage("gas"),
                "usage_unit": unit("gas"),
                "cost": cost("gas"),
            },
            "water": {
                "usage": usage("water"),
                "usage_unit": unit("water"),
                "cost": cost("water"),
            },
            "district_heating": {
                "usage": usage("district_heating"),
                "usage_unit": unit("district_heating"),
            },
            "hot_water": hot_water,
            "sewer": {
                "usage": usage("sewer"),
                "usage_unit": unit("sewer"),
                "carbon_calculation_supported": False,
            },
            "management_fee_total": (
                total_result["value"] if total_result else None
            ),
        }

        confidence = self._confidence(parsed)
        fallback = self._fallback_recommended(
            parsed=parsed,
            confidence=confidence,
            text=text,
        )

        warnings = self._build_warnings(parsed)

        if fallback:
            warnings.append(
                "핵심 관리비 정보의 추출 신뢰도가 낮아 "
                "Gemini Vision fallback을 권장합니다."
            )

        return {
            "status": "parsed",
            "data": parsed,
            "parser_confidence": confidence,
            "fallback_recommended": fallback,
            "warnings": warnings,
        }

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\t", " ")
        )

        text = re.sub(r"(?i)k\s*w\s*h", "kWh", text)
        text = re.sub(r"(?i)m\s*w\s*h", "MWh", text)
        text = re.sub(r"(?i)g\s*c\s*a\s*l", "Gcal", text)

        text = text.replace("㎥", "m³")

        text = re.sub(r"(?i)\bnm\s*3\b", "Nm³", text)
        text = re.sub(r"(?i)\bm\s*3\b", "m³", text)

        return "\n".join(
            re.sub(r" {2,}", " ", line).strip()
            for line in text.split("\n")
        )

    def _extract_usage(
        self,
        lines: list[str],
        config: dict[str, Any],
        category: str,
    ) -> dict[str, Any] | None:
        candidates = []

        for i, line in enumerate(lines):
            if not self._contains(line, config["aliases"]):
                continue

            if self._contains(line, config["exclude"]):
                continue

            if self._contains(line, self.COMPARISON_WORDS):
                continue

            result = self._usage_from_line(
                line=line,
                units=config["units"],
                category=category,
            )

            if result:
                result["score"] += 4
                candidates.append(result)

            for distance in (1, 2):
                j = i + distance

                if j >= len(lines):
                    break

                next_line = lines[j]

                if self._is_other_category(next_line, config):
                    break

                result = self._usage_from_line(
                    line=next_line,
                    units=config["units"],
                    category=category,
                )

                if result:
                    result["score"] += 3 - distance
                    candidates.append(result)
                    break

        return (
            max(candidates, key=lambda x: x["score"])
            if candidates
            else None
        )

    def _usage_from_line(
        self,
        line: str,
        units: list[str],
        category: str,
    ) -> dict[str, Any] | None:
        patterns: list[tuple[str, str]] = []

        for unit in units:
            if unit == "kWh":
                patterns.extend([
                    (r"kWh", "kWh"),
                    (r"kwh", "kWh"),
                    (r"kw", "kWh"),
                ])

                # Google Vision에서 kWh가 kh/km 등으로 깨지는 경우가 있다.
                # 전기 항목을 탐색하는 문맥에서만 OCR 변형으로 인정한다.
                if category == "electricity":
                    patterns.extend([
                        (r"kh", "kWh"),
                        (r"km", "kWh"),
                    ])

            elif unit == "MWh":
                patterns.append((r"MWh", "MWh"))

            elif unit == "Gcal":
                patterns.append((r"Gcal", "Gcal"))

            elif unit == "Nm³":
                patterns.extend([
                    (r"Nm³", "Nm³"),
                    (r"Nm\s*[³3]", "Nm³"),
                ])

            elif unit == "m³":
                patterns.extend([
                    (r"m³", "m³"),
                    (r"m\s*[³3]", "m³"),
                    (r"㎡", "m³"),
                    (r"m²", "m³"),
                ])

                # 수도 계량에서는 1 t의 물을 약 1 m³로 취급할 수 있다.
                # 다른 항목의 t 단위를 잘못 변환하지 않도록 수도 문맥에서만 허용한다.
                if category == "water":
                    patterns.append((r"t\b", "m³"))

        for pattern, canonical in patterns:
            match = re.search(
                rf"{self.NUMBER}\s*{pattern}",
                line,
                re.IGNORECASE,
            )

            if not match:
                continue

            value = self._to_float(match.group(1))

            if value is not None and 0 <= value < 1_000_000:
                return {
                    "value": value,
                    "unit": canonical,
                    "score": 5,
                }

        return None

    def _extract_cost(
        self,
        lines: list[str],
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates = []

        for i, line in enumerate(lines):
            alias = self._find_alias(line, config["cost_aliases"])

            if alias is None:
                continue

            if self._contains(line, config["exclude"]):
                continue

            if self._contains(line, self.COMPARISON_WORDS):
                continue

            # 가장 중요한 규칙:
            # 해당 항목 alias보다 앞에 있는 숫자는 이전 항목의 금액일 수 있다.
            #
            # 예:
            #   48,370 세대전기료
            #   15,160
            #
            # 48,370은 일반관리비이고 15,160이 세대전기료다.
            after_alias = self._text_after_alias(line, alias)

            if after_alias:
                cleaned = self._remove_usage(after_alias)
                same_line_value = self._money(cleaned)

                if same_line_value is not None:
                    candidates.append({
                        "value": same_line_value,
                        "score": 10,
                    })

            # 같은 줄에 사용량이 있고 금액이 없다면 다음 줄의 금액을 우선한다.
            #
            # 예:
            #   세대전기료 171kWh
            #   8,970
            for distance in (1, 2):
                j = i + distance

                if j >= len(lines):
                    break

                next_line = lines[j]

                # 다음 카테고리가 바로 시작하면 현재 항목의 금액 탐색 종료.
                if self._is_any_category(next_line):
                    break

                value = self._money(
                    next_line,
                    allow_plain=True,
                    allow_dot_thousands=True,
                )

                if value is not None:
                    candidates.append({
                        "value": value,
                        "score": 9 - distance,
                    })
                    break

        return (
            max(candidates, key=lambda x: x["score"])
            if candidates
            else None
        )

    def _extract_total(
        self,
        lines: list[str],
    ) -> dict[str, Any] | None:
        candidates = []

        for i, line in enumerate(lines):
            alias = self._find_alias(line, self.TOTAL_ALIASES)

            if alias is None:
                continue

            if self._contains(line, self.COMPARISON_WORDS):
                continue

            # 당월부과액 앞에 있는 숫자는 다른 항목의 금액일 수 있으므로
            # alias 뒤쪽만 검사한다.
            #
            # 예:
            #   10,390 당월부과액
            #   900,500원
            after_alias = self._text_after_alias(line, alias)

            if after_alias:
                same_line_value = self._money(
                    after_alias,
                    allow_plain=True,
                    allow_dot_thousands=True,
                )

                if same_line_value is not None:
                    candidates.append({
                        "value": same_line_value,
                        "score": 12,
                    })

            # 다음 행의 금액을 높은 우선순위로 사용한다.
            for distance in (1, 2):
                j = i + distance

                if j >= len(lines):
                    break

                value = self._money(
                    lines[j],
                    allow_plain=True,
                    allow_dot_thousands=True,
                )

                if value is not None:
                    candidates.append({
                        "value": value,
                        "score": 11 - distance,
                    })
                    break

        return (
            max(candidates, key=lambda x: x["score"])
            if candidates
            else None
        )

    @staticmethod
    def _money(
        line: str,
        allow_plain: bool = False,
        allow_dot_thousands: bool = False,
    ) -> float | None:
        # 명시적인 원화 금액을 가장 먼저 찾는다.
        matches = re.findall(
            r"(?<![\d.])"
            r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,9})"
            r"\s*원(?!\w)",
            line,
        )

        if matches:
            return UtilityBillParser._to_float(matches[-1])

        # 50,000 형태
        matches = re.findall(
            r"(?<![\d.])([0-9]{1,3}(?:,[0-9]{3})+)(?![\d.])",
            line,
        )

        if matches:
            return UtilityBillParser._to_float(matches[-1])

        # OCR에서 35,980원이 35.980처럼 인식되는 경우.
        if allow_dot_thousands:
            matches = re.findall(
                r"(?<![\d.])([0-9]{1,3}(?:\.[0-9]{3})+)(?![\d.])",
                line,
            )

            if matches:
                return UtilityBillParser._to_float(
                    matches[-1].replace(".", "")
                )

        if allow_plain:
            value = line.replace("원", "").strip()

            if re.fullmatch(r"[0-9]{3,9}", value):
                return UtilityBillParser._to_float(value)

        return None

    @staticmethod
    def _remove_usage(line: str) -> str:
        return re.sub(
            r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
            r"\s*"
            r"(?:kWh|kwh|kw|kh|km|MWh|Gcal|Nm[³3]|m[³3²2]|㎡|t\b)",
            " ",
            line,
            flags=re.IGNORECASE,
        )

    def _find_alias(
        self,
        line: str,
        aliases: list[str],
    ) -> str | None:
        compact_line = re.sub(r"\s+", "", line).lower()

        # 더 구체적인 긴 alias부터 검사한다.
        for alias in sorted(aliases, key=len, reverse=True):
            compact_alias = re.sub(r"\s+", "", alias).lower()

            if compact_alias in compact_line:
                return alias

        return None

    @staticmethod
    def _text_after_alias(line: str, alias: str) -> str:
        """
        공백 차이가 있어도 alias 이후의 문자열을 반환한다.

        예:
            "48,370 세대전기료 171kWh"
            alias="세대전기료"
            -> " 171kWh"
        """
        alias_chars = [re.escape(char) for char in alias if not char.isspace()]

        if not alias_chars:
            return ""

        pattern = r"\s*".join(alias_chars)
        match = re.search(pattern, line, re.IGNORECASE)

        if not match:
            return ""

        return line[match.end():]

    def _is_any_category(self, line: str) -> bool:
        for config in self.CONFIG.values():
            aliases = config["aliases"] + config["cost_aliases"]

            if self._contains(line, aliases):
                return True

        return False

    def _is_other_category(
        self,
        line: str,
        current: dict[str, Any],
    ) -> bool:
        for config in self.CONFIG.values():
            if config is current:
                continue

            aliases = config["aliases"] + config["cost_aliases"]

            if self._contains(line, aliases):
                return True

        return False

    @staticmethod
    def _contains(text: str, words: list[str]) -> bool:
        compact = re.sub(r"\s+", "", text).lower()

        return any(
            re.sub(r"\s+", "", word).lower() in compact
            for word in words
        )

    def _build_hot_water_info(
        self,
        text: str,
        usage: float | None,
        detected_unit: str | None,
    ) -> dict[str, Any]:
        source = None
        context = self._find_context(text, ["급탕", "온수"])

        if context:
            lower = context.lower()

            if "지역난방" in context or "gcal" in lower:
                source = "district_heating"
            elif "도시가스" in context or "nm³" in lower:
                source = "gas"
            elif "전기" in context or "kwh" in lower:
                source = "electricity"

        supported = (
            usage is not None
            and source is not None
            and detected_unit in {"Gcal", "Nm³", "kWh"}
        )

        return {
            "usage": usage,
            "source": source,
            "energy_unit": detected_unit,
            "included_in_total": None,
            "carbon_calculation_supported": supported,
        }

    @staticmethod
    def _find_context(
        text: str,
        keywords: list[str],
    ) -> str | None:
        lines = text.split("\n")

        for i, line in enumerate(lines):
            if any(keyword in line for keyword in keywords):
                start = max(0, i - 1)
                end = min(len(lines), i + 3)
                return " ".join(lines[start:end])

        return None

    @staticmethod
    def _confidence(parsed: dict[str, Any]) -> float:
        score = 0.0

        for key in ("electricity", "gas", "water"):
            if parsed[key]["usage"] is not None:
                score += 0.22
            elif parsed[key]["cost"] is not None:
                score += 0.08

        if parsed["district_heating"]["usage"] is not None:
            score += 0.22

        if parsed["hot_water"]["usage"] is not None:
            score += 0.08

        if parsed["management_fee_total"] is not None:
            score += 0.12

        return round(min(score, 1.0), 2)

    def _fallback_recommended(
        self,
        parsed: dict[str, Any],
        confidence: float,
        text: str,
    ) -> bool:
        # 1. 전체 추출 신뢰도가 낮으면 fallback.
        if confidence < 0.60:
            return True

        # 2. 사용량을 하나도 추출하지 못한 경우.
        usages = [
            parsed["electricity"]["usage"],
            parsed["gas"]["usage"],
            parsed["water"]["usage"],
            parsed["district_heating"]["usage"],
            parsed["hot_water"]["usage"],
        ]

        if all(value is None for value in usages):
            return True

        # 3. OCR에는 주요 항목이 있는데 사용량과 요금 모두 못 잡은 경우.
        important_categories = {
            "electricity": ["세대전기", "전기사용량", "전기"],
            "gas": ["도시가스", "가스사용량"],
            "water": ["세대수도", "수도사용량", "수도"],
        }

        for category, keywords in important_categories.items():
            if not self._contains(text, keywords):
                continue

            item = parsed[category]

            if (
                item["usage"] is None
                and item["cost"] is None
            ):
                return True

        # 4. 당월부과액 계열 문구가 있는데 총액을 못 잡은 경우.
        if (
            self._contains(text, self.TOTAL_ALIASES)
            and parsed["management_fee_total"] is None
        ):
            return True

        return False

    @staticmethod
    def _build_warnings(parsed: dict[str, Any]) -> list[str]:
        warnings = []

        hot = parsed["hot_water"]

        if hot["usage"] is not None and hot["source"] is None:
            warnings.append(
                "급탕/온수 사용량은 인식했지만 실제 열원을 확인하지 못했습니다."
            )

        if (
            hot["usage"] is not None
            and hot["energy_unit"] == "m³"
        ):
            warnings.append(
                "온수 m³ 값은 물 사용량이므로 "
                "열에너지 배출계수에 직접 적용하지 않습니다."
            )

        if parsed["sewer"]["usage"] is not None:
            warnings.append(
                "하수 사용량은 인식했지만 현재 탄소 계산에서는 제외합니다."
            )

        return warnings

    @staticmethod
    def _to_float(value: str) -> float | None:
        try:
            return float(value.replace(",", "").strip())
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _empty_result(warning: str) -> dict[str, Any]:
        return {
            "status": "empty",
            "data": {
                "electricity": {
                    "usage": None,
                    "usage_unit": None,
                    "cost": None,
                },
                "gas": {
                    "usage": None,
                    "usage_unit": None,
                    "cost": None,
                },
                "water": {
                    "usage": None,
                    "usage_unit": None,
                    "cost": None,
                },
                "district_heating": {
                    "usage": None,
                    "usage_unit": None,
                },
                "hot_water": {
                    "usage": None,
                    "source": None,
                    "energy_unit": None,
                    "included_in_total": None,
                    "carbon_calculation_supported": False,
                },
                "sewer": {
                    "usage": None,
                    "usage_unit": None,
                    "carbon_calculation_supported": False,
                },
                "management_fee_total": None,
            },
            "parser_confidence": 0.0,
            "fallback_recommended": True,
            "warnings": [warning],
        }


utility_bill_parser = UtilityBillParser()