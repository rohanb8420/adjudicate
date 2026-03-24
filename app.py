from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd
from dotenv import load_dotenv

from calculations import compute_application_metrics
from mock_ai import MockRecommendation, generate_mock_recommendation, get_mock_chat_reply, load_chat_responses


load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "applications.json"
CHAT_PATH = ROOT / "data" / "mock_chat_responses.json"
CSS_PATH = ROOT / "assets" / "styles.css"


def fmt_currency(value: float | int) -> str:
    return f"${value:,.0f}"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def risk_chip(value: float, green_max: float, amber_max: float) -> str:
    if value <= green_max:
        return "chip-pass"
    if value <= amber_max:
        return "chip-warn"
    return "chip-fail"


def metric_status(rule: str, actual: Any, threshold: Any) -> str:
    if isinstance(actual, str):
        return "chip-pass" if actual == threshold else "chip-fail"
    if rule in {"LTV", "PTI", "DTI", "Vehicle Age", "Mileage"}:
        margin = float(actual) - float(threshold)
        if margin <= 0:
            return "chip-pass"
        if margin <= (0.03 if rule in {"LTV", "PTI", "DTI"} else 8000):
            return "chip-warn"
        return "chip-fail"
    if rule == "Minimum Bureau Score":
        gap = float(threshold) - float(actual)
        if gap <= 0:
            return "chip-pass"
        if gap <= 20:
            return "chip-warn"
        return "chip-fail"
    return "chip-warn"


def load_applications() -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    chat_map = load_chat_responses(CHAT_PATH)
    hydrated: list[dict[str, Any]] = []
    for app in apps:
        base_metrics = compute_application_metrics(app)
        rec = generate_mock_recommendation(app, base_metrics, chat_map).model_dump()
        app["_base_metrics"] = base_metrics
        app["_base_recommendation"] = rec
        hydrated.append(app)
    return hydrated


def queue_dataframe(apps: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for app in apps:
        m = app["_base_metrics"]
        rows.append(
            {
                "application_id": app["application_id"],
                "submitted_ts": app["submitted_ts"].replace("T", " ")[:16],
                "applicant_name": app["applicant_name"],
                "dealer_name": app["dealer_name"],
                "requested_amount": fmt_currency(app["requested_loan_amount"]),
                "bureau_score": app["bureau_score"],
                "ltv": fmt_pct(m["ltv"]),
                "pti": fmt_pct(m["pti"]),
                "queue_reason": app["queue_reason"],
                "sla_bucket": app["sla_bucket"],
            }
        )
    return pd.DataFrame(rows)


def filter_queue(
    all_apps: list[dict[str, Any]],
    search: str,
    queue_reason: str,
    dealer_name: str,
    recommendation_status: str,
    sla_bucket: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    filtered = []
    q = (search or "").strip().lower()
    for app in all_apps:
        if q:
            hay = " ".join([app["application_id"], app["applicant_name"], app["dealer_name"]]).lower()
            if q not in hay:
                continue
        if queue_reason != "All" and app["queue_reason"] != queue_reason:
            continue
        if dealer_name != "All" and app["dealer_name"] != dealer_name:
            continue
        if recommendation_status != "All" and app["_base_recommendation"]["recommendation"] != recommendation_status:
            continue
        if sla_bucket != "All" and app["sla_bucket"] != sla_bucket:
            continue
        filtered.append(app)
    return queue_dataframe(filtered), filtered


def render_summary_strip(app: dict[str, Any]) -> str:
    return (
        "<div class='summary-strip'>"
        f"<div><span>Application ID</span><strong>{app['application_id']}</strong></div>"
        f"<div><span>Queue Status</span><strong>{app['underwriter_queue_status']}</strong></div>"
        f"<div><span>Submitted</span><strong>{app['submitted_ts'].replace('T', ' ')[:16]}</strong></div>"
        f"<div><span>SLA</span><strong>{app['sla_bucket']}</strong></div>"
        f"<div><span>Assigned</span><strong>{app['underwriter_assignment']}</strong></div>"
        "</div>"
    )


def render_top_cards(app: dict[str, Any], metrics: dict[str, Any]) -> str:
    return (
        "<div class='top-cards'>"
        f"<div class='card'><h4>Credit Application</h4><p>Requested: <b>{fmt_currency(app['requested_loan_amount'])}</b><br>"
        f"Down Payment: {fmt_currency(metrics['scenario']['cash_down_payment'])}<br>Term: {metrics['scenario']['term_months']} months<br>"
        f"APR: {app['apr']:.2f}%<br>Monthly Payment: {fmt_currency(metrics['monthly_payment'])}</p></div>"
        f"<div class='card'><h4>Applicant</h4><p>Bureau: <b>{app['bureau_score']}</b><br>Income: {fmt_currency(app['gross_monthly_income'])}/mo<br>"
        f"Employment: {app['employment_status']}<br>Job Tenure: {app['job_tenure_months']} mos<br>"
        f"Residence Tenure: {app['residence_tenure_months']} mos</p></div>"
        f"<div class='card'><h4>Dealer</h4><p>{app['dealer_name']} ({app['dealer_id']})<br>Tier: <b>{app['dealer_tier']}</b><br>"
        f"Region: {app['dealer_region']} / {app['province_state']}<br>TD Volume: {app['funded_volume_td']} deals</p></div>"
        f"<div class='card'><h4>Vehicle</h4><p>{app['vehicle_year']} {app['vehicle_make']} {app['vehicle_model']}<br>"
        f"VIN: {app['vin']}<br>Mileage: {app['mileage']:,} km<br>Sale Price: {fmt_currency(app['sale_price'])}<br>"
        f"Book Value: {fmt_currency(app['book_value'])}<br>Title/Salvage: {'Salvage' if app['salvage_flag'] else 'Clean'}</p></div>"
        "</div>"
    )


def render_policy_matrix(metrics: dict[str, Any]) -> tuple[str, bool]:
    rows = metrics["policy_checks"]
    html_rows = []
    all_pass = True
    for row in rows:
        actual = row["actual"]
        threshold = row["threshold"]
        if row["format"] == "pct":
            actual_text = fmt_pct(float(actual))
            threshold_text = fmt_pct(float(threshold))
        elif row["format"] == "int":
            actual_text = f"{int(actual):,}"
            threshold_text = f"{int(threshold):,}"
        else:
            actual_text = str(actual)
            threshold_text = str(threshold)
        chip_class = metric_status(row["rule"], actual, threshold)
        pass_label = "PASS" if row["pass"] else "FAIL"
        if not row["pass"]:
            all_pass = False
        html_rows.append(
            "<tr>"
            f"<td>{row['rule']}</td><td>{actual_text}</td><td>{row['comparator']} {threshold_text}</td>"
            f"<td><span class='chip {chip_class}'>{pass_label}</span></td>"
            f"<td>{row['severity'].upper()}</td>"
            "</tr>"
        )
    table = (
        "<div class='section-card'><h3>Policy Thresholds + Constraint Checks</h3>"
        "<table class='compact-table'><thead><tr><th>Rule</th><th>Actual</th><th>Threshold</th><th>Status</th><th>Severity</th></tr></thead>"
        f"<tbody>{''.join(html_rows)}</tbody></table></div>"
    )
    return table, all_pass


def render_financial(metrics: dict[str, Any]) -> str:
    cards = [
        ("LTV", fmt_pct(metrics["ltv"]), risk_chip(metrics["ltv"], 1.05, 1.14)),
        ("PTI", fmt_pct(metrics["pti"]), risk_chip(metrics["pti"], 0.14, 0.19)),
        ("DTI", fmt_pct(metrics["dti"]), risk_chip(metrics["dti"], 0.35, 0.44)),
        ("Amount Financed", fmt_currency(metrics["amount_financed"]), "chip-neutral"),
        ("Monthly Payment", fmt_currency(metrics["monthly_payment"]), "chip-neutral"),
        ("Residual Income", fmt_currency(metrics["residual_income"]), "chip-pass" if metrics["residual_income"] >= 0 else "chip-fail"),
        ("Internal Risk Tier", metrics["internal_risk_grade"], "chip-warn"),
        ("PD", fmt_pct(metrics["pd"]), risk_chip(metrics["pd"], 0.04, 0.1)),
        ("LGD", fmt_pct(metrics["lgd"]), risk_chip(metrics["lgd"], 0.45, 0.6)),
        ("EAD Proxy", fmt_currency(metrics["ead"]), "chip-neutral"),
        ("Expected Loss", fmt_currency(metrics["expected_loss"]), "chip-fail" if metrics["expected_loss"] > 1200 else "chip-warn"),
        ("Valuation Gap", fmt_pct(metrics["valuation_gap_pct"]), risk_chip(abs(metrics["valuation_gap_pct"]), 0.04, 0.1)),
        ("Exception Severity", f"{metrics['exception_severity_score']:.1f}/100", risk_chip(metrics["exception_severity_score"] / 100, 0.2, 0.45)),
        ("Dealer Risk Tier", metrics["dealer_risk_tier"], "chip-warn"),
        ("Collateral Score", f"{metrics['collateral_quality_score']:.1f}", "chip-pass" if metrics["collateral_quality_score"] > 70 else "chip-warn"),
    ]
    card_html = "".join(
        f"<div class='mini-card'><span>{label}</span><strong>{value}</strong><em class='chip {chip}'>{chip.replace('chip-', '').upper()}</em></div>"
        for label, value, chip in cards
    )
    signal_items = [
        ("Credit Score", "PASS" if metrics["internal_risk_grade"] in {"A", "B"} else "WARN"),
        ("Credit History", "PASS" if metrics["pd"] <= 0.06 else "WARN"),
        ("Collateral Score", "PASS" if metrics["collateral_quality_score"] >= 70 else "WARN"),
        ("Dealer Profile", "PASS" if metrics["dealer_risk_tier"] in {"Low", "Medium"} else "WARN"),
        ("Documentation", "PASS" if any(r["rule"] == "Document Completeness" and r["pass"] for r in metrics["policy_checks"]) else "WARN"),
    ]
    signal_html = "".join(
        [
            f"<div class='signal-pill'><span>{name}</span><em class='chip {'chip-pass' if status == 'PASS' else 'chip-warn'}'>{status}</em></div>"
            for name, status in signal_items
        ]
    )
    return (
        f"<div class='section-card'><h3>Financial / Risk Analysis</h3><div class='mini-grid'>{card_html}</div>"
        f"<div class='signals-row'>{signal_html}</div></div>"
    )


def render_history_tabs(app: dict[str, Any], metrics: dict[str, Any]) -> tuple[str, str, str, str, str]:
    threshold_breaches = [r["rule"] for r in metrics["policy_checks"] if not r["pass"]]
    applicant = (
        f"- Prior TD relationship: {app['prior_td_relationship_years']} years\n"
        f"- Prior auto loans paid off: {app['prior_auto_loan_paid_off_count']}\n"
        f"- Delinquencies (12m): {app['delinquencies_12m']}\n"
        f"- Collections / bankruptcy: {'Yes' if app['collections_flag'] or app['bankruptcies_flag'] else 'No'}\n"
        f"- Recent inquiries (6m): {app['recent_inquiries_6m']}\n\n"
        f"**Employment history**: {app['employment_history']}\n\n"
        f"**Residence stability**: {app['residence_stability']}\n\n"
        f"**Compensating factors**: {', '.join(app['compensating_factors'])}"
    )
    dealer = (
        f"- Dealer tier: {app['dealer_tier']}\n"
        f"- Funded volume with TD: {app['funded_volume_td']}\n"
        f"- Historical approval rate: {fmt_pct(app['historical_approval_rate'])}\n"
        f"- First payment default rate: {fmt_pct(app['first_payment_default_rate'])}\n"
        f"- Exception rate: {fmt_pct(app['dealer_exception_rate'])}\n"
        f"- Avg booked risk tier: {app['average_booked_risk_tier']}\n"
        f"- Watchlist status: {'On watchlist' if app.get('dealer_watchlist_flag') else 'Normal'}"
    )
    vehicle = (
        f"- VIN decode match: {'Yes' if app['vin_decode_match'] else 'No'}\n"
        f"- Valuation sources: {', '.join(app['valuation_sources'])}\n"
        f"- Prior title / accident / salvage: {app['prior_title_issue']}\n"
        f"- Mileage reasonableness: {app['mileage_reasonableness']}\n"
        f"- Age vs term reasonableness: {app['age_term_reasonableness']}\n\n"
        f"**Collateral notes**: {app['collateral_quality_notes']}"
    )
    policy = (
        f"- Missing docs: {', '.join(app['missing_docs']) if app['missing_docs'] else 'None'}\n"
        f"- Verification status: {app['verification_status']}\n"
        f"- Required conditions: {', '.join(app['required_conditions']) if app['required_conditions'] else 'None'}\n"
        f"- Threshold breaches: {', '.join(threshold_breaches) if threshold_breaches else 'None'}\n"
        f"- Prior policy exceptions for similar deals: {app['prior_policy_exceptions_count']}"
    )
    similar_cases = (
        "| Case | Decision | Summary rationale | Similar metrics |\n"
        "|---|---|---|---|\n"
        f"| H-{app['application_id'][-2:]}1 | Approve | Strong stability and compliant thresholds | LTV 99%, PTI 12% |\n"
        f"| H-{app['application_id'][-2:]}2 | Conditional | Moderate ratio pressure resolved with +$2k DP | LTV 109%, PTI 17% |\n"
        f"| H-{app['application_id'][-2:]}3 | Decline | Affordability and bureau fell below policy floor | LTV 116%, PTI 22% |\n"
    )
    return applicant, dealer, vehicle, policy, similar_cases


def render_timeline(app: dict[str, Any]) -> str:
    items = "".join([f"<li><span>{e['event_ts']}</span><p>{e['event']}</p></li>" for e in app["application_timeline"]])
    return f"<ul class='timeline'>{items}</ul>"


def render_recommendation_panel(rec: MockRecommendation) -> tuple[str, str, str, str, str, str, str]:
    badge_class = {"approve": "chip-pass", "conditional_approval": "chip-warn", "decline": "chip-fail"}[rec.recommendation]
    badge_label = {"approve": "Approve", "conditional_approval": "Conditional Approval", "decline": "Decline / Refer"}[
        rec.recommendation
    ]
    badge_html = f"<div class='reco-badge {badge_class}'>{badge_label}</div>"
    confidence = f"**Confidence:** {rec.confidence:.0%}"
    positives = "\n".join([f"- {x}" for x in rec.positive_drivers])
    risks = "\n".join([f"- {x}" for x in rec.risk_drivers])
    conditions = "\n".join([f"- {x}" for x in rec.suggested_conditions]) if rec.suggested_conditions else "- None"
    alternates = "\n".join(
        [
            f"- **{a.title}**: Down {fmt_currency(a.down_payment)}, Term {a.term_months}m, "
            f"Amount {fmt_currency(a.amount_financed)}, LTV {a.new_ltv:.1%}, PTI {a.new_pti:.1%}. {a.note}"
            for a in rec.alternate_structures
        ]
    )
    return badge_html, confidence, rec.executive_summary, positives, risks, conditions, alternates


def render_chat(history: list[dict[str, str]]) -> str:
    if not history:
        return "<div class='chat-empty'>Ask a prompt chip to see mock copilot guidance.</div>"
    bubbles = []
    for row in history:
        klass = "user" if row["role"] == "user" else "assistant"
        bubbles.append(f"<div class='chat-bubble {klass}'><span>{row['text']}</span></div>")
    return "<div class='chat-wrap'>" + "".join(bubbles) + "</div>"


def build_panels(
    app: dict[str, Any],
    base_metrics: dict[str, Any],
    current_metrics: dict[str, Any],
    recommendation: MockRecommendation,
    chat_history: list[dict[str, str]],
) -> tuple[Any, ...]:
    policy_html, compliant = render_policy_matrix(current_metrics)
    scenario_html = ""
    applicant, dealer, vehicle, policy, similar = render_history_tabs(app, current_metrics)
    badge_html, conf, summary, positives, risks, conditions, alternates = render_recommendation_panel(recommendation)
    compliance_badge = (
        "<div class='policy-badge pass'>Policy-Compliant Recommendation</div>"
        if compliant
        else "<div class='policy-badge warn'>Exception-Based Recommendation</div>"
    )
    return (
        render_summary_strip(app),
        render_top_cards(app, current_metrics),
        policy_html,
        render_financial(current_metrics),
        scenario_html,
        applicant,
        dealer,
        vehicle,
        policy,
        similar,
        render_timeline(app),
        badge_html,
        conf,
        summary,
        positives,
        risks,
        conditions,
        alternates,
        compliance_badge,
        render_chat(chat_history),
    )


def initialize_dashboard(
    apps: list[dict[str, Any]],
    chat_map: dict[str, dict[str, str]],
) -> tuple[Any, ...]:
    if not apps:
        return tuple([""] * 20) + ({}, {}, {}, [], {})
    app = apps[0]
    base_metrics = app["_base_metrics"]
    rec = MockRecommendation.model_validate(app["_base_recommendation"])
    chat_history = [{"role": "assistant", "text": rec.chatbot_responses["summary_of_case"]}]
    panels = build_panels(app, base_metrics, base_metrics, rec, chat_history)
    return panels + (
        app,
        base_metrics,
        base_metrics,
        chat_history,
        rec.model_dump(),
    )


def refresh_recommendation(
    selected_app: dict[str, Any] | None,
    chat_map: dict[str, dict[str, str]],
    base_metrics: dict[str, Any] | None,
) -> tuple[Any, ...]:
    if not selected_app:
        return tuple([""] * 20) + ({}, {}, {})
    scenario = (base_metrics or selected_app.get("_base_metrics") or {}).get("scenario", {})
    current_metrics = compute_application_metrics(selected_app, scenario)
    recommendation = generate_mock_recommendation(selected_app, current_metrics, chat_map)
    chat_history = [{"role": "assistant", "text": recommendation.chatbot_responses["summary_of_case"]}]
    panels = build_panels(selected_app, base_metrics or selected_app["_base_metrics"], current_metrics, recommendation, chat_history)
    return panels + (current_metrics, recommendation.model_dump(), chat_history)


def select_row(
    evt: gr.SelectData,
    filtered_apps: list[dict[str, Any]],
    chat_map: dict[str, dict[str, str]],
) -> tuple[Any, ...]:
    if not filtered_apps:
        return tuple([""] * 20) + ({}, {}, {}, [], {})
    row = evt.index[0] if isinstance(evt.index, (tuple, list)) else int(evt.index)
    app = filtered_apps[max(0, min(row, len(filtered_apps) - 1))]
    base_metrics = app["_base_metrics"]
    rec = MockRecommendation.model_validate(app["_base_recommendation"])
    chat_history = [{"role": "assistant", "text": rec.chatbot_responses["summary_of_case"]}]
    panels = build_panels(app, base_metrics, base_metrics, rec, chat_history)
    return panels + (
        app,
        base_metrics,
        base_metrics,
        chat_history,
        rec.model_dump(),
    )


def apply_condition(app: dict[str, Any] | None, down: float, term: int, amount: float) -> tuple[float, int, float]:
    if not app:
        return down, term, amount
    current_down = float(down if down is not None else app["cash_down_payment"])
    current_term = int(term if term is not None else app["term_months"])
    current_amount = float(amount if amount is not None else app["_base_metrics"]["amount_financed"])
    down2 = current_down + 2500
    term2 = max(48, current_term - 12)
    amount2 = max(0.0, current_amount - 2500)
    return down2, term2, amount2


def chat_from_text(user_text: str, recommendation_data: dict[str, Any], history: list[dict[str, str]]) -> tuple[str, list[dict[str, str]], str]:
    text = (user_text or "").strip()
    if not text:
        return render_chat(history), history, ""
    rec = MockRecommendation.model_validate(recommendation_data)
    reply, _ = get_mock_chat_reply(text, rec)
    new_history = history + [{"role": "user", "text": text}, {"role": "assistant", "text": reply}]
    return render_chat(new_history), new_history, ""


def chat_from_chip(intent: str, recommendation_data: dict[str, Any], history: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    labels = {
        "why_high_risk": "Why is this high risk?",
        "policy_breaches": "What are the key policy breaches?",
        "compensating_factors": "What compensating factors exist?",
        "why_conditional": "Why conditional approval instead of decline?",
        "additional_documents": "What additional documents would you ask for?",
        "alternate_deals": "Show alternate deal options",
    }
    rec = MockRecommendation.model_validate(recommendation_data)
    user_text = labels[intent]
    reply, _ = get_mock_chat_reply(user_text, rec, preferred_intent=intent)
    new_history = history + [{"role": "user", "text": user_text}, {"role": "assistant", "text": reply}]
    return render_chat(new_history), new_history


def clear_chat(recommendation_data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    rec = MockRecommendation.model_validate(recommendation_data)
    history = [{"role": "assistant", "text": rec.chatbot_responses.get("summary_of_case", "Ready for follow-up questions.")}]
    return render_chat(history), history


applications = load_applications()
chat_lookup = load_chat_responses(CHAT_PATH)

queue_reasons = ["All"] + sorted({a["queue_reason"] for a in applications})
dealers = ["All"] + sorted({a["dealer_name"] for a in applications})
sla_options = ["All"] + sorted({a["sla_bucket"] for a in applications})


with gr.Blocks(title="TD Auto Finance - Assisted Lending Workbench", css=CSS_PATH.read_text(encoding="utf-8")) as demo:
    all_apps_state = gr.State(applications)
    chat_lookup_state = gr.State(chat_lookup)
    filtered_state = gr.State(applications)
    selected_app_state = gr.State(applications[0] if applications else None)
    base_metrics_state = gr.State(applications[0]["_base_metrics"] if applications else {})
    current_metrics_state = gr.State(applications[0]["_base_metrics"] if applications else {})
    recommendation_state = gr.State(applications[0]["_base_recommendation"] if applications else {})
    chat_history_state = gr.State([])

    gr.HTML("<div class='badge-banner'>Internal Demo - Synthetic Data Only</div>")
    gr.HTML("<div class='app-title'>TD Auto Finance - Assisted Lending Workbench</div>")

    with gr.Row():
        with gr.Column(scale=3, elem_classes="left-pane"):
            gr.Markdown("### Applications Requiring Review")
            search = gr.Textbox(label="Search", placeholder="Search ID, applicant, dealer")
            queue_reason = gr.Dropdown(queue_reasons, value="All", label="Queue Reason")
            dealer_filter = gr.Dropdown(dealers, value="All", label="Dealer")
            reco_filter = gr.Dropdown(["All", "approve", "conditional_approval", "decline"], value="All", label="Recommendation Status")
            sla_filter = gr.Dropdown(sla_options, value="All", label="SLA Urgency")
            queue_table = gr.Dataframe(
                value=queue_dataframe(applications),
                interactive=False,
                wrap=True,
                label="Review Queue",
                row_count=12,
                elem_id="review_queue_table",
            )

        with gr.Column(scale=6, elem_classes="center-pane"):
            summary_strip = gr.HTML()
            top_cards = gr.HTML()
            policy_matrix = gr.HTML()
            financial_view = gr.HTML()
            scenario_compare = gr.HTML()
            with gr.Tabs():
                with gr.Tab("Applicant History"):
                    applicant_tab = gr.Markdown()
                with gr.Tab("Dealer History"):
                    dealer_tab = gr.Markdown()
                with gr.Tab("Vehicle & Collateral"):
                    vehicle_tab = gr.Markdown()
                with gr.Tab("Policy / Documentation"):
                    policy_tab = gr.Markdown()
                with gr.Tab("Similar Cases"):
                    similar_tab = gr.Markdown()
            with gr.Accordion("Mini Application Timeline", open=False):
                timeline_html = gr.HTML()

        with gr.Column(scale=4, elem_classes="right-pane"):
            gr.Markdown("## AI Recommendation")
            policy_badge = gr.HTML()
            gr.HTML("<div class='mock-mode'>AI Mode: Deterministic Mock Engine</div>")
            recommendation_badge = gr.HTML()
            confidence_md = gr.Markdown()
            executive_summary = gr.Markdown(label="Executive Summary")
            positive_md = gr.Markdown(label="Top Positive Drivers")
            risk_md = gr.Markdown(label="Top Risk Drivers")
            conditions_md = gr.Markdown(label="Suggested Conditions")
            alternate_md = gr.Markdown(label="Alternate Structures (Secondary)")
            refresh_btn = gr.Button("Refresh AI Recommendation", variant="primary")

            with gr.Group(elem_classes="copilot-panel"):
                gr.Markdown("### Ask Adjudication Copilot")
                gr.Markdown("_Mock copilot responses for demo purposes_")
                chat_html = gr.HTML()
                with gr.Row():
                    chip1 = gr.Button("Why is this high risk?")
                    chip2 = gr.Button("What are the key policy breaches?")
                with gr.Row():
                    chip3 = gr.Button("What compensating factors exist?")
                    chip4 = gr.Button("Why conditional approval instead of decline?")
                with gr.Row():
                    chip5 = gr.Button("What additional documents would you ask for?")
                    chip6 = gr.Button("Show alternate deal options")
                chat_input = gr.Textbox(label="Type a question", placeholder="Ask anything about this case")
                with gr.Row():
                    chat_send = gr.Button("Send", variant="primary")
                    chat_clear = gr.Button("Clear Chat")

    gr.HTML("<div class='footer'>Internal Demo - Synthetic Data | AI recommendation is mocked for demo purposes</div>")

    filter_inputs = [all_apps_state, search, queue_reason, dealer_filter, reco_filter, sla_filter]
    filter_outputs = [queue_table, filtered_state]
    search.change(filter_queue, filter_inputs, filter_outputs)
    queue_reason.change(filter_queue, filter_inputs, filter_outputs)
    dealer_filter.change(filter_queue, filter_inputs, filter_outputs)
    reco_filter.change(filter_queue, filter_inputs, filter_outputs)
    sla_filter.change(filter_queue, filter_inputs, filter_outputs)

    selection_outputs = [
        summary_strip, top_cards, policy_matrix, financial_view, scenario_compare, applicant_tab, dealer_tab, vehicle_tab,
        policy_tab, similar_tab, timeline_html, recommendation_badge, confidence_md, executive_summary, positive_md,
        risk_md, conditions_md, alternate_md, policy_badge, chat_html, selected_app_state, base_metrics_state,
        current_metrics_state, chat_history_state, recommendation_state,
    ]
    queue_table.select(select_row, [filtered_state, chat_lookup_state], selection_outputs)

    scenario_outputs = [
        summary_strip, top_cards, policy_matrix, financial_view, scenario_compare, applicant_tab, dealer_tab, vehicle_tab,
        policy_tab, similar_tab, timeline_html, recommendation_badge, confidence_md, executive_summary, positive_md,
        risk_md, conditions_md, alternate_md, policy_badge, chat_html, current_metrics_state, recommendation_state, chat_history_state
    ]

    refresh_btn.click(
        refresh_recommendation,
        [selected_app_state, chat_lookup_state, base_metrics_state],
        scenario_outputs,
    )
    chat_send.click(chat_from_text, [chat_input, recommendation_state, chat_history_state], [chat_html, chat_history_state, chat_input])
    chip1.click(lambda r, h: chat_from_chip("why_high_risk", r, h), [recommendation_state, chat_history_state], [chat_html, chat_history_state])
    chip2.click(lambda r, h: chat_from_chip("policy_breaches", r, h), [recommendation_state, chat_history_state], [chat_html, chat_history_state])
    chip3.click(lambda r, h: chat_from_chip("compensating_factors", r, h), [recommendation_state, chat_history_state], [chat_html, chat_history_state])
    chip4.click(lambda r, h: chat_from_chip("why_conditional", r, h), [recommendation_state, chat_history_state], [chat_html, chat_history_state])
    chip5.click(lambda r, h: chat_from_chip("additional_documents", r, h), [recommendation_state, chat_history_state], [chat_html, chat_history_state])
    chip6.click(lambda r, h: chat_from_chip("alternate_deals", r, h), [recommendation_state, chat_history_state], [chat_html, chat_history_state])
    chat_clear.click(clear_chat, [recommendation_state], [chat_html, chat_history_state])

    demo.load(initialize_dashboard, [filtered_state, chat_lookup_state], selection_outputs)


if __name__ == "__main__":
    demo.launch()
