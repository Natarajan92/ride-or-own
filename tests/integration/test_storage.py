import hashlib
import json
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter

from rideorown.domain.models import CoverageMonth, MonthlyEntry, Receipt, TravelGroup, Workspace
from rideorown.storage.backup import export_backup, restore_backup
from rideorown.storage.database import connect
from rideorown.storage.repositories import Repository


def original_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def make_workspace():
    data = original_pdf()
    group = TravelGroup(name="Office")
    receipt = Receipt(
        filename="../../private.pdf",
        file_hash=hashlib.sha256(data).hexdigest(),
        ride_id="RD123456789",
        selected_price=Decimal("123.45"),
        original={"selected_price": "120"},
        corrections={"selected_price": "123.45"},
        group_id=group.id,
    )
    return Workspace(receipts=[receipt], groups=[group]), data


def zip_backup(payload, members=None):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("workspace.json", json.dumps(payload))
        for name, data in (members or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def test_save_reload_preserves_decimal_corrections_and_state(tmp_path):
    repository = Repository(tmp_path)
    assert not repository.has_workspace()
    assert repository.load().receipts == []
    workspace, data = make_workspace()
    repository.put_pdf(workspace.receipts[0], data)
    repository.save(workspace)
    assert repository.has_workspace()
    loaded = Repository(tmp_path).load()
    assert loaded == workspace
    assert repository.get_pdf(loaded.receipts[0]) == data
    assert (
        list((tmp_path / "receipts").iterdir())[0].name == f"{workspace.receipts[0].file_hash}.pdf"
    )
    assert not (tmp_path.parent / "private.pdf").exists()


def test_full_zip_backup_roundtrip(tmp_path):
    source = Repository(tmp_path / "source")
    workspace, data = make_workspace()
    source.put_pdf(workspace.receipts[0], data)
    source.save(workspace)
    backup = export_backup(source, workspace)
    target = Repository(tmp_path / "target")
    restored = restore_backup(target, backup)
    assert restored == workspace
    assert target.load() == workspace
    assert target.get_pdf(restored.receipts[0]) == data


def test_office_bus_selection_and_saved_cab_fares_survive_reload_and_backup(tmp_path):
    source = Repository(tmp_path / "source")
    group = TravelGroup(
        name="Office",
        frequency="weekly",
        visits=Decimal(3),
        transport_mode="bus",
        bus_cost_per_visit=Decimal("200.50"),
        cab_cost_low=Decimal("725.50"),
        cab_cost_high=Decimal("900.75"),
        tip_per_visit=Decimal("25.25"),
    )
    workspace = Workspace(groups=[group])
    source.save(workspace)
    loaded = Repository(source.data_dir).load()
    assert loaded == workspace

    target = Repository(tmp_path / "target")
    restored = restore_backup(target, export_backup(source, loaded))
    assert restored == target.load() == workspace
    saved_group = restored.groups[0]
    assert saved_group.transport_mode == "bus"
    assert saved_group.fare_low == saved_group.fare_high == Decimal("200.50")
    saved_group.transport_mode = "cab"
    assert saved_group.fare_low == Decimal("750.75")
    assert saved_group.fare_high == Decimal("926.00")


def test_legacy_stored_and_backup_groups_default_to_cab(tmp_path):
    workspace = Workspace(
        groups=[TravelGroup(name="Office", cab_cost_low=Decimal(700), cab_cost_high=Decimal(900))]
    )
    payload = workspace.model_dump(mode="json")
    payload["groups"][0].pop("transport_mode")
    payload["groups"][0].pop("bus_cost_per_visit")
    repository = Repository(tmp_path / "legacy")
    with connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO workspace(id, payload) VALUES (1, ?)", (json.dumps(payload),)
        )
    loaded = repository.load()
    restored = restore_backup(Repository(tmp_path / "restored"), zip_backup(payload))
    assert loaded == restored == workspace
    for group in (loaded.groups[0], restored.groups[0]):
        assert group.transport_mode == "cab"
        assert group.bus_cost_per_visit == Decimal(200)
        assert group.fare_low == Decimal(700)
        assert group.fare_high == Decimal(900)


def test_missing_original_blocks_incomplete_backup(tmp_path):
    workspace, _ = make_workspace()
    with pytest.raises(ValueError, match="original PDF.*missing"):
        export_backup(Repository(tmp_path), workspace)


@pytest.mark.parametrize(
    "failure", ["hash", "path", "version", "reference", "duplicate", "cycle", "missing"]
)
def test_invalid_restore_leaves_existing_files_and_database_unchanged(tmp_path, failure):
    repository = Repository(tmp_path)
    existing = Workspace(groups=[TravelGroup(name="Existing")])
    repository.save(existing)
    workspace, data = make_workspace()
    payload = workspace.model_dump(mode="json")
    members = {f"receipts/{workspace.receipts[0].file_hash}.pdf": data}
    if failure == "hash":
        members[next(iter(members))] = data + b"corrupt"
    elif failure == "path":
        members["../escape.pdf"] = data
    elif failure == "version":
        payload["schema_version"] = 2
    elif failure == "reference":
        payload["receipts"][0]["group_id"] = "missing"
    elif failure == "duplicate":
        payload["groups"].append(payload["groups"][0])
    elif failure == "cycle":
        payload["groups"][0]["parent_id"] = payload["groups"][0]["id"]
    elif failure == "missing":
        members = {}
    with pytest.raises(ValueError):
        restore_backup(repository, zip_backup(payload, members))
    assert repository.load() == existing
    assert list(repository.pdf_dir.iterdir()) == []
    assert not (tmp_path.parent / "escape.pdf").exists()


def test_corrupt_pdf_with_matching_hash_is_rejected(tmp_path):
    invalid = b"%PDF-not really a PDF"
    receipt = Receipt(filename="bad.pdf", file_hash=hashlib.sha256(invalid).hexdigest())
    workspace = Workspace(receipts=[receipt])
    member = {f"receipts/{receipt.file_hash}.pdf": invalid}
    with pytest.raises(ValueError, match="corrupted or unreadable"):
        restore_backup(Repository(tmp_path), zip_backup(workspace.model_dump(mode="json"), member))


def test_renamed_identical_pdf_cannot_double_count(tmp_path):
    workspace, _ = make_workspace()
    duplicate = workspace.receipts[0].model_copy(deep=True)
    duplicate.id = "another-id"
    duplicate.filename = "renamed.pdf"
    duplicate.ride_id = None
    workspace.receipts.append(duplicate)
    with pytest.raises(ValueError, match="same PDF more than once"):
        Repository(tmp_path).save(workspace)


def test_hash_is_never_allowed_to_be_a_path(tmp_path):
    receipt = Receipt(filename="harmless.pdf", file_hash="../../escape")
    with pytest.raises(ValueError, match="invalid SHA-256"):
        Repository(tmp_path).get_pdf(receipt)


def test_invalid_references_and_coverage_rejected(tmp_path):
    repository = Repository(tmp_path)
    workspace = Workspace(
        monthly_entries=[MonthlyEntry(month="2026-09", amount=1, group_id="missing")]
    )
    with pytest.raises(ValueError, match="missing group"):
        repository.save(workspace)
    workspace = Workspace(coverage=[CoverageMonth(month="2026-09"), CoverageMonth(month="2026-09")])
    with pytest.raises(ValueError, match="same coverage month"):
        repository.save(workspace)


def test_failed_database_save_rolls_back_new_original_files(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    existing = Workspace()
    repository.save(existing)
    workspace, data = make_workspace()
    backup = zip_backup(
        workspace.model_dump(mode="json"), {f"receipts/{workspace.receipts[0].file_hash}.pdf": data}
    )

    def fail_save(_):
        raise OSError("Simulated disk failure")

    monkeypatch.setattr(repository, "save", fail_save)
    with pytest.raises(OSError, match="disk failure"):
        restore_backup(repository, backup)
    assert repository.load() == existing
    assert list(repository.pdf_dir.iterdir()) == []
