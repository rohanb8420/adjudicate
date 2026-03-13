from __future__ import annotations

from datetime import datetime
from typing import Any


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def calculate_amount_financed(
    sale_price: float,
    taxes: float,
    fees: float,
    cash_down_payment: float,
    trade_in_value: float,
) -> float:
    return max(sale_price + taxes + fees - cash_down_payment - trade_in_value, 0.0)


def calculate_monthly_payment(amount_financed: float, apr: float, term_months: int) -> float:
    if term_months <= 0:
        return 0.0
    monthly_rate = max(apr, 0.0) / 100 / 12
    if monthly_rate == 0:
        return amount_financed / term_months
    factor = (1 + monthly_rate) ** term_months
    return amount_financed * monthly_rate * factor / (factor - 1)


def calculate_ltv(amount_financed: float, book_value: float) -> float:
    return safe_divide(amount_financed, max(book_value, 1.0))


def calculate_pti(monthly_payment: float, gross_monthly_income: float) -> float:
    return safe_divide(monthly_payment, max(gross_monthly_income, 1.0))


def calculate_dti(other_monthly_debt: float, monthly_payment: float, gross_monthly_income: float) -> float:
    return safe_divide(other_monthly_debt + monthly_payment, max(gross_monthly_income, 1.0))


def calculate_residual_income(
    gross_monthly_income: float,
    other_monthly_debt: float,
    monthly_payment: float,
    baseline_living_cost_ratio: float = 0.35,
) -> float:
    living_cost = gross_monthly_income * baseline_living_cost_ratio
    return gross_monthly_income - other_monthly_debt - monthly_payment - living_cost


def calculate_valuation_gap_pct(sale_price: float, book_value: float) -> float:
    return safe_divide(sale_price - book_value, max(book_value, 1.0))


def calculate_vehicle_age(vehicle_year: int, reference_year: int | None = None) -> int:
    year = reference_year or datetime.now().year
    return max(year - vehicle_year, 0)


def calculate_collateral_quality_score(application: dict[str, Any], metrics: dict[str, Any]) -> float:
    score = 100.0
    score -= clamp(metrics["ltv"] - 1.0, 0.0, 0.5) * 100
    score -= clamp(application["mileage"] / 220_000, 0.0, 1.0) * 25
    score -= clamp(metrics["vehicle_age"] / 15, 0.0, 1.0) * 20
    score -= 20 if application.get("salvage_flag") else 0
    score -= clamp(metrics["valuation_gap_pct"], 0.0, 0.3) * 40
    return round(clamp(score, 5.0, 99.0), 1)


def calculate_dealer_risk_tier(application: dict[str, Any], exception_severity_score: float) -> str:
    tier = application.get("dealer_tier", "B").upper()
    watchlist = application.get("dealer_watchlist_flag", False)
    if watchlist or tier == "C":
        return "High"
    if tier == "B" and exception_severity_score > 45:
        return "Medium-High"
    if tier == "B":
        return "Medium"
    return "Low"


def calculate_internal_risk_grade(application: dict[str, Any], metrics: dict[str, Any]) -> str:
    score = 820 - application["bureau_score"]
    score += clamp(metrics["ltv"] - 1.0, 0.0, 0.8) * 120
    score += clamp(metrics["dti"] - 0.35, 0.0, 0.35) * 220
    score += clamp(metrics["pti"] - 0.12, 0.0, 0.2) * 250
    score += application.get("delinquencies_12m", 0) * 12
    score += 90 if application.get("bankruptcies_flag") else 0
    score += clamp(application.get("recent_inquiries_6m", 0), 0, 10) * 4
    if score <= 120:
        return "A"
    if score <= 220:
        return "B"
    if score <= 320:
        return "C"
    if score <= 430:
        return "D"
    return "E"


def estimate_pd(internal_risk_grade: str) -> float:
    mapping = {"A": 0.018, "B": 0.032, "C": 0.058, "D": 0.104, "E": 0.168}
    return mapping.get(internal_risk_grade, 0.09)


def estimate_lgd(application: dict[str, Any], metrics: dict[str, Any]) -> float:
    lgd = 0.42
    lgd += clamp(metrics["ltv"] - 1.0, 0.0, 0.5) * 0.45
    lgd += 0.12 if application.get("salvage_flag") else 0.0
    lgd += clamp(metrics["vehicle_age"] - 7, 0.0, 10.0) / 100
    lgd -= clamp(metrics["collateral_quality_score"] - 70, 0.0, 25.0) / 200
    return round(clamp(lgd, 0.2, 0.9), 4)


def estimate_ead(amount_financed: float) -> float:
    return round(amount_financed * 0.86, 2)


def estimate_expected_loss(pd: float, lgd: float, ead: float) -> float:
    return round(pd * lgd * ead, 2)


def calculate_exception_severity_score(
    application: dict[str, Any],
    metrics: dict[str, Any],
) -> float:
    thresholds = application["policy_thresholds"]
    components = []
    components.append(clamp((metrics["ltv"] - thresholds["max_ltv"]) * 120, 0.0, 35.0))
    components.append(clamp((metrics["pti"] - thresholds["max_pti"]) * 220, 0.0, 20.0))
    components.append(clamp((metrics["dti"] - thresholds["max_dti"]) * 180, 0.0, 20.0))
    components.append(clamp((thresholds["min_bureau"] - application["bureau_score"]) * 0.3, 0.0, 18.0))
    components.append(clamp((metrics["vehicle_age"] - thresholds["max_vehicle_age"]) * 2.0, 0.0, 10.0))
    components.append(clamp((application["mileage"] - thresholds["max_mileage"]) / 7_500, 0.0, 10.0))
    components.append(8.0 if application.get("missing_docs") else 0.0)
    return round(clamp(sum(components), 0.0, 100.0), 1)


def evaluate_policy_checks(application: dict[str, Any], metrics: dict[str, Any]) -> list[dict[str, Any]]:
    thresholds = application["policy_thresholds"]
    missing_docs = application.get("missing_docs", [])
    dealer_eligible = application.get("dealer_tier", "B").upper() != "C" and not application.get(
        "dealer_watchlist_flag", False
    )
    rows = [
        {
            "rule": "LTV",
            "actual": metrics["ltv"],
            "threshold": thresholds["max_ltv"],
            "comparator": "<=",
            "pass": metrics["ltv"] <= thresholds["max_ltv"],
            "severity": "high" if metrics["ltv"] > thresholds["max_ltv"] + 0.08 else "medium",
            "format": "pct",
        },
        {
            "rule": "PTI",
            "actual": metrics["pti"],
            "threshold": thresholds["max_pti"],
            "comparator": "<=",
            "pass": metrics["pti"] <= thresholds["max_pti"],
            "severity": "high" if metrics["pti"] > thresholds["max_pti"] + 0.02 else "medium",
            "format": "pct",
        },
        {
            "rule": "DTI",
            "actual": metrics["dti"],
            "threshold": thresholds["max_dti"],
            "comparator": "<=",
            "pass": metrics["dti"] <= thresholds["max_dti"],
            "severity": "high" if metrics["dti"] > thresholds["max_dti"] + 0.04 else "medium",
            "format": "pct",
        },
        {
            "rule": "Minimum Bureau Score",
            "actual": application["bureau_score"],
            "threshold": thresholds["min_bureau"],
            "comparator": ">=",
            "pass": application["bureau_score"] >= thresholds["min_bureau"],
            "severity": "high",
            "format": "int",
        },
        {
            "rule": "Vehicle Age",
            "actual": metrics["vehicle_age"],
            "threshold": thresholds["max_vehicle_age"],
            "comparator": "<=",
            "pass": metrics["vehicle_age"] <= thresholds["max_vehicle_age"],
            "severity": "medium",
            "format": "int",
        },
        {
            "rule": "Mileage",
            "actual": application["mileage"],
            "threshold": thresholds["max_mileage"],
            "comparator": "<=",
            "pass": application["mileage"] <= thresholds["max_mileage"],
            "severity": "medium",
            "format": "int",
        },
        {
            "rule": "Dealer Eligibility",
            "actual": "Eligible" if dealer_eligible else "Ineligible",
            "threshold": "Eligible",
            "comparator": "==",
            "pass": dealer_eligible,
            "severity": "high",
            "format": "label",
        },
        {
            "rule": "Document Completeness",
            "actual": "Complete" if not missing_docs else f"Missing {len(missing_docs)}",
            "threshold": "Complete",
            "comparator": "==",
            "pass": len(missing_docs) == 0,
            "severity": "medium",
            "format": "label",
        },
    ]
    return rows


def compute_application_metrics(
    application: dict[str, Any],
    scenario: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario = scenario or {}
    down_payment = float(scenario.get("cash_down_payment", application["cash_down_payment"]))
    term_months = int(scenario.get("term_months", application["term_months"]))
    amount_financed = scenario.get("amount_financed")
    if amount_financed is None:
        amount_financed = calculate_amount_financed(
            sale_price=application["sale_price"],
            taxes=application["taxes"],
            fees=application["fees"],
            cash_down_payment=down_payment,
            trade_in_value=application["trade_in_value"],
        )
    amount_financed = float(amount_financed)
    monthly_payment = calculate_monthly_payment(amount_financed, application["apr"], term_months)
    ltv = calculate_ltv(amount_financed, application["book_value"])
    pti = calculate_pti(monthly_payment, application["gross_monthly_income"])
    dti = calculate_dti(application["other_monthly_debt"], monthly_payment, application["gross_monthly_income"])
    residual_income = calculate_residual_income(
        application["gross_monthly_income"],
        application["other_monthly_debt"],
        monthly_payment,
    )
    valuation_gap_pct = calculate_valuation_gap_pct(application["sale_price"], application["book_value"])
    vehicle_age = calculate_vehicle_age(application["vehicle_year"])
    payment_shock = monthly_payment - application.get("existing_auto_payment", 0.0)
    collateral_quality_score = calculate_collateral_quality_score(application, {"ltv": ltv, "vehicle_age": vehicle_age, "valuation_gap_pct": valuation_gap_pct})
    provisional = {
        "amount_financed": round(amount_financed, 2),
        "monthly_payment": round(monthly_payment, 2),
        "ltv": round(ltv, 4),
        "pti": round(pti, 4),
        "dti": round(dti, 4),
        "residual_income": round(residual_income, 2),
        "valuation_gap_pct": round(valuation_gap_pct, 4),
        "vehicle_age": vehicle_age,
        "payment_shock": round(payment_shock, 2),
        "collateral_quality_score": collateral_quality_score,
    }
    exception_severity_score = calculate_exception_severity_score(application, provisional)
    dealer_risk_tier = calculate_dealer_risk_tier(application, exception_severity_score)
    internal_risk_grade = calculate_internal_risk_grade(application, provisional)
    pd = estimate_pd(internal_risk_grade)
    lgd = estimate_lgd(application, provisional)
    ead = estimate_ead(amount_financed)
    expected_loss = estimate_expected_loss(pd, lgd, ead)
    policy_checks = evaluate_policy_checks(application, provisional)
    policy_compliant = all(item["pass"] for item in policy_checks)

    return {
        **provisional,
        "exception_severity_score": exception_severity_score,
        "dealer_risk_tier": dealer_risk_tier,
        "internal_risk_grade": internal_risk_grade,
        "pd": round(pd, 4),
        "lgd": round(lgd, 4),
        "ead": round(ead, 2),
        "expected_loss": expected_loss,
        "policy_checks": policy_checks,
        "policy_compliant": policy_compliant,
        "scenario": {
            "cash_down_payment": round(down_payment, 2),
            "term_months": term_months,
            "amount_financed": round(amount_financed, 2),
        },
    }

