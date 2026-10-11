"""Actions acknowledge their actual outcome, including after a Streamlit rerun."""

import json
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from sqlite3 import OperationalError

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from streamlit.testing.v1 import AppTest, element_tree

from rideorown.domain.models import OwnershipScenario, Receipt
from rideorown.services.application import get_repository
from rideorown.services.backup import export_backup
from rideorown.services.catalog import get_car_presets
from rideorown.storage.repositories import Repository
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


def notifications(app):
    """Read the accessible message from each fixed action popup."""

    class NotificationParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.hidden_depth = 0
            self.messages = []
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if self.depth:
                self.depth += 1
                if self.hidden_depth:
                    self.hidden_depth += 1
                elif dict(attrs).get("aria-hidden") == "true":
                    self.hidden_depth = 1
            elif dict(attrs).get("role") in {"status", "alert"}:
                self.depth = 1
                self.parts = []

        def handle_endtag(self, tag):
            if self.depth:
                if self.hidden_depth:
                    self.hidden_depth -= 1
                self.depth -= 1
                if not self.depth:
                    self.messages.append("".join(self.parts).strip())

        def handle_data(self, data):
            if self.depth and not self.hidden_depth:
                self.parts.append(data)

    messages = []
    for item in app.markdown:
        if 'class="action-notification ' in item.value:
            assert 'aria-live="' in item.value
            assert 'aria-label="Dismiss notification"' in item.value
            parser = NotificationParser()
            parser.feed(item.value)
            messages.extend(parser.messages)
    return messages


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("RIDEOROWN_DATA_DIR", str(tmp_path / "local-data"))
    # These action tests start with explicitly saved journeys, not fresh defaults.
    from rideorown.domain.models import Workspace

    get_repository().save(Workspace(groups=example_groups(10)))
    original_states = element_tree.ElementTree.get_widget_states

    def with_dataframe_states(tree):
        states = original_states(tree)
        # AppTest does not yet send table selections or data-editor changes.
        for node in tree:
            if isinstance(node, element_tree.Dataframe) and node.key is not None:
                if node.proto.selection_mode or node.proto.editing_mode:
                    state = states.widgets.add()
                    state.id = node.proto.id
                    state.string_value = json.dumps(tree.session_state[node.key])
        return states

    monkeypatch.setattr(element_tree.ElementTree, "get_widget_states", with_dataframe_states)
    tested = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not tested.exception
    return tested


def navigate(app, step):
    app.sidebar.radio[0].set_value(SCREENS[step - 1]).run()
    assert not app.exception
    # Clear the acknowledgement from navigating before testing another action.
    app.run()


def select_group(app, group_id="office-commute"):
    navigate(app, 2)
    groups = app.session_state["workspace"].groups
    row = next(index for index, group in enumerate(groups) if group.id == group_id)
    app.session_state["travel_groups_table"] = {"selection": {"rows": [row]}}
    app.run()
    assert not app.exception


def group_forecast(app):
    navigate(app, 3)
    element(app.radio, "Build my forecast from").set_value("groups").run()
    assert not app.exception
    app.run()


def seed_receipts(app):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    pdf = output.getvalue()
    receipt = Receipt(
        filename="receipt.pdf",
        file_hash=sha256(pdf).hexdigest(),
        ride_id="RD123456789",
        ride_at=datetime(2026, 9, 11),
        selected_price=Decimal(100),
    )
    workspace = app.session_state["workspace"]
    workspace.receipts = [receipt]
    get_repository().put_pdf(receipt, pdf)
    get_repository().save(workspace)
    app.run()
    return receipt


def ready_comparison(app):
    workspace = app.session_state["workspace"]
    workspace.cab_forecast.source = "groups"
    workspace.groups = workspace.groups[:1]
    workspace.scenario = OwnershipScenario(
        on_road_price=Decimal(500000),
        price_includes_acko=True,
        city_efficiency=Decimal(10),
        highway_efficiency=Decimal(20),
        petrol_price=Decimal(100),
        maintenance_monthly=Decimal(1000),
        parking_monthly=Decimal(500),
        resale_value=Decimal(100000),
        insurance_renewals=[Decimal(10000)] * 4,
        assumptions_confirmed=True,
    )
    get_repository().save(workspace)
    navigate(app, 5)


def assert_feedback(app, text, *, level="success", once=True):
    assert not app.exception, [exception.message for exception in app.exception]
    messages = [item.value for item in app.get(level) if text.lower() in item.value.lower()]
    popups = notifications(app)
    assert messages, f"No {level} acknowledgement containing {text!r}"
    assert any(message in popups for message in messages), popups
    if once:
        app.run()
        assert not app.exception
        assert not any(text.lower() in message.lower() for message in notifications(app))
        assert not any(text.lower() in item.value.lower() for item in app.get(level))


def prepare_action(app, action):
    """Prepare a real form/button mutation, leaving its final click to the test."""
    if action in {"group", "create_group", "overrides", "remove"}:
        select_group(app)
        if action == "create_group":
            element(app.button, "New travel group").click().run()
            app.run()
            element(app.text_input, "Group name").set_value("Airport travel")
            element(app.text_input, "Distance per complete visit / round trip (km)").set_value("80")
            element(app.text_input, "Cab fare per complete visit — lower (₹)").set_value("1000")
            element(app.text_input, "Cab fare per complete visit — upper (₹)").set_value("1500")
            return element(app.button, "Save travel group"), "travel group"
        if action == "group":
            element(app.text_input, "Group name").set_value("Updated office rides")
            return element(app.button, "Save travel group"), "travel group"
        if action == "overrides":
            app.session_state["overrides_office-commute"] = {
                "edited_rows": {},
                "added_rows": [{"Month (YYYY-MM)": "2026-11", "Visits": 9}],
                "deleted_rows": [],
            }
            return element(app.button, "Save monthly overrides"), "monthly attendance"
        element(app.checkbox, "Remove Office commute and its future pattern").check().run()
        return element(app.button, "Remove travel group"), "removed"
    if action in {"forecast", "forecast_start"}:
        navigate(app, 3)
        if action == "forecast":
            element(app.text_input, "Annual India inflation assumption (%)").set_value("6.25")
            return element(app.button, "Save cab forecast"), "forecast saved"
        group_forecast(app)
        element(app.date_input, "Start the five-year forecast in").set_value(
            datetime(2026, 11, 1).date()
        )
        return element(app.button, "Save forecast start"), "forecast start"
    if action in {"car", "custom_car", "reference", "defaults"}:
        if action == "defaults":
            group_forecast(app)
        navigate(app, 4)
        if action == "car":
            element(app.text_input, "Reference on-road price / dealer quote (₹)").set_value(
                "900000"
            )
            return element(app.button, "Save car scenario"), "car"
        if action == "custom_car":
            return element(app.button, "Use a custom car"), "custom car"
        if action == "reference":
            element(app.selectbox, "Car reference").set_value(get_car_presets()[0]["id"]).run()
            return element(app.button, "Use this car reference"), "car reference"
        element(app.number_input, "Fuel price escalation (% / year)").set_value(0.0)
        return element(app.button, "Use researched escalation defaults"), "researched rates"
    if action in {"receipt", "assignment"}:
        receipt = seed_receipts(app)
        if action == "receipt":
            element(app.text_input, "Final payment you made (₹, total paid)").set_value("120")
            return element(app.button, "Save receipt corrections"), "receipt corrections"
        element(app.multiselect, "Receipts to assign").set_value([receipt.id])
        element(app.selectbox, "Travel group").set_value("office-commute").run()
        return element(app.button, "Assign selected receipts"), "assigned"
    if action == "notes":
        ready_comparison(app)
        element(app.text_area, "Comfort and convenience").set_value("Door-to-door convenience")
        return element(app.button, "Save personal considerations"), "personal considerations"
    raise AssertionError(action)


MUTATIONS = [
    "group",
    "create_group",
    "overrides",
    "remove",
    "forecast",
    "forecast_start",
    "car",
    "custom_car",
    "reference",
    "defaults",
    "receipt",
    "assignment",
    "notes",
]


@pytest.mark.parametrize("action", MUTATIONS)
def test_successful_actions_show_a_one_time_confirmation_after_rerun(app, action):
    button, acknowledgement = prepare_action(app, action)
    button.click().run()
    assert_feedback(app, acknowledgement, level="warning" if action == "defaults" else "success")
    workspace = get_repository().load()
    if action == "group":
        assert workspace.groups[0].name == "Updated office rides"
    elif action == "create_group":
        assert len(workspace.groups) == 4
        assert workspace.groups[-1].name == "Airport travel"
    elif action == "overrides":
        assert workspace.groups[0].monthly_overrides == {"2026-11": Decimal(9)}
    elif action == "remove":
        assert "office-commute" not in [group.id for group in workspace.groups]
    elif action == "forecast":
        assert workspace.cab_forecast.annual_inflation == Decimal("6.25")
    elif action == "forecast_start":
        assert workspace.forecast_start == datetime(2026, 11, 1).date()
    elif action == "car":
        assert workspace.scenario.on_road_price == Decimal(900000)
    elif action == "reference":
        assert workspace.scenario.preset_id == get_car_presets()[0]["id"]
    elif action == "custom_car":
        assert workspace.scenario.preset_id is None
        assert workspace.scenario.on_road_price is None
    elif action == "defaults":
        assert workspace.scenario.fuel_escalation == Decimal(5)
        assert not workspace.scenario.assumptions_confirmed
    elif action == "receipt":
        assert workspace.receipts[0].spending == Decimal(120)
    elif action == "assignment":
        assert workspace.receipts[0].group_id == "office-commute"
    elif action == "notes":
        assert workspace.personal_considerations["comfort"] == "Door-to-door convenience"


@pytest.mark.parametrize("action", MUTATIONS)
def test_failed_persistence_never_shows_success_or_keeps_unsaved_changes(app, action, monkeypatch):
    button, _ = prepare_action(app, action)
    original = get_repository().load().model_dump_json()

    def fail_save(self, workspace):
        raise OSError("Test disk unavailable")

    monkeypatch.setattr(Repository, "save", fail_save)
    button.click().run()
    assert_feedback(app, "could not be saved", level="error", once=False)
    assert not any(
        "saved" in message.lower()
        for message in notifications(app)
        if "could not" not in message.lower()
    )
    assert get_repository().load().model_dump_json() == original
    assert app.session_state["workspace"].model_dump_json() == original


def test_sqlite_save_failure_shows_error_with_no_saved_confirmation(app, monkeypatch):
    button, _ = prepare_action(app, "group")

    def fail_save(self, workspace):
        raise OperationalError("Test database locked")

    monkeypatch.setattr(Repository, "save", fail_save)
    button.click().run()
    assert_feedback(app, "could not be saved", level="error", once=False)
    assert not app.success


@pytest.mark.parametrize("action", ["group", "overrides", "forecast", "car", "receipt"])
def test_invalid_form_values_show_errors_and_preserve_saved_data(app, action):
    button, _ = prepare_action(app, action)
    original = get_repository().load().model_dump_json()
    if action == "group":
        element(app.text_input, "Group name").set_value("")
    elif action == "overrides":
        app.session_state["overrides_office-commute"]["added_rows"][0]["Month (YYYY-MM)"] = (
            "2026-13"
        )
    elif action == "forecast":
        element(app.text_input, "Annual India inflation assumption (%)").set_value("-51")
    elif action == "car":
        element(app.text_input, "Expected city efficiency (km/l)").set_value("0")
    else:
        element(app.text_input, "Final payment you made (₹, total paid)").set_value("-1")
    button.click().run()
    assert not app.exception
    assert app.error
    assert notifications(app)
    assert not app.success
    assert get_repository().load().model_dump_json() == original


def test_new_group_and_forecast_source_acknowledge_the_action_once(app):
    select_group(app)
    element(app.button, "New travel group").click().run()
    assert element(app.text_input, "Group name").value == ""
    assert_feedback(app, "new travel group", level="info")
    navigate(app, 3)
    element(app.radio, "Build my forecast from").set_value("groups").run()
    assert get_repository().load().cab_forecast.source == "groups"
    assert_feedback(app, "forecast", level="success")


def test_forward_back_and_sidebar_navigation_acknowledge_destination(app):
    element(app.button, "Continue to Advanced travel settings").click().run()
    assert app.sidebar.radio[0].value == SCREENS[1]
    assert_feedback(app, "Advanced travel settings opened", level="info")
    element(app.button, "Back to Cab receipts").click().run()
    assert app.sidebar.radio[0].value == SCREENS[0]
    assert_feedback(app, "Cab receipts opened", level="info")
    app.sidebar.radio[0].set_value(SCREENS[3]).run()
    assert_feedback(app, "New car plan opened", level="info")


def test_reload_and_backup_preparation_have_one_time_confirmations(app):
    element(app.sidebar.button, "Reload saved workspace").click().run()
    assert_feedback(app, "reloaded")
    element(app.sidebar.button, "Prepare workspace backup").click().run()
    assert "prepared_backup" in app.session_state
    assert_feedback(app, "backup")
    assert element(app.get("download_button"), "Download workspace backup")


def test_backup_preparation_failure_removes_previous_download_and_shows_error(app, monkeypatch):
    from rideorown.ui import navigation

    element(app.sidebar.button, "Prepare workspace backup").click().run()
    app.run()

    def fail_export(*args):
        raise OSError("Test backup failure")

    monkeypatch.setattr(navigation, "export_backup", fail_export)
    element(app.sidebar.button, "Prepare workspace backup").click().run()
    assert_feedback(app, "could not be prepared", level="error", once=False)
    assert "prepared_backup" not in app.session_state
    assert not any(item.label == "Download workspace backup" for item in app.get("download_button"))
    assert not app.success


def test_restore_confirms_after_rerun_and_replaces_stale_form_values(app):
    saved = get_repository().load()
    saved.scenario.name = "Restored car plan"
    saved.scenario.insurance_renewals[0] = Decimal(12345)
    get_repository().save(saved)
    backup = export_backup(get_repository(), saved)
    navigate(app, 4)
    app.session_state["renewal_None_2"] = "99999"
    app.run()
    element(app.file_uploader, "Restore a backup").upload(
        "backup.zip", backup, "application/zip"
    ).run()
    element(app.checkbox, "Replace my current workspace with this backup").check().run()
    element(app.sidebar.button, "Restore workspace").click().run()
    assert element(app.text_input, "Year 2 renewal (₹)").value == "12,345"
    assert_feedback(app, "restored")
    assert get_repository().load().scenario.name == "Restored car plan"


def test_invalid_restore_shows_error_and_preserves_workspace(app):
    original = get_repository().load().model_dump_json()
    element(app.file_uploader, "Restore a backup").upload(
        "broken.zip", b"broken", "application/zip"
    ).run()
    element(app.checkbox, "Replace my current workspace with this backup").check().run()
    element(app.sidebar.button, "Restore workspace").click().run()
    assert_feedback(app, "could not be restored", level="error", once=False)
    assert not app.success
    assert get_repository().load().model_dump_json() == original


@pytest.mark.parametrize("download", ["original", "forecast", "backup", "report", "monthly"])
def test_downloads_confirm_request_once_without_claiming_completed_transfer(app, download):
    if download == "original":
        seed_receipts(app)
        label = "Download original PDF"
    elif download == "forecast":
        group_forecast(app)
        element(app.radio, "Projection view").set_value("Travel group").run()
        label = "Download projection CSV"
    elif download == "backup":
        element(app.sidebar.button, "Prepare workspace backup").click().run()
        app.run()
        label = "Download workspace backup"
    else:
        ready_comparison(app)
        label = (
            "Download printable comparison"
            if download == "report"
            else "Download monthly cash-flow CSV"
        )
    element(app.get("download_button"), label).click().run()
    assert_feedback(app, "Download requested", level="info")


def receipt_pdf(*, price="100", metadata="one"):
    """Selectable synthetic receipt for upload tests, containing no personal data."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    lines = [
        "Booking History",
        "Ride ID RD123456789",
        "Mode of Vehicle Car",
        "Time of Ride Sep 11th 2026, 1:24 PM",
        "Selected Price",
        f"INR {price}",
    ]
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 12 Tf 20 760 Td\n"
            + "\n".join(f"({line}) Tj 0 -20 Td" for line in lines)
            + "\nET"
        ).encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": metadata})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def upload_and_import(app, name, pdf):
    element(app.file_uploader, "PDF receipts").set_value((name, pdf, "application/pdf")).run()
    element(app.button, "Import receipts").click().run()
    assert not app.exception


def test_receipt_import_duplicate_and_conflict_report_correct_outcomes(app):
    pdf = receipt_pdf()
    upload_and_import(app, "one.pdf", pdf)
    assert_feedback(app, "imported")
    assert len(get_repository().load().receipts) == 1
    upload_and_import(app, "renamed.pdf", pdf)
    assert not app.success
    assert_feedback(app, "duplicate", level="info")
    upload_and_import(app, "conflict.pdf", receipt_pdf(price="200", metadata="conflict"))
    assert not app.success
    assert_feedback(app, "No receipts imported", level="warning")
    assert any("Conflicting ride IDs" in item.value for item in app.warning)
    assert get_repository().load().receipts[0].spending == Decimal(100)


def test_all_failed_imports_show_error_and_clear_previous_success(app):
    upload_and_import(app, "one.pdf", receipt_pdf())
    app.run()
    upload_and_import(app, "broken.pdf", b"not a PDF")
    assert not app.success
    assert_feedback(app, "No receipts imported", level="error")
    assert len(get_repository().load().receipts) == 1
    assert app.session_state["last_import"]["success"] == 0


def test_partial_import_preserves_success_and_warns_about_failed_receipts(app):
    element(app.file_uploader, "PDF receipts").set_value(
        [
            ("one.pdf", receipt_pdf(), "application/pdf"),
            ("broken.pdf", b"broken", "application/pdf"),
        ]
    ).run()
    element(app.button, "Import receipts").click().run()
    assert_feedback(app, "receipts imported", level="warning")
    assert not app.success
    assert len(get_repository().load().receipts) == 1
    assert len(app.session_state["last_import"]["errors"]) == 1


def test_import_exception_clears_previous_summary_and_shows_no_false_success(app, monkeypatch):
    from rideorown.ui.screens import capture

    upload_and_import(app, "one.pdf", receipt_pdf())
    app.run()

    def fail_import(*args, **kwargs):
        raise OSError("Test import failure")

    monkeypatch.setattr(capture, "import_receipts", fail_import)
    upload_and_import(app, "another.pdf", receipt_pdf(metadata="another"))
    assert_feedback(app, "could not be imported", level="error", once=False)
    assert "last_import" not in app.session_state
    assert not app.success
    assert len(get_repository().load().receipts) == 1


def test_batch_limit_explains_error_without_importing_or_reusing_previous_report(app):
    upload_and_import(app, "one.pdf", receipt_pdf())
    app.run()
    files = [(f"receipt-{index}.pdf", b"pdf", "application/pdf") for index in range(501)]
    element(app.file_uploader, "PDF receipts").set_value(files).run()
    element(app.button, "Import receipts").click().run()
    assert_feedback(app, "500 PDFs", level="error", once=False)
    assert "last_import" not in app.session_state
    assert not app.success
    assert len(get_repository().load().receipts) == 1


def test_duplicate_ride_id_correction_shows_error_and_preserves_original_receipts(app):
    receipt = seed_receipts(app)
    workspace = app.session_state["workspace"]
    workspace.receipts.append(
        Receipt(
            filename="second.pdf",
            file_hash="2" * 64,
            ride_id="RD987654321",
            selected_price=Decimal(200),
        )
    )
    get_repository().save(workspace)
    app.run()
    element(app.selectbox, "Receipt to correct").set_value(receipt.id).run()
    original = get_repository().load().model_dump_json()
    element(app.text_input, "Ride ID").set_value("RD987654321")
    element(app.button, "Save receipt corrections").click().run()
    assert_feedback(app, "already belongs", level="error", once=False)
    assert not app.success
    assert get_repository().load().model_dump_json() == original


def test_disabled_actions_explain_prerequisites_without_claiming_success(app):
    assert element(app.button, "Import receipts").disabled
    assert element(app.sidebar.button, "Restore workspace").disabled
    select_group(app)
    assert element(app.button, "Remove travel group").disabled
    assert not notifications(app)
    assert not app.success


def test_new_travel_group_again_resets_the_unsaved_draft(app):
    select_group(app)
    element(app.button, "New travel group").click().run()
    element(app.selectbox, "Travel mode").set_value("bus").run()
    draft_name = element(app.text_input, "Group name")
    draft_name.set_value("Unfinished travel draft")
    assert draft_name.value == "Unfinished travel draft"
    element(app.button, "New travel group").click().run()
    assert element(app.text_input, "Group name").value == ""
    assert element(app.selectbox, "Travel mode").value == "cab"
    assert len(get_repository().load().groups) == 3
    assert_feedback(app, "New travel group", level="info")


def test_forecast_source_save_failure_reports_error_and_keeps_saved_source(app, monkeypatch):
    navigate(app, 3)

    def fail_save(self, workspace):
        raise OSError("Test source change failure")

    with monkeypatch.context() as failing_storage:
        failing_storage.setattr(Repository, "save", fail_save)
        element(app.radio, "Build my forecast from").set_value("groups").run()
        assert_feedback(app, "could not be saved", level="error", once=False)
        assert not app.success
        assert get_repository().load().cab_forecast.source == "receipts"
        assert app.session_state["workspace"].cab_forecast.source == "receipts"

    # Recovery on a later unrelated rerun must not retry and save the failed selection.
    app.run()
    assert not app.exception
    assert get_repository().load().cab_forecast.source == "receipts"
    assert app.session_state["workspace"].cab_forecast.source == "receipts"
    assert element(app.radio, "Build my forecast from").value == "receipts"
    assert not notifications(app)


def test_consecutive_saves_acknowledge_each_action_and_display_the_latest_result(app):
    select_group(app)
    for name in ["First saved group", "Second saved group"]:
        element(app.text_input, "Group name").set_value(name)
        element(app.button, "Save travel group").click().run()
        assert_feedback(app, name, once=False)
        assert notifications(app) == [f'Travel group "{name}" saved.']
        assert get_repository().load().groups[0].name == name
    app.run()
    assert not notifications(app)


def test_save_and_recovery_load_failure_still_show_the_action_error(app, monkeypatch):
    button, _ = prepare_action(app, "group")
    original = get_repository().load().model_dump_json()
    app.session_state["prepared_backup"] = b"stale backup"

    def fail_save(self, workspace):
        raise OSError("Test save failure")

    def fail_load(self):
        raise OSError("Test recovery failure")

    monkeypatch.setattr(Repository, "save", fail_save)
    monkeypatch.setattr(Repository, "load", fail_load)
    button.click().run()
    assert_feedback(app, "could not be saved", level="error", once=False)
    assert not app.success
    assert "prepared_backup" not in app.session_state
    assert app.session_state["workspace"].model_dump_json() == original


def test_reload_failure_has_visible_error_without_success(app, monkeypatch):
    original = app.session_state["workspace"].model_dump_json()

    def fail_load(self):
        raise OSError("Test load failure")

    monkeypatch.setattr(Repository, "load", fail_load)
    element(app.sidebar.button, "Reload saved workspace").click().run()
    assert_feedback(app, "could not be reloaded", level="error")
    assert not app.success
    assert app.session_state["workspace"].model_dump_json() == original
