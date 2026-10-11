"""Exercise the user-facing workflows against an isolated local workspace."""

import json
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from streamlit.testing.v1 import AppTest, element_tree

from rideorown.domain.models import Receipt
from rideorown.domain.travel import add_months
from rideorown.services.application import get_repository
from rideorown.services.forecast import calculate_workspace_comparison
from tests.example_groups import example_groups

APP = Path(__file__).resolve().parents[2] / "app.py"
SCREENS = [
    "1 · Cab receipts",
    "2 · Advanced travel settings",
    "3 · Cab forecast",
    "4 · New car plan",
    "5 · Comparison & decision",
]


def element(elements, label):
    return next(item for item in elements if item.label == label)


@pytest.fixture
def app(tmp_path, monkeypatch, request):
    monkeypatch.setenv("RIDEOROWN_DATA_DIR", str(tmp_path / "local-data"))
    # Existing-workspace workflows explicitly use saved example journeys.
    if request.node.name != "test_initial_workspace_and_all_five_screens":
        repo = get_repository()
        from rideorown.domain.models import Workspace

        repo.save(Workspace(groups=example_groups(10)))
    original_states = element_tree.ElementTree.get_widget_states

    def widget_states_with_selections(tree):
        states = original_states(tree)
        # AppTest omits selectable dataframes. Send their state as a browser does.
        for node in tree:
            if (
                isinstance(node, element_tree.Dataframe)
                and node.key is not None
                and node.proto.selection_mode
            ):
                state = states.widgets.add()
                state.id = node.proto.id
                state.string_value = json.dumps(tree.session_state[node.key])
        return states

    monkeypatch.setattr(
        element_tree.ElementTree, "get_widget_states", widget_states_with_selections
    )
    return AppTest.from_file(str(APP), default_timeout=30).run()


def navigate(app, screen):
    screen = {
        "Capture travel": SCREENS[0],
        "Advanced travel settings": SCREENS[1],
        "Travel patterns": SCREENS[2],
        "Car scenario": SCREENS[3],
        "Five-year comparison": SCREENS[4],
    }.get(screen, screen)
    app.sidebar.radio[0].set_value(screen).run()
    assert not app.exception


def select_group(app, group_id):
    if app.sidebar.radio[0].value != SCREENS[1]:
        navigate(app, "Advanced travel settings")
    table = next(item for item in app.dataframe if item.key == "travel_groups_table")
    group_ids = [g.id for g in app.session_state["workspace"].groups]
    # AppTest exposes dataframe data but has no row-selection interaction helper.
    app.session_state[table.key] = {"selection": {"rows": [group_ids.index(group_id)]}}
    app.run()
    assert not app.exception


def use_group_forecast(app):
    navigate(app, "Travel patterns")
    element(app.radio, "Build my forecast from").set_value("groups").run()
    assert not app.exception


def test_initial_workspace_and_all_five_screens(app):
    assert not app.exception
    assert not app.tabs
    assert app.sidebar.radio[0].options == SCREENS
    assert app.session_state["workspace"].groups == []
    assert app.session_state["workspace"].receipts == []
    navigate(app, "Advanced travel settings")
    assert not any(item.key == "travel_groups_table" for item in app.dataframe)
    assert element(app.button, "New travel group")
    assert app.session_state["workspace"].cab_forecast.source == "receipts"
    navigate(app, "Travel patterns")
    assert any("Import receipts in step 1" in info.value for info in app.info)
    assert element(app.text_input, "Annual India inflation assumption (%)").value == "4.82"
    navigate(app, "Car scenario")
    assert any("inputs needed" in expander.label for expander in app.expander)
    navigate(app, "Five-year comparison")
    assert app.info or app.error


def test_continue_and_back_follow_five_steps_and_preserve_saved_travel_settings(app):
    element(app.button, "Continue to Advanced travel settings").click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == SCREENS[1]
    select_group(app, "office-commute")
    element(app.text_input, "Group name").set_value("My office travel")
    element(app.button, "Save travel group").click().run()
    assert not app.exception
    assert get_repository().load().cab_forecast.source == "receipts"

    for screen in SCREENS[2:]:
        element(app.button, f"Continue to {screen.split(' · ')[1]}").click().run()
        assert not app.exception
        assert app.sidebar.radio[0].value == screen
        assert app.session_state["workspace"].groups[0].name == "My office travel"
        assert app.session_state["workspace"].cab_forecast.source == "receipts"
    assert not any(item.label.startswith("Continue to ") for item in app.button)

    for screen in reversed(SCREENS[:-1]):
        element(app.button, f"Back to {screen.split(' · ')[1]}").click().run()
        assert not app.exception
        assert app.sidebar.radio[0].value == screen
        assert app.session_state["workspace"].groups[0].name == "My office travel"
        if screen == SCREENS[1]:
            select_group(app, "office-commute")
            assert element(app.text_input, "Group name").value == "My office travel"
    assert not any(item.label.startswith("Back to ") for item in app.button)


@pytest.mark.parametrize(
    ("legacy_screen", "screen"),
    [
        ("2 · Cab forecast", SCREENS[2]),
        ("3 · Car costs", SCREENS[3]),
        ("4 · Five-year decision", SCREENS[4]),
    ],
)
def test_existing_session_route_migrates_into_the_five_page_journey(app, legacy_screen, screen):
    existing_session = AppTest.from_file(str(APP), default_timeout=30)
    existing_session.session_state["journey_step"] = legacy_screen
    existing_session.run()
    assert not existing_session.exception
    assert existing_session.sidebar.radio[0].value == screen
    assert len(existing_session.session_state["workspace"].groups) == 3


def test_create_group_without_creating_receipts(app):
    select_group(app, "office-commute")
    element(app.button, "New travel group").click().run()
    assert not app.exception
    assert element(app.text_input, "Group name").value == ""
    assert app.session_state["travel_groups_table"]["selection"]["rows"] == []
    element(app.text_input, "Group name").set_value("Airport rides")
    element(app.text_input, "Visits per period / outings per parent visit").set_value("2")
    element(app.text_input, "Distance per complete visit / round trip (km)").set_value("80")
    element(app.text_input, "Cab fare per complete visit — lower (₹)").set_value("1800")
    element(app.text_input, "Cab fare per complete visit — upper (₹)").set_value("2200")
    element(app.button, "Save travel group").click().run()
    assert not app.exception
    saved = get_repository().load()
    assert len(saved.groups) == 4
    assert next(g for g in saved.groups if g.name == "Airport rides").visits == Decimal(2)
    assert element(app.text_input, "Group name").value == "Airport rides"
    assert saved.receipts == []
    assert saved.monthly_entries == []
    assert saved.cab_forecast.source == "receipts"


def test_selected_row_edits_only_that_group_and_stays_selected_after_save(app):
    assert not any(item.label == "Create or edit a group" for item in app.selectbox)
    select_group(app, "office-commute")
    assert element(app.text_input, "Group name").value == "Office commute"
    element(app.text_input, "Group name").set_value("Office commute updated")
    element(app.text_input, "Visits per period / outings per parent visit").set_value("5")
    element(app.button, "Save travel group").click().run()
    assert not app.exception
    saved = get_repository().load()
    assert len(saved.groups) == 3
    assert saved.groups[0].name == "Office commute updated"
    assert saved.groups[0].visits == Decimal(5)
    assert saved.groups[1].visits == saved.groups[2].visits == Decimal(1)
    assert saved.cab_forecast.source == "receipts"
    assert element(app.text_input, "Group name").value == "Office commute updated"
    table = next(item for item in app.dataframe if item.key == "travel_groups_table")
    assert table.value.iloc[0]["Pattern"] == "5 / weekly"
    select_group(app, "regional-visit")
    assert element(app.text_input, "Group name").value == saved.groups[1].name
    assert element(app.text_input, "Distance per complete visit / round trip (km)").value == "1200"


def test_removing_selected_group_clears_edit_form_and_keeps_other_rows_correct(app):
    select_group(app, "office-commute")
    element(app.checkbox, "Remove Office commute and its future pattern").check().run()
    element(app.button, "Remove travel group").click().run()
    assert not app.exception
    assert [g.id for g in get_repository().load().groups] == ["regional-visit", "linked-local"]
    assert get_repository().load().cab_forecast.source == "receipts"
    assert app.session_state["travel_groups_table"]["selection"]["rows"] == []
    assert element(app.text_input, "Group name").value == ""
    select_group(app, "regional-visit")
    assert element(app.text_input, "Group name").value == "Regional round trip"


def test_office_commute_switches_between_bus_and_saved_cab_fares(app):
    select_group(app, "office-commute")
    element(app.text_input, "Separately paid tip per visit (₹)").set_value("50")
    element(app.button, "Save travel group").click().run()
    assert not app.exception

    element(app.selectbox, "Travel mode").set_value("bus").run()
    assert not app.exception
    assert element(app.text_input, "Bus fare per day / complete visit (₹)").value == "200"
    assert not any("Cab fare per complete visit" in item.label for item in app.text_input)
    element(app.button, "Save travel group").click().run()
    assert not app.exception
    saved = get_repository().load()
    office = next(g for g in saved.groups if g.id == "office-commute")
    assert office.transport_mode == "bus"
    assert office.bus_cost_per_visit == Decimal(200)
    assert office.fare_low == office.fare_high == Decimal(200)
    assert office.cab_cost_low == Decimal(1600)
    assert office.cab_cost_high == Decimal(2800)
    assert office.tip_per_visit == Decimal(50)
    assert saved.receipts == []
    assert saved.monthly_entries == []

    # A fresh session must load the saved bus selection and update projections.
    reloaded = AppTest.from_file(str(APP), default_timeout=30).run()
    select_group(reloaded, "office-commute")
    assert element(reloaded.selectbox, "Travel mode").value == "bus"
    use_group_forecast(reloaded)
    assert any("First-year travel spending" in item.value for item in reloaded.markdown)

    navigate(reloaded, "Capture travel")
    select_group(reloaded, "office-commute")
    element(reloaded.selectbox, "Travel mode").set_value("cab").run()
    assert element(reloaded.text_input, "Cab fare per complete visit — lower (₹)").value == "1,600"
    assert element(reloaded.text_input, "Cab fare per complete visit — upper (₹)").value == "2,800"
    assert element(reloaded.text_input, "Separately paid tip per visit (₹)").value == "50"
    element(reloaded.button, "Save travel group").click().run()
    assert not reloaded.exception
    office = next(g for g in get_repository().load().groups if g.id == "office-commute")
    assert office.transport_mode == "cab"
    assert office.fare_low == Decimal(1650)
    assert office.fare_high == Decimal(2850)
    assert office.bus_cost_per_visit == Decimal(200)


@pytest.mark.parametrize("transport", ["cab", "bus"])
def test_complete_scenario_and_explore_provisional_comparison(app, transport):
    select_group(app, "office-commute")
    if transport == "bus":
        select_group(app, "office-commute")
        element(app.selectbox, "Travel mode").set_value("bus").run()
        element(app.button, "Save travel group").click().run()
        assert not app.exception
    use_group_forecast(app)
    navigate(app, "Car scenario")
    values = {
        "Reference on-road price / dealer quote (₹)": "900000",
        "Insurance included in reference price (₹)": "30000",
        "Initial ACKO premium / labelled budget (₹)": "25000",
        "Expected city efficiency (km/l)": "10",
        "Expected outstation efficiency (km/l)": "16",
        "Petrol price (₹ / litre)": "105",
        "Monthly maintenance and repairs (₹)": "1500",
        "Monthly fixed parking (₹)": "1000",
        "End of year 5 resale value (₹)": "350000",
        "Year 2 renewal (₹)": "15000",
        "Year 3 renewal (₹)": "15000",
        "Year 4 renewal (₹)": "20000",
        "Year 5 renewal (₹)": "20000",
    }
    for label, value in values.items():
        element(app.text_input, label).set_value(value)
    element(
        app.checkbox, "I have reviewed these ownership budgets, including any explicit zero costs"
    ).check()
    element(app.button, "Save car scenario").click().run()
    assert not app.exception
    assert get_repository().load().scenario.assumptions_confirmed

    navigate(app, "Five-year comparison")
    assert any("Provisional" in warning.value for warning in app.warning)
    assert any(
        "Local connecting travel" in caption.value and "missing" in caption.value
        for caption in app.caption
    )
    travel_label = "Travel" if transport == "bus" else "Cabs"
    monthly = next(
        item.value for item in app.dataframe if f"{travel_label} lower" in item.value.columns
    )
    # The second month has only office travel under the default quarterly schedule.
    second_month = add_months(app.session_state["workspace"].forecast_start, 1).strftime("%Y-%m")
    second = monthly[monthly["Month"] == second_month].iloc[0]
    assert second[f"{travel_label} lower"] == (2600 if transport == "bus" else 20800)
    assert second[f"{travel_label} upper"] == (2600 if transport == "bus" else 36400)
    element(app.slider, "Replaceable travel demand (%)").set_value(125).run()
    assert not app.exception
    element(app.slider, "Fuel efficiency vs your estimate (%)").set_value(80).run()
    assert not app.exception
    # Sensitivity is exploratory, rather than rewriting the saved car inputs.
    assert get_repository().load().scenario.city_efficiency == Decimal(10)
    assert any(
        button.label == "Download printable comparison" for button in app.get("download_button")
    )


def test_group_removal_requires_child_unlinking(app):
    select_group(app, "regional-visit")
    assert not app.exception
    assert any("Unlink or remove" in warning.value for warning in app.warning)
    assert not any(button.label == "Remove travel group" for button in app.button)


def test_receipt_final_paid_amount_includes_tip_and_corrections_preserve_original(app):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    pdf = output.getvalue()
    receipt = Receipt(
        filename="synthetic.pdf",
        file_hash=sha256(pdf).hexdigest(),
        selected_price=Decimal(100),
        tip_estimate=Decimal(50),
        original={"selected_price": "100"},
    )
    workspace = app.session_state["workspace"]
    workspace.receipts.append(receipt)
    workspace.cab_forecast.coverage_confirmed = True
    repo = get_repository()
    repo.put_pdf(receipt, pdf)
    repo.save(workspace)
    app.run()
    assert not app.exception
    assert element(app.text_input, "Final payment you made (₹, total paid)").value == ""
    assert not any(item.label == "Separately paid tip (₹)" for item in app.text_input)
    assert not any("Confirmed final payment" in item.label for item in app.text_input)
    element(app.text_input, "Final payment you made (₹, total paid)").set_value("120")
    element(app.button, "Save receipt corrections").click().run()
    assert not app.exception
    saved = repo.load().receipts[0]
    assert saved.spending == Decimal(120)
    assert saved.spending_status == "User-entered final payment"
    assert saved.tip_estimate == 50
    assert saved.tip_paid is None
    assert saved.selected_price == 100
    assert saved.final_paid == 120
    assert saved.original == {"selected_price": "100"}
    assert saved.corrections["final_paid"] == "120"
    assert repo.get_pdf(saved) == pdf
    receipt_table = next(item.value for item in app.dataframe if "File" in item.value.columns)
    assert receipt_table.iloc[0]["User-entered final payment"] == 120
    assert "Separate tip" not in receipt_table.columns
    assert not get_repository().load().cab_forecast.coverage_confirmed
    navigate(app, "Travel patterns")
    assert any("undated" in item.value for item in app.error)


def test_receipts_to_inflated_forecast_to_car_decision_with_cash_reconciliation(app):
    workspace = app.session_state["workspace"]
    repo = get_repository()
    for offset in range(6):
        month = add_months(workspace.forecast_start, offset - 6)
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_metadata({"/Title": month.isoformat()})
        output = BytesIO()
        writer.write(output)
        pdf = output.getvalue()
        receipt = Receipt(
            filename=f"{month:%Y-%m}.pdf",
            file_hash=sha256(pdf).hexdigest(),
            ride_at=datetime(month.year, month.month, 15),
            selected_price=Decimal(10000),
        )
        workspace.receipts.append(receipt)
        repo.put_pdf(receipt, pdf)
    repo.save(workspace)
    app.run()
    element(app.button, "Continue to Advanced travel settings").click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == SCREENS[1]
    element(app.button, "Continue to Cab forecast").click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == SCREENS[2]
    element(
        app.checkbox, "These six months include all my cab spending and represent my future travel"
    ).check()
    element(app.text_input, "Monthly city kilometres you will drive").set_value("600")
    element(app.text_input, "Monthly outstation kilometres you will drive").set_value("0")
    element(app.button, "Save cab forecast").click().run()
    assert not app.exception
    assert repo.load().cab_forecast.coverage_confirmed
    projection = next(
        item.value for item in app.dataframe if "Cab cash paid (₹)" in item.value.columns
    )
    assert projection.iloc[0]["Cab cash paid (₹)"] == 120000
    assert projection.iloc[1]["Cab cash paid (₹)"] == pytest.approx(125784)
    assert any("₹10,000" in item.value for item in app.markdown)

    element(app.button, "Continue to New car plan").click().run()
    assert not app.exception
    assert not any("escalation" in item.label.lower() for item in app.number_input)
    values = {
        "Reference on-road price / dealer quote (₹)": "900000",
        "Insurance included in reference price (₹)": "30000",
        "Initial ACKO premium / labelled budget (₹)": "25000",
        "Expected city efficiency (km/l)": "10",
        "Expected outstation efficiency (km/l)": "16",
        "Petrol price (₹ / litre)": "100",
        "Monthly maintenance and repairs (₹)": "1500",
        "Monthly fixed parking (₹)": "1000",
        "End of year 5 resale value (₹)": "350000",
        "Year 2 renewal (₹)": "15000",
        "Year 3 renewal (₹)": "15000",
        "Year 4 renewal (₹)": "20000",
        "Year 5 renewal (₹)": "20000",
    }
    for label, value in values.items():
        element(app.text_input, label).set_value(value)
    element(
        app.checkbox, "I have reviewed these ownership budgets, including any explicit zero costs"
    ).check()
    element(app.button, "Save car scenario").click().run()
    element(app.button, "Continue to Comparison & decision").click().run()
    assert not app.exception
    result = calculate_workspace_comparison(repo.load())
    assert any("final payment" in warning for warning in result.warnings)
    assert result.ride_total_low == result.ride_total_high
    assert result.months[0].fuel == 6000
    assert result.months[12].fuel == Decimal("6289.2")
    assert any("60000" in str(item.value) or "60,000" in str(item.value) for item in app.caption)
    monthly = next(item.value for item in app.dataframe if "Resale received" in item.value.columns)
    assert len(monthly) == 61
    assert monthly.iloc[0]["Month"] == "Today"
    assert monthly.iloc[0]["Car cash out lower"] == 895000
    assert monthly.iloc[-1]["Car cash in"] == 350000
    assert monthly["Car net lower"].sum() == pytest.approx(float(result.ownership_net_low))
    assert monthly["Cabs lower"].sum() == pytest.approx(float(result.ride_total_low))

    # A changed saved history period must be reviewed again before a final decision.
    navigate(app, "Travel patterns")
    first = element(app.date_input, "First month of the six-month receipt period")
    first.set_value(add_months(first.value, -1))
    element(app.button, "Save cab forecast").click().run()
    assert not app.exception
    assert not repo.load().cab_forecast.coverage_confirmed


def test_confirmed_zero_spending_months_show_a_zero_cab_forecast(app):
    navigate(app, "Travel patterns")
    element(
        app.checkbox, "These six months include all my cab spending and represent my future travel"
    ).check()
    element(app.text_input, "Monthly city kilometres you will drive").set_value("0")
    element(app.text_input, "Monthly outstation kilometres you will drive").set_value("0")
    element(app.button, "Save cab forecast").click().run()
    assert not app.exception
    table = next(item.value for item in app.dataframe if "Cab cash paid (₹)" in item.value.columns)
    assert table["Cab cash paid (₹)"].sum() == 0
    assert any(
        "Average cab spending / month" in item.value and "₹0" in item.value for item in app.markdown
    )


def test_advanced_escalation_defaults_show_sources_and_preserve_user_overrides(app):
    select_group(app, "office-commute")
    use_group_forecast(app)
    navigate(app, "Car scenario")
    defaults = {
        "Cab fare escalation (% / year)": 3.39,
        "Fuel price escalation (% / year)": 5.0,
        "Operating budget escalation (% / year)": 5.0,
    }
    for label, expected in defaults.items():
        assert element(app.number_input, label).value == expected
    assert any("Researched 11 October 2026" in item.value for item in app.caption)
    assert any("broad planning proxy" in item.value for item in app.caption)
    assert any("not an observed current annual rate" in item.value for item in app.caption)
    assert any("rounded planning proxy" in item.value for item in app.caption)
    sources = "\n".join(item.value for item in app.markdown)
    assert "pib.gov.in/PressReleasePage.aspx?PRID=2310058" in sources
    assert "sansad.in/getFile/annex/268/AU1729_OerGJQ.pdf" in sources
    assert "sansad.in/getFile/annex/270/AU2689_st3pq5.pdf" in sources

    overrides = {
        "Cab fare escalation (% / year)": 0.0,
        "Fuel price escalation (% / year)": 2.75,
        "Operating budget escalation (% / year)": 7.25,
    }
    for label, value in overrides.items():
        element(app.number_input, label).set_value(value)
    element(app.button, "Save car scenario").click().run()
    assert not app.exception
    saved = get_repository().load()
    assert saved.scenario.fare_escalation == Decimal(0)
    assert saved.scenario.fuel_escalation == Decimal("2.75")
    assert saved.scenario.operating_escalation == Decimal("7.25")

    reloaded = AppTest.from_file(str(APP), default_timeout=30).run()
    navigate(reloaded, "Car scenario")
    for label, expected in overrides.items():
        assert element(reloaded.number_input, label).value == expected


def test_apply_researched_escalation_defaults_saves_pending_car_inputs_and_requires_review(app):
    select_group(app, "office-commute")
    use_group_forecast(app)
    navigate(app, "Car scenario")
    values = {
        "Reference on-road price / dealer quote (₹)": "900000",
        "Expected city efficiency (km/l)": "10",
        "Expected outstation efficiency (km/l)": "16",
        "Petrol price (₹ / litre)": "105",
        "Monthly maintenance and repairs (₹)": "1500",
        "Monthly fixed parking (₹)": "1000",
        "End of year 5 resale value (₹)": "350000",
        **{f"Year {year} renewal (₹)": "15000" for year in range(2, 6)},
    }
    for label, value in values.items():
        element(app.text_input, label).set_value(value)
    element(app.checkbox, "My entered purchase quote already includes ACKO initial cover").check()
    for label in (
        "Cab fare escalation (% / year)",
        "Fuel price escalation (% / year)",
        "Operating budget escalation (% / year)",
    ):
        element(app.number_input, label).set_value(0.0)
    reviewed = "I have reviewed these ownership budgets, including any explicit zero costs"
    element(app.checkbox, reviewed).check()
    element(app.button, "Save car scenario").click().run()
    assert not app.exception
    assert get_repository().load().scenario.assumptions_confirmed

    # The default action submits the whole form, including edits not saved previously.
    pending = {
        "Scenario name": "Updated purchase and loan plan",
        "Reference on-road price / dealer quote (₹)": "975000",
        "Loan principal (₹), used for loan purchases": "600000",
        "Additional upfront costs not in quote (₹)": "25000",
        "Monthly maintenance and repairs (₹)": "1800",
    }
    for label, value in pending.items():
        element(app.text_input, label).set_value(value)
    element(app.radio, "How will you pay?").set_value("loan")
    element(app.number_input, "Annual loan interest (%)").set_value(9.0)
    element(app.number_input, "Loan tenure (months)").set_value(72)
    element(app.button, "Use researched escalation defaults").click().run()
    assert not app.exception
    saved = get_repository().load()
    assert saved.scenario.fare_escalation == Decimal("3.39")
    assert saved.scenario.fuel_escalation == saved.scenario.operating_escalation == Decimal(5)
    assert saved.scenario.name == "Updated purchase and loan plan"
    assert saved.scenario.on_road_price == Decimal(975000)
    assert saved.scenario.financing == "loan"
    assert saved.scenario.loan_principal == Decimal(600000)
    assert saved.scenario.annual_interest_rate == Decimal(9)
    assert saved.scenario.loan_months == 72
    assert saved.scenario.upfront_extras == Decimal(25000)
    assert saved.scenario.maintenance_monthly == Decimal(1800)
    assert not saved.scenario.assumptions_confirmed
    assert not element(app.checkbox, reviewed).value
    assert element(app.number_input, "Cab fare escalation (% / year)").value == 3.39
    assert element(app.number_input, "Fuel price escalation (% / year)").value == 5.0
    assert element(app.number_input, "Operating budget escalation (% / year)").value == 5.0
    assert any("4,00,000" in item.value for item in app.markdown)
    assert saved.cab_forecast.annual_inflation == Decimal("4.82")


def test_receipt_mode_hides_advanced_escalation_controls_and_keeps_shared_inflation(app):
    navigate(app, "Car scenario")
    assert app.session_state["workspace"].cab_forecast.source == "receipts"
    assert not any("escalation" in item.label.lower() for item in app.number_input)
    assert not any(item.label == "Use researched escalation defaults" for item in app.button)
    assert any("4.82% per year" in item.value for item in app.info)
    element(app.text_input, "Reference on-road price / dealer quote (₹)").set_value("880000")
    element(app.checkbox, "My entered purchase quote already includes ACKO initial cover").check()
    element(app.button, "Save car scenario").click().run()
    assert not app.exception
    assert get_repository().load().cab_forecast.annual_inflation == Decimal("4.82")
    navigate(app, "Travel patterns")
    assert element(app.text_input, "Annual India inflation assumption (%)").value == "4.82"
