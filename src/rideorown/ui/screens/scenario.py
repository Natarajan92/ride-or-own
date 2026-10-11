"""A sequential car-cost plan with an explicit saved cash-flow preview."""

from decimal import Decimal

import streamlit as st

from rideorown.domain.assumptions import (
    ESCALATION_DEFAULTS,
    RESEARCHED_ON,
    escalation_default_values,
)
from rideorown.domain.loans import amortize
from rideorown.domain.models import OwnershipScenario
from rideorown.domain.ownership import normalized_acquisition, purchase_funding, scenario_errors
from rideorown.services.catalog import get_car_presets, get_insurance_metadata
from rideorown.ui.components import (
    amount_input,
    decimal_value,
    metric_card,
    money,
    notify,
    page_intro,
    queue_notice,
    rupee_value,
    save,
)


def _preset_label(preset):
    return f"{preset['brand']} {preset['model']} · {preset['variant']} · {preset['transmission']}"


def render(repo, workspace):
    page_intro(
        "04 / New car estimation & purchase plan",
        "Plan your new car purchase.",
        "Estimate purchase, running and resale costs over five years.",
    )
    scenario = workspace.scenario
    st.subheader("1. Choose your car")
    presets = get_car_presets()
    ids = [None] + [p["id"] for p in presets]
    selected = st.selectbox(
        "Car reference",
        ids,
        index=ids.index(scenario.preset_id) if scenario.preset_id in ids else 0,
        format_func=lambda pid: (
            "Custom car / dealer quote"
            if pid is None
            else _preset_label(next(p for p in presets if p["id"] == pid))
        ),
    )
    selected_preset = next((p for p in presets if p["id"] == selected), None)
    if selected_preset:
        a, b = st.columns([2, 1])
        with a:
            st.write(f"**{_preset_label(selected_preset)}**")
            with st.expander("Reference details"):
                st.caption(
                    f"{selected_preset['city']} · researched {selected_preset['researched_on']} · effective price date: {selected_preset.get('price_effective_date') or 'not stated'}"
                )
                st.markdown(
                    f"[Reference price source]({selected_preset['source_url']}) · [Manufacturer]({selected_preset['manufacturer_url']})"
                )
                st.caption(selected_preset.get("notes", ""))
        with b:
            metric_card(
                "Published on-road estimate",
                money(Decimal(selected_preset["on_road_price"])),
                "Reference estimate; confirm against your purchase quote",
            )
    if st.button(
        "Use this car reference" if selected_preset else "Use a custom car",
        help="Apply the purchase price, then review the costs below.",
    ):
        values = scenario.model_dump()
        values.update(
            preset_id=selected, source_snapshot=selected_preset or {}, assumptions_confirmed=False
        )
        if selected_preset:
            values.update(
                name=_preset_label(selected_preset),
                on_road_price=Decimal(selected_preset["on_road_price"]),
                embedded_insurance=Decimal(selected_preset["included_insurance"])
                if selected_preset.get("included_insurance") is not None
                else None,
                price_includes_acko=False,
            )
        else:
            values.update(
                name="My custom car",
                on_road_price=None,
                embedded_insurance=None,
                price_includes_acko=False,
            )
        workspace.scenario = OwnershipScenario.model_validate(values)
        save(repo, workspace)
        queue_notice(
            "Car reference applied. Review your purchase plan."
            if selected_preset
            else "Custom car selected. Enter your purchase plan."
        )
        st.rerun()
    _form(repo, workspace)
    with st.container(border=True, key="saved_car_costs"):
        _saved_cost_preview(workspace.scenario)
    errors = scenario_errors(workspace.scenario)
    if errors:
        with st.expander(f"{len(errors)} inputs needed for a complete calculation", expanded=True):
            for error in errors:
                st.write(f"• {error}")
    elif workspace.scenario.assumptions_confirmed:
        st.success("Car plan ready. Continue to Comparison & decision.")
    else:
        st.info("Review your budgets, check the confirmation and save.")
    _insurance_sources()


def _form(repo, workspace):
    current = workspace.scenario
    forecast = getattr(workspace, "cab_forecast", None)
    receipt_forecast = forecast is not None and forecast.source == "receipts"
    with st.form(f"ownership_{current.preset_id or 'custom'}"):
        st.subheader("2. Confirm the purchase price")
        st.caption(
            "Enter the total on-road amount in INR, including dealer taxes, RTO/registration "
            "and initial insurance. Include initial insurance once; do not add the down payment here."
        )
        price = amount_input("Reference on-road price / dealer quote (₹)", current.on_road_price)
        includes = st.checkbox(
            "My entered purchase quote already includes ACKO initial cover",
            value=current.price_includes_acko,
            help="If selected, the entered on-road amount is used as-is. Do not subtract and add insurance again.",
        )
        with st.expander(
            "Initial insurance in your purchase price", expanded=not current.price_includes_acko
        ):
            st.caption(
                "Replace the reference insurance with your ACKO budget. Both fields are ignored if your quote already includes ACKO."
            )
            a, b = st.columns(2)
            with a:
                embedded = amount_input(
                    "Insurance included in reference price (₹)",
                    current.embedded_insurance,
                    help="Required when the quote is not already ACKO-inclusive. Get this component from the source or dealer; do not assume zero.",
                )
            with b:
                initial = amount_input(
                    "Initial ACKO premium / labelled budget (₹)",
                    current.acko_initial,
                    help="Initial third-party and own-damage cover. Ignored as an added cost when your quote already includes ACKO.",
                )

        st.divider()
        st.subheader("3. Decide how to pay")
        st.caption(
            "Down payment is calculated automatically: on-road purchase price minus loan. "
            "Enter the amount borrowed in Loan principal, not the down payment."
        )
        financing = st.radio(
            "How will you pay?",
            ["cash", "loan"],
            index=0 if current.financing == "cash" else 1,
            format_func=lambda v: (
                "Cash purchase" if v == "cash" else "Fixed-rate reducing-balance loan"
            ),
            horizontal=True,
        )
        with st.expander(
            "Loan details · complete if choosing a loan", expanded=current.financing == "loan"
        ):
            a, b, c = st.columns(3)
            with a:
                principal = amount_input(
                    "Loan principal (₹), used for loan purchases",
                    current.loan_principal,
                    help="Amount the lender funds towards the car. ₹5 lakh means 5,00,000.",
                )
            with b:
                interest = st.number_input(
                    "Annual loan interest (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(current.annual_interest_rate),
                    step=0.25,
                )
            with c:
                term = st.number_input(
                    "Loan tenure (months)",
                    min_value=1,
                    max_value=120,
                    value=current.loan_months,
                    step=1,
                )
            st.caption(
                "Enter the nominal annual reducing-balance interest rate, not a flat rate or APR. "
                "EMI uses that rate ÷ 12 and the tenure in months; 5 years = 60 months. "
                "A term beyond 60 months leaves a balance at year five."
            )
            st.caption(
                "This is a fixed-rate monthly EMI estimate. Your lender's repayment schedule "
                "may differ with daily interest, disbursement dates or rate changes. "
                "Enter separately paid processing fees including applicable tax as additional costs."
            )
        with st.expander("Additional purchase costs"):
            extras = amount_input(
                "Additional upfront costs not in quote (₹)",
                current.upfront_extras,
                help="Do not enter your down payment here. Enter 0 if the on-road quote covers "
                "everything. Add only separately paid fees, accessories or other costs outside "
                "the quote, including applicable tax; exclude anything already included or financed.",
            )
            st.caption(
                "Example: ₹9,03,000 on-road − ₹5,00,000 loan = ₹4,03,000 down payment. "
                "With no separate extra costs, cash needed today is ₹4,03,000."
            )
        st.caption(
            "Cash needed today = purchase price with insurance − loan + upfront extras. EMI is counted when paid."
        )

        st.divider()
        st.subheader("4. Budget your monthly running costs")
        st.caption(
            "Fuel uses your Cab forecast distance. Enter maintenance and parking budgets, including 0 where appropriate."
        )
        a, b, c = st.columns(3)
        with a:
            city = amount_input("Expected city efficiency (km/l)", current.city_efficiency)
        with b:
            highway = amount_input(
                "Expected outstation efficiency (km/l)", current.highway_efficiency
            )
        with c:
            petrol = amount_input("Petrol price (₹ / litre)", current.petrol_price)
        a, b, c = st.columns(3)
        with a:
            maintenance = amount_input(
                "Monthly maintenance and repairs (₹)",
                current.maintenance_monthly,
                help="A planning budget. Enter 0 explicitly only if that is your assumption.",
            )
        with b:
            parking = amount_input(
                "Monthly fixed parking (₹)",
                current.parking_monthly,
                help="Exclude visit parking already entered on travel groups.",
            )
        with c:
            tolls = amount_input(
                "Other monthly tolls (₹)",
                current.tolls_monthly,
                help="Exclude tolls already entered on travel groups.",
            )
        with st.expander("Additional driving after buying a car"):
            st.caption("Add only extra driving beyond the cab journeys you will replace.")
            a, b = st.columns(2)
            with a:
                additional_city = amount_input(
                    "Additional city driving (km / month)",
                    current.additional_city_km,
                    help="New driving enabled by car ownership, beyond replacement journeys.",
                )
            with b:
                additional_highway = amount_input(
                    "Additional outstation driving (km / month)", current.additional_highway_km
                )
        if receipt_forecast:
            st.info(
                f"Fuel and running costs use {forecast.annual_inflation}% per year from Cab forecast. "
                "EMI, insurance and resale are not increased again."
            )

        st.divider()
        st.subheader("5. Plan annual insurance and resale")
        st.write(
            "Year 1 cover is in the purchase price. Renewal amounts are planning budgets, not live ACKO quotes."
        )
        cols = st.columns(4)
        renewals = []
        for index, col in enumerate(cols):
            with col:
                year = index + 2
                renewals.append(
                    amount_input(
                        f"Year {year} renewal (₹)",
                        current.insurance_renewals[index],
                        key=f"renewal_{current.preset_id}_{year}",
                        help="Own damage + selected add-ons"
                        if year < 4
                        else "Third party + own damage + selected add-ons",
                    )
                )
        with st.expander("Insurance renewal guidance"):
            st.caption(
                "Typical coverage: initial 3-year third-party + 1-year own-damage cover; years 2–3 renew own damage, years 4–5 renew third party and own damage. Confirm this against your actual ACKO policy and selected add-ons."
            )
        resale = amount_input(
            "End of year 5 resale value (₹)",
            current.resale_value,
            help="Enter a future-rupee assumption; no extra depreciation charge is added.",
        )
        st.caption("Resale is received at year five; any remaining loan must be repaid.")

        fare_escalation = current.fare_escalation
        fuel_escalation = current.fuel_escalation
        operating_escalation = current.operating_escalation
        use_escalation_defaults = False
        if not receipt_forecast:
            with st.expander("Annual escalation assumptions · travel-group forecast"):
                defaults_summary = ", ".join(
                    f"{item.label.lower()} {item.annual_percent}%"
                    for item in ESCALATION_DEFAULTS.values()
                )
                st.caption(
                    f"Researched {RESEARCHED_ON:%d %B %Y}. Defaults: {defaults_summary} per year. "
                    "These are editable planning "
                    "assumptions; actual prices can rise or fall."
                )
                a, b, c = st.columns(3)
                with a:
                    fare_escalation = st.number_input(
                        "Cab fare escalation (% / year)",
                        min_value=-50.0,
                        max_value=100.0,
                        value=float(current.fare_escalation),
                        step=1.0,
                        help=ESCALATION_DEFAULTS["fare_escalation"].basis,
                    )
                with b:
                    fuel_escalation = st.number_input(
                        "Fuel price escalation (% / year)",
                        min_value=-50.0,
                        max_value=100.0,
                        value=float(current.fuel_escalation),
                        step=1.0,
                        help=ESCALATION_DEFAULTS["fuel_escalation"].basis,
                    )
                with c:
                    operating_escalation = st.number_input(
                        "Operating budget escalation (% / year)",
                        min_value=-50.0,
                        max_value=100.0,
                        value=float(current.operating_escalation),
                        step=1.0,
                        help=ESCALATION_DEFAULTS["operating_escalation"].basis,
                    )
                for assumption in ESCALATION_DEFAULTS.values():
                    st.caption(
                        f"**{assumption.label} · {assumption.annual_percent}%:** {assumption.basis}"
                    )
                    for title, url in assumption.sources:
                        st.markdown(f"[{title}]({url})")
                st.caption(
                    "These separate rates apply to travel-group forecasts; the fare rate also applies to any bus journeys you select. Fixed EMI, explicit annual insurance budgets, and future resale value are not escalated again. This nominal comparison excludes opportunity cost and discounted cash flow."
                )
        st.divider()
        st.subheader("Review and save your plan")
        with st.expander("Name this car plan"):
            name = st.text_input("Scenario name", value=current.name)
        confirmed = st.checkbox(
            "I have reviewed these ownership budgets, including any explicit zero costs",
            value=current.assumptions_confirmed,
        )
        submitted = st.form_submit_button("Save car scenario", type="primary")
        if not receipt_forecast:
            use_escalation_defaults = st.form_submit_button(
                "Use researched escalation defaults",
                help="Apply the reference rates and save your car inputs. Review the updated budgets before confirming the decision.",
            )
        if submitted or use_escalation_defaults:
            try:
                values = current.model_dump()
                values.update(
                    name=name.strip() or "My car scenario",
                    on_road_price=rupee_value(price, "on-road price", optional=True),
                    price_includes_acko=includes,
                    embedded_insurance=current.embedded_insurance
                    if includes
                    else rupee_value(embedded, "embedded insurance", optional=True),
                    acko_initial=current.acko_initial
                    if includes
                    else rupee_value(initial, "initial ACKO premium", optional=True),
                    financing=financing,
                    loan_principal=current.loan_principal
                    if financing == "cash"
                    else rupee_value(principal, "loan principal", positive=True),
                    annual_interest_rate=Decimal(str(interest)),
                    loan_months=int(term),
                    upfront_extras=rupee_value(extras, "extra upfront costs"),
                    city_efficiency=decimal_value(
                        city, "city efficiency", optional=True, positive=True
                    ),
                    highway_efficiency=decimal_value(
                        highway, "outstation efficiency", optional=True, positive=True
                    ),
                    petrol_price=rupee_value(petrol, "petrol price", optional=True, positive=True),
                    maintenance_monthly=rupee_value(maintenance, "maintenance", optional=True),
                    parking_monthly=rupee_value(parking, "parking", optional=True),
                    tolls_monthly=rupee_value(tolls, "tolls"),
                    additional_city_km=decimal_value(additional_city, "additional city driving"),
                    additional_highway_km=decimal_value(
                        additional_highway, "additional outstation driving"
                    ),
                    resale_value=rupee_value(resale, "resale value", optional=True),
                    insurance_renewals=[
                        rupee_value(value, f"year {index + 2} insurance", optional=True)
                        for index, value in enumerate(renewals)
                    ],
                    fare_escalation=Decimal(str(fare_escalation)),
                    fuel_escalation=Decimal(str(fuel_escalation)),
                    operating_escalation=Decimal(str(operating_escalation)),
                    assumptions_confirmed=confirmed,
                )
                if use_escalation_defaults:
                    values.update(escalation_default_values())
                    values["assumptions_confirmed"] = False
                candidate = OwnershipScenario.model_validate(values)
                try:
                    normalized_acquisition(candidate)
                except ValueError:
                    pass  # Incomplete price/insurance inputs can still be saved for later.
                else:
                    purchase_funding(candidate)  # Reject a loan larger than the purchase price.
                workspace.scenario = candidate
                save(repo, workspace)
                queue_notice(
                    "Car plan saved with researched rates. Review and confirm your budgets."
                    if use_escalation_defaults
                    else "Car plan saved.",
                    level="warning" if use_escalation_defaults else "success",
                )
                st.rerun()
            except ValueError as exc:
                notify(exc, level="error")


def _saved_cost_preview(scenario):
    st.markdown('<div class="eyebrow">Saved plan</div>', unsafe_allow_html=True)
    st.subheader("Your saved car cash flow")
    acquisition = None
    upfront = None
    funding = None
    try:
        funding = purchase_funding(scenario)
        acquisition = funding.acquisition
        upfront = funding.cash_needed
    except ValueError:
        pass
    emi = Decimal(0) if scenario.financing == "cash" else None
    if scenario.financing == "loan" and scenario.loan_principal > 0:
        try:
            emi = amortize(
                scenario.loan_principal, scenario.annual_interest_rate, scenario.loan_months
            )[0].payment
        except ValueError:
            pass
    fixed_running = None
    if scenario.maintenance_monthly is not None and scenario.parking_monthly is not None:
        fixed_running = (
            scenario.maintenance_monthly + scenario.parking_monthly + scenario.tolls_monthly
        )
    a, b, c = st.columns(3)
    with a:
        metric_card("Purchase cash needed", money(upfront), "Paid today, after loan financing")
    with b:
        metric_card("Monthly loan payment", money(emi), "EMI; zero for a cash purchase")
    with c:
        metric_card(
            "Monthly running budget",
            money(fixed_running),
            "Maintenance, fixed parking and tolls; before fuel",
        )
    if acquisition is not None:
        st.caption(
            f"ACKO-adjusted acquisition price: {money(acquisition)}. Initial insurance is included once."
        )
    if funding is not None:
        st.caption(
            f"Down payment: {money(funding.acquisition, places=2)} − "
            f"{money(funding.loan, places=2)} loan = {money(funding.down_payment, places=2)}. "
            f"Additional costs outside quote: {money(funding.extras, places=2)}. "
            f"Cash needed today: {money(funding.cash_needed, places=2)}."
        )
        if funding.extras > 0 and funding.extras == funding.down_payment:
            st.warning(
                "Your additional costs equal your calculated down payment. If you entered "
                "the down payment in Additional upfront costs, change that field to 0 and save. "
                "Keep an amount there only for separate costs outside the on-road quote."
            )
    if emi is not None and scenario.financing == "loan":
        st.caption(
            f"Estimated EMI: {money(emi, places=2)} at {scenario.annual_interest_rate}% "
            f"per year for {scenario.loan_months} months. Cards round to the nearest rupee; "
            "the repayment schedule uses paise and adjusts the final instalment."
        )


def _insurance_sources():
    with st.expander("Insurance sources and assumptions"):
        metadata = get_insurance_metadata()
        st.caption(
            "This plan uses ACKO. Replace any other insurer's amount included in your reference price."
        )
        sources = metadata.get("sources", [])
        if isinstance(sources, dict):
            sources = [{"label": label, "url": url} for label, url in sources.items()]
        for source in sources:
            if isinstance(source, dict):
                url = source.get("url") or source.get("source_url")
                label = source.get("label") or source.get("title") or "ACKO source"
                if url:
                    st.markdown(f"[{label}]({url})")
            elif isinstance(source, str):
                st.markdown(f"[ACKO source]({source})")
        if not sources:
            st.markdown(
                "[ACKO insurance calculator](https://www.acko.com/car-insurance/premium-calculator/)"
            )
