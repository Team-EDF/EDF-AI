# 탄소배출량 소수점 반올림 자릿수.
# calculate_carbon 호출 즉시 반올림해 periodic_stats 누적 시 float 오차가 증폭되는 것을 방지한다.
CARBON_ROUND_DIGITS = 6


def calculate_carbon(co2eq_KRW: float | None, amount_krw: float) -> float | None:
    """카테고리 탄소계수(kgCO2eq/KRW) × 결제금액(KRW) = 탄소배출량(kgCO2eq)"""
    if co2eq_KRW is None or amount_krw <= 0:
        return None

    # 서비스 레이어에서 반올림 원천 적용: DB 누적 통계의 소수점 오차 방지
    return round(co2eq_KRW * amount_krw, CARBON_ROUND_DIGITS)