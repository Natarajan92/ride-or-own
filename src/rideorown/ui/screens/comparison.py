"""A five-year decision with one reconciled cash-flow timeline."""

import json
from decimal import Decimal
from hashlib import sha256
from html import escape
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from rideorown.domain.assumptions import ESCALATION_DEFAULTS, RESEARCHED_ON
from rideorown.domain.decision import assess_decision
from rideorown.domain.models import BudgetConstraints
from rideorown.domain.ownership import purchase_funding, scenario_errors
from rideorown.services.forecast import (
    budget_assessment,
    calculate_workspace_comparison,
    cash_flow_export,
    comparison_scope,
    effective_assumptions,
    inactive_assumptions,
    monthly_cash_flows,
    receipt_baseline,
    selected_groups,
    yearly_cash_flows,
)
from rideorown.ui.components import (
    INR_AXIS_LABEL,
    amount_input,
    inr_frame,
    metric_card,
    money,
    notify,
    page_intro,
    queue_notice,
    rupee_value,
    save,
)

INFLATION_URL = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2310058&lang=1&reg=19"
ZERO = Decimal(0)


def _range(low, high):
    return money(low) if low == high else f"{money(low)} – {money(high)}"


def _saving_amount(value):
    return "less than ₹1" if ZERO < value < 1 else money(value)


def _decision_copy(result, has_bus):
    verdict = assess_decision(result)
    continuing = "keeping your current travel" if has_bus else "continuing cabs"
    headlines = {
        "buy": "Buying the car costs less",
        "continue": "Keep your current travel — it costs less"
        if has_bus
        else "Keep taking cabs — they cost less",
        "tie": "Both choices cost the same",
        "conditional": "Buying has no clear cost advantage",
        "provisional": "Provisional: complete the inputs before deciding",
    }
    low, high = verdict.savings_low, verdict.savings_high
    if verdict.base_status == "conditional":
        evidence = (
            f"At the lower fare estimate, {continuing} saves {_saving_amount(-low)} over five years. "
            f"At the upper fare estimate, buying the car saves {_saving_amount(high)}."
        )
    elif verdict.base_status == "tie":
        evidence = "Neither choice saves money over five years under the entered assumptions."
    else:
        lower, upper = (low, high) if verdict.base_status == "buy" else (-high, -low)
        choice = "Buying the car" if verdict.base_status == "buy" else continuing.capitalize()
        if lower == 0:
            evidence = (
                f"{choice} is no more expensive across your fare range: one end is a tie, "
                f"and the saving reaches {_saving_amount(upper)} over five years at the other."
            )
        else:
            saving = (
                _saving_amount(lower)
                if lower == upper
                else f"{_saving_amount(lower)} to {_saving_amount(upper)}"
            )
            evidence = f"{choice} saves {saving} over five years under your entered assumptions."
    if verdict.status == "provisional":
        action = "These are provisional estimates. Resolve the listed warnings before using them to decide."
    elif verdict.status == "conditional":
        action = "The fare range supports either outcome. Narrow your forecast in step 3 before buying for savings."
    elif verdict.status == "tie":
        action = "Base your choice on affordability, convenience and driving effort."
    else:
        action = "This compares cost only; also consider the purchase cash and practical trade-offs below."
    return headlines[verdict.status], evidence, action


def _outcomes(result, has_bus):
    continuing = "Keep current travel" if has_bus else "Continue cabs"
    continue_column = "Continue current travel" if has_bus else "Continue cabs"
    bounds = [
        ("At the lower fare estimate", result.ride_total_low, result.ownership_net_low),
        ("At the upper fare estimate", result.ride_total_high, result.ownership_net_high),
    ]
    if (
        result.ride_total_low == result.ride_total_high
        and result.ownership_net_low == result.ownership_net_high
    ):
        bounds = [("Your forecast", result.ride_total_low, result.ownership_net_low)]
    return pd.DataFrame(
        [
            {
                "Travel spending": label,
                continue_column: money(travel),
                "Buy a car": money(car),
                "Lower-cost choice": "Buy a car"
                if travel > car
                else continuing
                if travel < car
                else "Same cost",
                "Five-year saving": _saving_amount(abs(travel - car)),
            }
            for label, travel, car in bounds
        ]
    )


def _threshold_description(result, has_bus):
    travel = "travel" if has_bus else "cab"
    return (
        f"Cost threshold: {money(result.spending_threshold)} in first-year monthly replaceable {travel} spending. "
        "Above this, buying costs less; below it, continuing costs less, at the entered driving kilometres. "
        "This excludes retained fares and includes your annual escalation assumptions."
    )


def render(repo, workspace):
    page_intro(
        "05 / Comparison & buying decision",
        "Should you buy a car or keep taking cabs?",
        "Compare five-year costs and the cash needed to buy.",
    )
    errors = scenario_errors(workspace.scenario)
    if errors:
        st.info(
            "Complete step 4 · New car plan to compare your car with the cab forecast from step 3."
        )
        for error in errors:
            st.write(f"• {error}")
        return
    receipt_source = workspace.cab_forecast.source == "receipts"
    if not receipt_source and not workspace.groups:
        st.info(
            "Add your future travel groups in step 2 · Advanced travel settings, then review them in step 3 · Cab forecast."
        )
        return
    has_bus = not receipt_source and any(
        g.transport_mode == "bus" for g in selected_groups(workspace)
    )
    fare_label = "Travel" if has_bus else "Cabs"
    retained_label = "Retained travel" if has_bus else "Retained cabs"
    st.caption(comparison_scope(workspace))
    st.caption(_inflation_description(workspace))
    with st.container(border=True):
        st.subheader("Explore changes to your assumptions")
        a, b, c = st.columns(3)
        with a:
            demand = st.slider(
                "Replaceable travel demand (%)",
                25,
                200,
                None if "sensitivity_demand" in st.session_state else 100,
                5,
                key="sensitivity_demand",
                help="Changes replaced journeys, their fares and driving kilometres together. Retained fares and additional driving stay fixed.",
            )
        with b:
            efficiency = st.slider(
                "Fuel efficiency vs your estimate (%)",
                50,
                150,
                None if "sensitivity_efficiency" in st.session_state else 100,
                5,
                key="sensitivity_efficiency",
            )
        with c:
            resale = st.slider(
                "Resale vs your estimate (%)",
                0,
                150,
                None if "sensitivity_resale" in st.session_state else 100,
                5,
                key="sensitivity_resale",
            )
        st.caption(
            "100% uses your saved inputs. These sliders explore alternatives without changing them."
        )
        st.button("Reset sensitivity to baseline", on_click=_reset_sensitivity)
    try:
        result = calculate_workspace_comparison(
            workspace,
            demand_factor=Decimal(demand) / 100,
            efficiency_factor=Decimal(efficiency) / 100,
            resale_factor=Decimal(resale) / 100,
        )
    except ValueError as exc:
        st.error(str(exc))
        st.info(
            "Check step 3 · Cab forecast and step 4 · New car plan, then return to your decision."
        )
        return
    verdict = assess_decision(result)
    active = (demand, efficiency, resale) != (100, 100, 100)
    sensitivity_label = f"{'Active sensitivity' if active else 'Saved baseline'}: demand {demand}% ({demand - 100:+d}%), efficiency {efficiency}% ({efficiency - 100:+d}%), resale {resale}% ({resale - 100:+d}%)."
    st.info(sensitivity_label)
    if active:
        baseline_result = calculate_workspace_comparison(workspace)
        st.write(
            f"Change from baseline: cab five-year cost {_range(result.ride_total_low - baseline_result.ride_total_low, result.ride_total_high - baseline_result.ride_total_high)}; car five-year net cost {_range(result.ownership_net_low - baseline_result.ownership_net_low, result.ownership_net_high - baseline_result.ownership_net_high)}. Baseline car net cost {_range(baseline_result.ownership_net_low, baseline_result.ownership_net_high)}."
        )
    headline, evidence, action = _decision_copy(result, has_bus)
    with st.container(border=True, key="decision_summary"):
        st.markdown('<div class="eyebrow">Your five-year outlook</div>', unsafe_allow_html=True)
        if verdict.status in {"conditional", "provisional"}:
            st.warning(headline)
        elif verdict.status == "tie":
            st.info(headline)
        else:
            st.success(headline)
        st.write(evidence)
        st.write(action)
        st.caption(
            f"{workspace.scenario.name} · {workspace.forecast_start:%B %Y} to {result.months[-1].month} · future rupees, including inflation"
        )
        a, b = st.columns(2)
        with a:
            metric_card(
                "Continue selected travel · five-year net cost"
                if has_bus
                else "Continue cabs · five-year net cost",
                _range(result.ride_total_low, result.ride_total_high),
                "Selected travel payments over 60 months; no resale inflow",
                tone="teal",
            )
        with b:
            metric_card(
                "Buy a car · five-year net cost",
                _range(result.ownership_net_low, result.ownership_net_high),
                "All cash paid, including final loan settlement, less resale",
                tone="slate",
            )
    st.subheader("Which choice saves money?")
    st.dataframe(_outcomes(result, has_bus), hide_index=True, width="stretch")
    st.caption("Lower and upper fares are alternative scenarios, not probabilities.")
    if verdict.base_status == "conditional":
        st.write(_threshold_description(result, has_bus))
    _budget_result(repo, workspace, result)
    st.subheader("What buying requires")
    a, b, c = st.columns(3)
    with a:
        metric_card(
            "Buy a car · total cash paid",
            _range(
                result.ownership_cash_low + result.outstanding_balance,
                result.ownership_cash_high + result.outstanding_balance,
            ),
            "Purchase cash + 60 months of costs + final loan settlement",
        )
    with b:
        metric_card(
            "Car resale · cash received",
            money(result.resale_value),
            "Assumed sale at the end of month 60",
        )
    with c:
        metric_card(
            "Cash needed today",
            money(result.upfront_cash),
            "Your purchase contribution after any loan funding",
        )
    funding = purchase_funding(workspace.scenario)
    st.caption(
        f"Purchase: {money(funding.acquisition, places=2)} − {money(funding.loan, places=2)} loan "
        f"= {money(funding.down_payment, places=2)} down payment; "
        f"+ {money(funding.extras, places=2)} separate extras "
        f"= {money(funding.cash_needed, places=2)} cash needed today."
    )
    st.caption(
        f"Loan instalment: {money(result.monthly_emi)} per month while the loan runs. "
        f"Loan settlement at the end of year 5: {money(result.outstanding_balance)}. "
        "The comparison assumes you sell the car then and settle any remaining loan."
    )
    basis, confidence = _forecast_description(workspace)
    with st.expander("Forecast assumptions"):
        st.caption(basis)
        st.caption(_inflation_description(workspace))
        st.caption(
            confidence
            + (
                " Review your receipt assumptions in step 3 · Cab forecast."
                if receipt_source
                else " Edit journeys in step 2 · Advanced travel settings and review the projection in step 3 · Cab forecast."
            )
        )
    if result.warnings:
        with st.expander("Review what makes this result provisional", expanded=True):
            for warning in result.warnings:
                st.caption(f"⚠ {warning}")
    monthly_rows = monthly_cash_flows(result)
    yearly_rows = yearly_cash_flows(result)
    st.subheader("Your cash flow, year by year")
    st.caption("Net cost = cash paid − cash received.")
    st.dataframe(_cash_table(yearly_rows, fare_label, total=True), hide_index=True, width="stretch")
    st.caption(
        f"Today includes your purchase contribution. Years 1–5 include EMI, fuel, maintenance, parking, tolls, insurance, any driver and {retained_label.lower()}. "
        "Year 5 also includes final loan settlement and resale. The total matches the cards above."
    )
    _timeline(
        monthly_rows, fare_label, retained_label, workspace, result, demand, efficiency, resale
    )
    with st.expander("See the car cost breakdown"):
        labels = {
            "upfront": "Cash at purchase",
            "loan_payments": "Loan repayments",
            "fuel": "Fuel",
            "operating": "Maintenance, parking & tolls",
            "insurance_renewals": "Insurance renewals",
            "driver": "Hired driver",
            "outstanding_balance": "Final loan settlement",
            "resale": "Resale received",
        }
        breakdown = [
            {"Component": labels[key], "Amount": money(value)}
            for key, value in result.breakdown.items()
            if key in labels
        ]
        breakdown.append(
            {
                "Component": retained_label,
                "Amount": _range(
                    result.breakdown["retained_low"], result.breakdown["retained_high"]
                ),
            }
        )
        st.dataframe(pd.DataFrame(breakdown), hide_index=True, width="stretch")
        st.caption(
            "Resale is cash received and is subtracted from cash paid. Lower and upper fares describe alternatives; they are never added together."
        )
        st.caption(
            "Purchase cash and repayments count the acquisition once. Resale represents remaining value; depreciation is not charged again. Investment returns and the time value of money are excluded."
        )
    with st.expander("Monthly spending threshold"):
        metric_card(
            "Monthly replaceable travel spending for equal five-year cost"
            if has_bus
            else "Monthly replaceable cab spending for equal five-year cost",
            money(result.spending_threshold),
            "With your driving kilometres and inflation assumptions",
        )
        st.caption(
            "Retained fares cancel from this comparison. This threshold holds driving kilometres fixed; changing actual journeys changes both fares and car running costs. It does not identify a break-even month."
        )
    _personal_notes(repo, workspace)
    with st.expander("Saved assumptions, sources and printable report"):
        st.markdown("#### Effective inputs used for this result")
        st.json(effective_assumptions(workspace, demand, efficiency, resale))
        st.caption(
            "Sensitivity percentages apply to the saved baseline ownership and journey inputs shown above. Insurance budgets and loan EMI remain fixed."
        )
        with st.expander("Inactive stored settings — not applied"):
            st.json(inactive_assumptions(workspace))
        _sources(workspace)
        st.download_button(
            "Download printable comparison",
            _report(workspace, result, demand, efficiency, resale),
            file_name="rideorown-five-year-comparison.html",
            mime="text/html",
            on_click=queue_notice,
            args=("Download requested: printable comparison.",),
            kwargs={"level": "info"},
        )
        st.caption(
            "Open the report in your browser to print or save as PDF. It includes the full cash-flow timeline and assumptions used here."
        )


def _forecast_description(workspace):
    settings = workspace.cab_forecast
    if settings.source == "receipts":
        baseline = receipt_baseline(workspace)
        period = (
            f"{baseline.start:%B %Y} to {baseline.end:%B %Y}"
            if baseline.start and baseline.end
            else "your selected six-month period"
        )
        basis = (
            f"Receipt-based forecast: {money(baseline.total)} across {len(baseline.months)} months "
            f"({period}), averaging {money(baseline.monthly_average)} per month. "
            f"The forecast starts from this average; {settings.retained_share * 100:g}% of cab spending remains when you buy a car."
        )
        confidence = (
            "Receipt coverage needs review; this is a provisional projection."
            if baseline.errors or baseline.warnings
            else "You confirmed this receipt period is complete. Six months cannot establish a full annual or five-year travel pattern."
        )
    else:
        basis = (
            comparison_scope(workspace)
            + " Uses selected transport, fare ranges and replacement choices from step 2."
        )
        confidence = "Fare ranges are scenario bounds, not a statistical confidence interval."
    return basis, confidence


def _inflation_description(workspace):
    if workspace.cab_forecast.source == "receipts":
        rates = f"India inflation assumption: {workspace.cab_forecast.annual_inflation:g}% per year for cab fares, fuel and operating budgets."
    else:
        scenario = workspace.scenario
        rates = (
            f"Advanced annual escalation assumptions: fares {scenario.fare_escalation:g}%, "
            f"fuel {scenario.fuel_escalation:g}%, operating budgets {scenario.operating_escalation:g}%."
        )
    return (
        rates + " Year 1 uses today's amounts; each later year compounds the annual rate. "
        "Loan EMI, entered insurance budgets and resale stay as entered."
    )


def _cash_table(rows, fare_label, *, total=False):
    values = list(rows)
    if total:
        keys = [key for key in rows[0] if key not in {"period", "month"}]
        values.append(
            {
                "month": "Five-year total",
                **{key: sum((row[key] for row in rows), ZERO) for key in keys},
            }
        )
    return pd.DataFrame(
        [
            {
                "Period": row["month"],
                f"{fare_label} cash out": _range(row["cab_outflow_low"], row["cab_outflow_high"]),
                f"{fare_label} cash in": money(row["cab_inflow"]),
                f"{fare_label} net cost": _range(row["cab_net_low"], row["cab_net_high"]),
                "Car cash out": _range(row["car_outflow_low"], row["car_outflow_high"]),
                "Car cash in": money(row["car_inflow"]),
                "Car net cost": _range(row["car_net_low"], row["car_net_high"]),
            }
            for row in values
        ]
    )


def _timeline(
    rows,
    fare_label,
    retained_label,
    workspace=None,
    result=None,
    demand=100,
    efficiency=100,
    resale=100,
):
    chart = pd.DataFrame(
        [
            {
                "Month": row["period"],
                f"{fare_label} lower": float(row["cab_net_low"]),
                f"{fare_label} upper": float(row["cab_net_high"]),
                "Car lower": float(row["car_net_low"]),
                "Car upper": float(row["car_net_high"]),
            }
            for row in rows
        ]
    ).set_index("Month")
    if all(
        row["cab_net_low"] == row["cab_net_high"] and row["car_net_low"] == row["car_net_high"]
        for row in rows
    ):
        chart = chart[[f"{fare_label} lower", "Car lower"]].rename(
            columns={f"{fare_label} lower": fare_label, "Car lower": "Car"}
        )
        colors = ["#0f766e", "#536b92"]
    else:
        colors = ["#0f766e", "#7cbbb0", "#536b92", "#9babc4"]
    st.subheader("How the cost builds over five years")
    _cash_chart(
        chart.cumsum(),
        color=colors,
        x_label="Months from purchase (0 = today)",
        y_label="Cumulative net cost (₹)",
        height=290,
    )
    st.caption(
        "The timeline starts with today's purchase cash and ends after resale and loan settlement. Its final value equals the five-year net cost."
    )
    with st.expander("Monthly cash flow and CSV download"):
        _cash_chart(
            chart,
            color=colors,
            x_label="Months from purchase (0 = today)",
            y_label="Monthly cash out − cash in (₹)",
            height=290,
        )
        st.caption(
            "A negative month means cash received exceeds cash paid that month. The final month includes selling the car."
        )
        detail = pd.DataFrame(
            [
                {
                    "Month": row["month"],
                    f"{fare_label} lower": float(row["cab_outflow_low"]),
                    f"{fare_label} upper": float(row["cab_outflow_high"]),
                    "Car cash out lower": float(row["car_outflow_low"]),
                    "Car cash out upper": float(row["car_outflow_high"]),
                    "Car cash in": float(row["car_inflow"]),
                    "Car net lower": float(row["car_net_low"]),
                    "Car net upper": float(row["car_net_high"]),
                    "Purchase contribution": float(row["upfront"]),
                    "EMI": float(row["loan_payment"]),
                    "Fuel": float(row["fuel"]),
                    "Operating": float(row["operating"]),
                    "Insurance renewal": float(row["insurance"]),
                    "Driver": float(row["driver"]),
                    f"{retained_label} lower": float(row["retained_cab_low"]),
                    f"{retained_label} upper": float(row["retained_cab_high"]),
                    "Loan settlement": float(row["loan_settlement"]),
                    "Resale received": float(row["resale"]),
                }
                for row in rows
            ]
        )
        st.dataframe(
            inr_frame(detail, detail.columns.drop("Month")), hide_index=True, width="stretch"
        )
        st.caption(
            "All amounts are INR. Tables show paise; cards show rounded rupees. "
            "Totals use unrounded calculations, so adding displayed rounded rows can differ slightly. "
            "CSV amounts are ungrouped numbers for spreadsheet calculations and retain calculation precision."
        )
        st.download_button(
            "Download monthly cash-flow CSV",
            pd.DataFrame(
                cash_flow_export(workspace, result, demand, efficiency, resale)
                if workspace
                else rows
            )
            .to_csv(index=False)
            .encode("utf-8"),
            "rideorown-monthly-cash-flow.csv",
            "text/csv",
            on_click=queue_notice,
            args=("Download requested: monthly cash-flow CSV.",),
            kwargs={"level": "info"},
        )


def _cash_chart(frame, *, color, x_label, y_label, height):
    values = frame.reset_index().melt(id_vars="Month", var_name="Choice", value_name="Amount")
    values["Amount (INR)"] = values["Amount"].map(lambda value: money(value, places=2))
    dataset_name = "cash_" + sha256(values.to_json().encode()).hexdigest()
    st.vega_lite_chart(
        {
            "data": {"name": dataset_name},
            "datasets": {dataset_name: values},
            "mark": "line",
            "height": height,
            "encoding": {
                "x": {"field": "Month", "type": "quantitative", "title": x_label},
                "y": {
                    "field": "Amount",
                    "type": "quantitative",
                    "title": y_label,
                    "axis": {"labelExpr": INR_AXIS_LABEL},
                },
                "color": {
                    "field": "Choice",
                    "type": "nominal",
                    "scale": {"domain": list(frame.columns), "range": color},
                },
                "tooltip": [
                    {"field": "Month"},
                    {"field": "Choice"},
                    {"field": "Amount (INR)", "type": "nominal"},
                ],
            },
        },
        width="stretch",
        key="cash_" + sha256((values.to_json() + y_label).encode()).hexdigest(),
    )


def _personal_notes(repo, workspace):
    with st.expander("The practical side of the decision"):
        st.caption("Consider comfort, waiting and driving effort alongside the financial result.")
        current = workspace.personal_considerations
        with st.form("personal_considerations"):
            values = {}
            for key, label in [
                ("comfort", "Comfort and convenience"),
                ("waiting", "Cab availability and waiting"),
                ("driving", "Driving effort and hired-driver needs"),
                ("parking", "Parking access and practical constraints"),
            ]:
                values[key] = st.text_area(label, value=current.get(key, ""), height=75)
            if st.form_submit_button("Save personal considerations"):
                workspace.personal_considerations = values
                save(repo, workspace)
                notify("Personal considerations saved.")


def _reset_sensitivity():
    for name in ("demand", "efficiency", "resale"):
        st.session_state["sensitivity_" + name] = 100


def _budget_result(repo, workspace, result):
    st.subheader("Can this plan meet your cash limits?")
    with st.form("budget_limits"):
        upfront = amount_input("Upfront cash limit (₹, optional)", workspace.budget.upfront_limit)
        monthly = amount_input(
            "Strict monthly cash-out limit (₹, optional)", workspace.budget.monthly_limit
        )
        if st.form_submit_button("Save cash limits"):
            try:
                workspace.budget = BudgetConstraints(
                    upfront_limit=rupee_value(upfront, "upfront limit", optional=True),
                    monthly_limit=rupee_value(monthly, "monthly limit", optional=True),
                )
                save(repo, workspace)
                queue_notice("Cash limits saved.")
                st.rerun()
            except ValueError as exc:
                notify(exc, level="error")
    assessment = budget_assessment(workspace, result)
    st.write(_budget_upfront(assessment))
    st.write(
        f"Highest projected monthly cash out: {_cash_money(assessment['peak'])} in {', '.join(assessment['peak_months'])}."
    )
    limit, delta = assessment["monthly_limit"], assessment["peak_delta"]
    if limit is None:
        st.info("Monthly constraint: no limit set. Enter your own cap to check every month.")
    elif assessment["breaches"]:
        st.warning(
            f"Monthly constraint: exceeds {_cash_money(limit)} in {len(assessment['breaches'])} month(s). Peak is {_cash_money(delta)} above your limit. This plan does not meet the strict monthly cap."
        )
        for row in assessment["breaches"]:
            st.write(
                f"{row['month']}: {_cash_money(row['outflow'])} cash out — {_cash_money(row['over_limit'])} above limit."
            )
    else:
        st.success(
            f"Monthly constraint: all 60 projected months meet {_cash_money(limit)}; peak is {_cash_money(-delta)} below the limit."
        )
    st.caption(
        f"Average scheduled monthly cash out: {_cash_money(assessment['average'])}. An average does not test a strict cap. Checks use the upper fare case, including EMI, fuel, operations, insurance, drivers, retained travel and any final loan settlement. Today is evaluated separately; resale receipts do not offset the monthly outflow cap. Provisional input warnings still apply."
    )


def _budget_upfront(assessment):
    limit, delta = assessment["upfront_limit"], assessment["upfront_delta"]
    if limit is None:
        return f"Upfront constraint: {_cash_money(assessment['upfront'])} needed; no limit set."
    return (
        f"Upfront constraint: {_cash_money(assessment['upfront'])} needed against {_cash_money(limit)} limit; "
        + (
            f"exceeds by {_cash_money(delta)}."
            if delta > 0
            else f"meets limit with {_cash_money(-delta)} remaining."
        )
    )


def _cash_money(value):
    return "Not set" if value is None else money(value, places=2)


def _sources(workspace):
    source = workspace.scenario.source_snapshot
    if source:
        st.caption(
            f"Saved car reference: {source.get('city', 'Bengaluru')} · researched {source.get('researched_on', 'not stated')}"
        )
        for label, key in [
            ("Car price reference", "source_url"),
            ("Manufacturer", "manufacturer_url"),
        ]:
            url = source.get(key)
            if url and urlparse(url).scheme in {"http", "https"}:
                st.link_button(
                    label,
                    url,
                    on_click=queue_notice,
                    args=(f"Opening {label.lower()}.",),
                    kwargs={"level": "info"},
                )
    else:
        st.caption("Custom purchase assumption entered by you; no preset source is attached.")
    st.markdown(
        f"[India CPI reference: August 2026, 4.82%]({INFLATION_URL}). The editable future inflation assumption uses this dated annual CPI reading as a starting point. It is not a five-year inflation forecast."
    )
    if workspace.cab_forecast.source == "groups":
        st.caption(
            f"Advanced default research: {RESEARCHED_ON:%d %B %Y}. Saved overrides are used in this calculation."
        )
        for assumption in ESCALATION_DEFAULTS.values():
            st.caption(
                f"{assumption.label}: default {assumption.annual_percent}%. {assumption.basis}"
            )
            for title, url in assumption.sources:
                st.markdown(f"[{title}]({url})")
    st.markdown(
        "[ACKO premium calculator](https://www.acko.com/car-insurance/premium-calculator/) · [ACKO third-party cover](https://www.acko.com/third-party-car-insurance/)"
    )


def _report(workspace, result, demand, efficiency, resale):
    def table(rows):
        return pd.DataFrame(rows).to_html(index=False, escape=True, border=0)

    receipt_source = workspace.cab_forecast.source == "receipts"
    has_bus = not receipt_source and any(
        g.transport_mode == "bus" for g in selected_groups(workspace)
    )
    fare_label = "Travel" if has_bus else "Cabs"
    basis, confidence = _forecast_description(workspace)
    headline, decision_evidence, action = _decision_copy(result, has_bus)
    outcomes = _outcomes(result, has_bus).to_html(index=False, escape=True, border=0)
    threshold = (
        f"<p>{escape(_threshold_description(result, has_bus))}</p>"
        if assess_decision(result).base_status == "conditional"
        else ""
    )
    financial = table(
        [
            {"Measure": "Cash needed today", "Amount": money(result.upfront_cash)},
            {
                "Measure": f"Continue {fare_label.lower()} · five-year net cost",
                "Amount": _range(result.ride_total_low, result.ride_total_high),
            },
            {
                "Measure": "Buy car · cash paid including final loan settlement",
                "Amount": _range(
                    result.ownership_cash_low + result.outstanding_balance,
                    result.ownership_cash_high + result.outstanding_balance,
                ),
            },
            {
                "Measure": "Final loan settlement (included in cash paid)",
                "Amount": money(result.outstanding_balance),
            },
            {"Measure": "Resale received", "Amount": money(result.resale_value)},
            {
                "Measure": "Buy car · five-year net cost",
                "Amount": _range(result.ownership_net_low, result.ownership_net_high),
            },
        ]
    )
    funding = purchase_funding(workspace.scenario)
    purchase = table(
        [
            {
                "Purchase component": "On-road price including initial insurance",
                "INR": money(funding.acquisition, places=2),
            },
            {"Purchase component": "Loan funding (subtract)", "INR": money(funding.loan, places=2)},
            {
                "Purchase component": "Calculated down payment",
                "INR": money(funding.down_payment, places=2),
            },
            {
                "Purchase component": "Separate costs outside the quote (add)",
                "INR": money(funding.extras, places=2),
            },
            {
                "Purchase component": "Cash needed today",
                "INR": money(funding.cash_needed, places=2),
            },
        ]
    )
    assumptions = (
        "<pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"
        + escape(
            json.dumps(
                effective_assumptions(workspace, demand, efficiency, resale),
                indent=2,
                ensure_ascii=False,
            )
        )
        + "</pre>"
    )
    settings = table(
        [
            {"Inactive setting": key, "Not applied": str(value)}
            for key, value in inactive_assumptions(workspace).items()
        ]
    )
    budget = budget_assessment(workspace, result)
    budget_text = (
        escape(_budget_upfront(budget))
        + f"<p>Highest monthly cash out: {_cash_money(budget['peak'])}, months {', '.join(budget['peak_months'])}. Monthly limit: {_cash_money(budget['monthly_limit'])}. Peak minus limit: {_cash_money(budget['peak_delta'])}. Average scheduled cash out: {_cash_money(budget['average'])}.</p>"
    )
    budget_text += (
        table(
            [
                {
                    key: money(value, places=2) if isinstance(value, Decimal) else value
                    for key, value in row.items()
                }
                for row in budget["breaches"]
            ]
        )
        if budget["breaches"]
        else "<p>No breaches identified against entered monthly cap (if set).</p>"
    )
    budget_text += "<p>Uses the upper fare case and gross scheduled outflows, including final loan settlement; today and resale inflows are evaluated separately. An average is not a strict cap check. Provisional warnings still apply.</p>"
    if receipt_source:
        evidence = table(
            [
                {
                    "Month": month.month,
                    "Spending": money(month.total),
                    "Receipts": month.receipt_count,
                    "Coverage": month.coverage,
                }
                for month in receipt_baseline(workspace).months
            ]
        )
    else:
        evidence = table(
            [
                {"Group": group.name, "Saved inputs": str(group.model_dump(mode="json"))}
                for group in selected_groups(workspace)
            ]
        )
    monthly = _cash_table(monthly_cash_flows(result), fare_label).to_html(
        index=False, escape=True, border=0
    )
    yearly = _cash_table(yearly_cash_flows(result), fare_label, total=True).to_html(
        index=False, escape=True, border=0
    )
    source = workspace.scenario.source_snapshot
    links = ""
    source_links = [
        ("India CPI · August 2026 · 4.82%", INFLATION_URL),
        ("Car reference", source.get("source_url")),
        ("Manufacturer", source.get("manufacturer_url")),
        ("ACKO calculator", "https://www.acko.com/car-insurance/premium-calculator/"),
    ]
    default_references = ""
    if not receipt_source:
        default_references = (
            f"<p>Advanced planning defaults researched {RESEARCHED_ON:%d %B %Y}. "
            "The saved rates above are used, including any user overrides.</p>"
        )
        for assumption in ESCALATION_DEFAULTS.values():
            default_references += (
                f"<p><strong>{escape(assumption.label)}: default {assumption.annual_percent}%.</strong> "
                f"{escape(assumption.basis)}</p>"
            )
            source_links.extend(assumption.sources)
    for label, url in dict.fromkeys(source_links):
        if url and urlparse(url).scheme in {"http", "https"}:
            links += f'<p><a href="{escape(url, quote=True)}">{escape(label)}</a></p>'
    notes = "".join(
        f"<p><strong>{escape(key.title())}</strong>: {escape(value)}</p>"
        for key, value in workspace.personal_considerations.items()
        if value
    )
    warnings = "".join(f"<li>{escape(warning)}</li>" for warning in result.warnings)
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RideOrOwn five-year decision</title>
<style>body{{font-family:system-ui,sans-serif;color:#153b38;max-width:1100px;margin:40px auto;padding:0 24px;line-height:1.5}}h1,h2{{color:#087f75}}table{{border-collapse:collapse;width:100%;font-size:12px;margin:20px 0}}td,th{{border-bottom:1px solid #d7e3dc;text-align:left;padding:8px;vertical-align:top;overflow-wrap:anywhere}}th{{background:#edf4ef}}.wide{{overflow:auto}}@media print{{body{{margin:0;font-size:11px}}table{{font-size:9px}}h2{{break-after:avoid}}a{{color:inherit}}.wide{{overflow:visible}}}}</style>
<h1>RideOrOwn · Your five-year decision</h1><p>{escape(workspace.scenario.name)} · forecast begins {workspace.forecast_start:%B %Y}</p>
<p><strong>{escape(headline)}</strong></p><p>{escape(decision_evidence)}</p><p>{escape(action)}</p><ul>{warnings}</ul>
<h2>Which choice saves money?</h2>{outcomes}{threshold}
<p>These are alternative fare scenarios for the same journeys. Savings compare matching assumptions, so any retained travel cancels. The range does not give the probability of either outcome.</p>
<h2>What buying requires</h2>{purchase}{financial}
<p>All amounts are Indian rupees (INR), grouped in thousands, lakhs and crores. Cards and cash-flow summaries round to whole rupees; purchase details and cash limits show paise. Calculations retain Decimal precision. Loan EMI is a fixed-rate monthly reducing-balance estimate using the nominal annual rate divided by 12, with instalments rounded to paise and the final instalment adjusted. Confirm actual payments and fees against the lender's Key Facts Statement; daily interest or rate changes can differ. Separately paid fees and applicable taxes belong in extra costs only when outside the quote.</p>
<h2>Upfront and strict monthly cash limits</h2>{budget_text}
<p>{escape(comparison_scope(workspace))}</p>
<h2>Forecast basis</h2><p>{escape(basis)}</p><p>{escape(confidence)}</p>
<p>{escape(_inflation_description(workspace))} The CPI source is a dated reference, not a five-year forecast.</p>
<p>Displayed sensitivity: replaceable demand {demand}%, fuel efficiency {efficiency}%, resale {resale}%. Retained fares and additional driving stay fixed.</p>
<h2>Year-by-year cash flow</h2><p>Net cost = cash out − cash in. Today includes your contribution after loan funding. Year 5 includes selling the car and settling any remaining loan. The total reconciles to the financial comparison above.</p>{yearly}
<h2>How to read the decision</h2><p>Future rupees include inflation. Fare ranges describe scenarios without selecting a midpoint; savings compare matching fare assumptions, so retained fares cancel. Purchase cash and repayments count the acquisition once. Resale represents remaining value; no separate depreciation charge is added. Investment returns and the time value of money are excluded. Six months of evidence do not establish annual seasonality or guarantee future demand.</p>
<h2>Effective calculation inputs (saved baseline plus displayed sensitivity)</h2>{assumptions}
<h2>Inactive stored settings — not applied</h2>{settings}<h2>{"Six-month receipt evidence (final payment where entered; otherwise provisional selected price)" if receipt_source else "Selected future travel patterns"}</h2><div class="wide">{evidence}</div>
<h2>Personal considerations</h2>{notes or "<p>No notes entered.</p>"}
<h2>Sources</h2><p>Car reference researched {escape(str(source.get("researched_on", "not applicable")))}. Insurance amounts are user quotes or labelled budgets.</p>{default_references}{links}
<h2>Full monthly cash flow</h2><p>Today and all 60 forecast months are included. The final month contains resale inflow and loan settlement outflow.</p><div class="wide">{monthly}</div></html>"""
