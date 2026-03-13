from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import json
from pydantic import BaseModel, Field

from calculations import compute_application_metrics


RecommendationLabel = Literal["approve", "conditional_approval", "decline"]


class AlternateStructure(BaseModel):
    title: str
    down_payment: float
    term_months: int
    amount_financed: float
    new_ltv: float
    new_pti: float
    note: str


class MockRecommendation(BaseModel):
    recommendation: RecommendationLabel
    confidence: float = Field(ge=0.0, le=1.0)
    executive_summary: str
    positive_drivers: list[str]
    risk_drivers: list[str]
    suggested_conditions: list[str]
    alternate_structures: list[AlternateStructure]
    adjudication_memo: str
    chatbot_responses: dict[str, str]


def load_chat_responses(path: str | Path) -> dict[str, dict[str, str]]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _format_recommendation_label(label: RecommendationLabel) -> str:
    labels = {
        "approve": "Approve",
        "conditional_approval": "Conditional Approval",
        "decline": "Decline / Refer",
    }
    return labels[label]


def _derive_primary_recommendation(application: dict[str, Any], metrics: dict[str, Any]) -> tuple[RecommendationLabel, list[str]]:
    checks = metrics["policy_checks"]
    failed_rules = [row["rule"] for row in checks if not row["pass"]]
    thresholds = application["policy_thresholds"]
    severe_breach = (
        metrics["pti"] > thresholds["max_pti"] + 0.04
        or metrics["dti"] > thresholds["max_dti"] + 0.08
        or metrics["ltv"] > thresholds["max_ltv"] + 0.14
    )
    hard_stop = (
        application["bankruptcies_flag"]
        or "Minimum Bureau Score" in failed_rules and application["bureau_score"] < thresholds["min_bureau"] - 25
        or application.get("dealer_watchlist_flag", False)
        or severe_breach
        or application["baseline_recommendation"] == "decline"
    )
    if hard_stop:
        return "decline", failed_rules
    if not failed_rules and metrics["expected_loss"] < 700 and metrics["residual_income"] > 900:
        return "approve", failed_rules
    return "conditional_approval", failed_rules


def _build_positive_drivers(application: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    drivers: list[str] = []
    if application["bureau_score"] >= 700:
        drivers.append(f"Strong bureau score at {application['bureau_score']}.")
    if metrics["residual_income"] >= 900:
        drivers.append(f"Residual income remains positive at ${metrics['residual_income']:,.0f}.")
    if application["prior_td_relationship_years"] >= 4:
        drivers.append(
            f"Prior TD relationship of {application['prior_td_relationship_years']} years provides behavioral history."
        )
    if application["delinquencies_12m"] == 0:
        drivers.append("No recent delinquency activity in the last 12 months.")
    if metrics["collateral_quality_score"] >= 70:
        drivers.append(f"Collateral quality score is supportive ({metrics['collateral_quality_score']:.1f}).")
    if not drivers:
        drivers.append("Core profile includes at least one compensating factor for structured review.")
    return drivers[:4]


def _build_risk_drivers(application: dict[str, Any], metrics: dict[str, Any], failed_rules: list[str]) -> list[str]:
    drivers: list[str] = []
    for rule in failed_rules:
        drivers.append(f"Policy check failed: {rule}.")
    if application.get("dealer_watchlist_flag", False):
        drivers.append("Dealer is currently on watchlist / enhanced monitoring.")
    if application["recent_inquiries_6m"] >= 5:
        drivers.append(f"Elevated credit shopping with {application['recent_inquiries_6m']} inquiries in 6 months.")
    if metrics["expected_loss"] >= 1200:
        drivers.append(f"Expected loss proxy is elevated at ${metrics['expected_loss']:,.0f}.")
    if metrics["exception_severity_score"] >= 45:
        drivers.append(
            f"Exception severity score is high ({metrics['exception_severity_score']:.1f}/100), indicating multi-factor stress."
        )
    if not drivers:
        drivers.append("No material policy or risk red flags outside normal tolerance.")
    return drivers[:5]


def _build_suggested_conditions(application: dict[str, Any], metrics: dict[str, Any], failed_rules: list[str]) -> list[str]:
    conditions = list(application.get("required_conditions", []))
    if "LTV" in failed_rules and "Increase down payment by at least $2,500." not in conditions:
        conditions.append("Increase down payment by at least $2,500.")
    if "PTI" in failed_rules or "DTI" in failed_rules:
        conditions.append("Shorten term by 12 months to reduce monthly stress.")
    if "Document Completeness" in failed_rules:
        conditions.append("Collect all missing documents before booking.")
    if "Dealer Eligibility" in failed_rules:
        conditions.append("Escalate to senior adjudicator due to dealer eligibility risk.")
    if application["queue_reason"] == "Income Verification":
        conditions.append("Validate income with latest paystub and recent bank statements.")
    if metrics["valuation_gap_pct"] > 0.08:
        conditions.append("Obtain independent valuation and cap amount financed to book-value aligned level.")
    # Preserve deterministic, ordered and unique list.
    deduped = []
    seen = set()
    for item in conditions:
        norm = item.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(item)
    return deduped[:5]


def _build_alternate_structures(application: dict[str, Any]) -> list[AlternateStructure]:
    base_down = float(application["cash_down_payment"])
    base_term = int(application["term_months"])
    scenarios = [
        {
            "title": "Structure A - Increased Equity",
            "cash_down_payment": base_down + 2500,
            "term_months": base_term,
            "note": "Improves LTV through additional customer equity.",
        },
        {
            "title": "Structure B - Shorter Amortization",
            "cash_down_payment": base_down + 1500,
            "term_months": max(48, base_term - 12),
            "note": "Reduces duration risk and affordability pressure.",
        },
    ]
    alternates: list[AlternateStructure] = []
    for scenario in scenarios:
        new_metrics = compute_application_metrics(application, scenario)
        alternates.append(
            AlternateStructure(
                title=scenario["title"],
                down_payment=float(scenario["cash_down_payment"]),
                term_months=int(scenario["term_months"]),
                amount_financed=float(new_metrics["amount_financed"]),
                new_ltv=float(new_metrics["ltv"]),
                new_pti=float(new_metrics["pti"]),
                note=scenario["note"],
            )
        )
    return alternates


def _build_confidence(label: RecommendationLabel, metrics: dict[str, Any], failed_rules: list[str]) -> float:
    base = {"approve": 0.9, "conditional_approval": 0.77, "decline": 0.83}[label]
    adjusted = base - (len(failed_rules) * 0.03) - (metrics["exception_severity_score"] / 500)
    return round(_clamp(adjusted, 0.51, 0.95), 2)


def _build_executive_summary(
    application: dict[str, Any],
    metrics: dict[str, Any],
    label: RecommendationLabel,
    failed_rules: list[str],
) -> str:
    status = _format_recommendation_label(label)
    breaches = ", ".join(failed_rules[:4]) if failed_rules else "No threshold breaches detected."
    return (
        f"{status}: Case {application['application_id']} in queue for '{application['queue_reason']}'. "
        f"Key metrics - LTV {metrics['ltv']:.1%}, PTI {metrics['pti']:.1%}, DTI {metrics['dti']:.1%}, "
        f"Expected Loss ${metrics['expected_loss']:,.0f}. "
        f"Policy view: {breaches}"
    )


def _build_adjudication_memo(
    application: dict[str, Any],
    metrics: dict[str, Any],
    label: RecommendationLabel,
    conditions: list[str],
) -> str:
    condition_block = "\n".join([f"- {c}" for c in conditions]) if conditions else "- None"
    return (
        f"Application: {application['application_id']}\n"
        f"Applicant: {application['applicant_name']} | Dealer: {application['dealer_name']}\n"
        f"Recommendation: {_format_recommendation_label(label)}\n"
        f"Summary: LTV {metrics['ltv']:.1%}, PTI {metrics['pti']:.1%}, DTI {metrics['dti']:.1%}, "
        f"Risk Grade {metrics['internal_risk_grade']}, PD {metrics['pd']:.1%}, Expected Loss ${metrics['expected_loss']:,.0f}.\n"
        f"Conditions / Actions:\n{condition_block}\n"
        f"Decision support only. Final authority remains with designated adjudicator."
    )


def generate_mock_recommendation(
    application: dict[str, Any],
    metrics: dict[str, Any],
    chat_responses_by_app: dict[str, dict[str, str]],
) -> MockRecommendation:
    label, failed_rules = _derive_primary_recommendation(application, metrics)
    summary = _build_executive_summary(application, metrics, label, failed_rules)
    positives = _build_positive_drivers(application, metrics)
    risks = _build_risk_drivers(application, metrics, failed_rules)
    conditions = _build_suggested_conditions(application, metrics, failed_rules)
    alternates = _build_alternate_structures(application)
    confidence = _build_confidence(label, metrics, failed_rules)
    memo = _build_adjudication_memo(application, metrics, label, conditions)
    chat = chat_responses_by_app.get(application["application_id"], {})
    fallback_chat = {
        "why_high_risk": "Risk is driven by policy threshold pressure and structure details.",
        "policy_breaches": "Refer to policy matrix for current threshold breaches.",
        "compensating_factors": "Compensating factors include income stability and repayment history where present.",
        "why_conditional": "Conditional path is used when risk can be reduced by explicit conditions.",
        "additional_documents": "Request any missing verification documents and collateral support.",
        "alternate_deals": "Try additional down payment and shorter term scenarios.",
        "summary_of_case": "Manual review case with underwriting judgement required.",
        "recommended_next_step": "Apply conditions and re-run scenario metrics.",
    }
    merged_chat = {**fallback_chat, **chat}
    return MockRecommendation(
        recommendation=label,
        confidence=confidence,
        executive_summary=summary,
        positive_drivers=positives,
        risk_drivers=risks,
        suggested_conditions=conditions,
        alternate_structures=alternates,
        adjudication_memo=memo,
        chatbot_responses=merged_chat,
    )


def infer_chat_intent(user_text: str) -> str:
    text = user_text.lower()
    intent_keywords = {
        "why_high_risk": ["high risk", "risk", "risky", "decline", "concern"],
        "policy_breaches": ["policy", "breach", "threshold", "violate", "rule"],
        "compensating_factors": ["compensating", "offset", "strength", "positive"],
        "why_conditional": ["conditional", "instead of decline", "why not decline"],
        "additional_documents": ["document", "docs", "verification", "proof"],
        "alternate_deals": ["alternate", "option", "structure", "down payment", "term"],
        "summary_of_case": ["summary", "overview", "case"],
        "recommended_next_step": ["next", "action", "what should", "step"],
    }
    best_intent = "summary_of_case"
    best_score = -1
    for intent, keywords in intent_keywords.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_intent = intent
    return best_intent


def get_mock_chat_reply(
    user_text: str,
    recommendation: MockRecommendation,
    preferred_intent: str | None = None,
) -> tuple[str, str]:
    intent = preferred_intent or infer_chat_intent(user_text)
    response = recommendation.chatbot_responses.get(intent, recommendation.chatbot_responses.get("summary_of_case", ""))
    return response, intent

