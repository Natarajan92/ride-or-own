import json
from datetime import date
from decimal import Decimal as D

import pytest
from test_imports import receipt_pdf
from test_storage import make_workspace, zip_backup

from rideorown.domain.models import CabForecastSettings, Workspace
from rideorown.services.application import import_receipts
from rideorown.storage.backup import export_backup, restore_backup
from rideorown.storage.database import connect
from rideorown.storage.repositories import Repository


@pytest.mark.parametrize("source", ["receipts", "groups"])
def test_forecast_settings_survive_saved_workspace_and_full_zip_backup(tmp_path, source):
    workspace, data = make_workspace()
    workspace.cab_forecast = CabForecastSettings(
        source=source,
        history_start=date(2026, 4, 1),
        coverage_confirmed=True,
        monthly_city_km=D("670.25"),
        monthly_highway_km=D("425.50"),
        retained_share=D("0.175"),
        annual_inflation=D("4.82"),
    )
    source_repo = Repository(tmp_path / "source")
    source_repo.put_pdf(workspace.receipts[0], data)
    source_repo.save(workspace)

    reloaded = Repository(source_repo.data_dir).load()
    assert reloaded == workspace
    assert reloaded.cab_forecast.monthly_city_km == D("670.25")
    assert reloaded.cab_forecast.retained_share == D("0.175")

    target_repo = Repository(tmp_path / "restored")
    restored = restore_backup(target_repo, export_backup(source_repo, reloaded))
    assert restored == target_repo.load() == workspace
    assert restored.cab_forecast == workspace.cab_forecast
    assert target_repo.get_pdf(restored.receipts[0]) == data


def test_legacy_saved_workspace_and_zip_without_forecast_settings_use_defaults(tmp_path):
    workspace, data = make_workspace()
    payload = workspace.model_dump(mode="json")
    payload.pop("cab_forecast")
    legacy_repo = Repository(tmp_path / "legacy")
    legacy_repo.put_pdf(workspace.receipts[0], data)
    with connect(legacy_repo.database_path) as connection:
        connection.execute(
            "INSERT INTO workspace(id, payload) VALUES (1, ?)", (json.dumps(payload),)
        )
    loaded = legacy_repo.load()
    members = {f"receipts/{workspace.receipts[0].file_hash}.pdf": data}
    restored = restore_backup(Repository(tmp_path / "restored"), zip_backup(payload, members))
    assert loaded == restored == workspace
    for settings in (loaded.cab_forecast, restored.cab_forecast):
        assert settings == CabForecastSettings()
        assert settings.source == "receipts"
        assert settings.annual_inflation == D("4.82")
        assert not settings.coverage_confirmed
        assert settings.monthly_city_km is None
        assert settings.monthly_highway_km is None


def test_successful_new_receipt_import_resets_persisted_coverage_confirmation(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace(cab_forecast=CabForecastSettings(coverage_confirmed=True))
    repo.save(workspace)
    result = import_receipts(repo, workspace, [("new.pdf", receipt_pdf())])
    assert result["success"] == 1
    assert not result["errors"]
    assert not workspace.cab_forecast.coverage_confirmed
    assert repo.load() == workspace
    assert not repo.load().cab_forecast.coverage_confirmed


def test_duplicate_only_import_preserves_persisted_coverage_confirmation(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace()
    original = receipt_pdf()
    import_receipts(repo, workspace, [("original.pdf", original)])
    workspace.cab_forecast.coverage_confirmed = True
    repo.save(workspace)
    result = import_receipts(
        repo,
        workspace,
        [
            ("renamed.pdf", original),
            ("rewritten.pdf", receipt_pdf(metadata="rewritten")),
        ],
    )
    assert result["success"] == 0
    assert result["duplicates"] == 2
    assert not result["errors"] and not result["conflicts"]
    assert workspace.cab_forecast.coverage_confirmed
    assert repo.load() == workspace
    assert len(workspace.receipts) == 1


@pytest.mark.parametrize(
    ("filename", "data"),
    [("broken.pdf", b"not a PDF"), ("empty.pdf", b""), ("wrong.txt", b"not a PDF")],
)
def test_failed_receipt_import_preserves_persisted_coverage_confirmation(tmp_path, filename, data):
    repo = Repository(tmp_path)
    workspace = Workspace(cab_forecast=CabForecastSettings(coverage_confirmed=True))
    repo.save(workspace)
    result = import_receipts(repo, workspace, [(filename, data)])
    assert result["success"] == 0
    assert len(result["errors"]) == 1
    assert workspace.cab_forecast.coverage_confirmed
    assert repo.load() == workspace
    assert not workspace.receipts


def test_conflicting_receipt_import_preserves_coverage_confirmation(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace()
    import_receipts(repo, workspace, [("original.pdf", receipt_pdf())])
    workspace.cab_forecast.coverage_confirmed = True
    repo.save(workspace)
    result = import_receipts(repo, workspace, [("conflict.pdf", receipt_pdf(price="150"))])
    assert result["success"] == result["duplicates"] == 0
    assert len(result["conflicts"]) == 1
    assert workspace.cab_forecast.coverage_confirmed
    assert repo.load() == workspace


def test_failed_import_save_restores_confirmation_and_preexisting_workspace(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    workspace = Workspace(cab_forecast=CabForecastSettings(coverage_confirmed=True))
    repo.save(workspace)

    def fail_save(_):
        raise OSError("Simulated disk failure")

    monkeypatch.setattr(repo, "save", fail_save)
    result = import_receipts(repo, workspace, [("new.pdf", receipt_pdf())])
    assert result["success"] == 0
    assert result["errors"][0]["error"] == "Simulated disk failure"
    assert workspace.cab_forecast.coverage_confirmed
    assert repo.load() == workspace
    assert workspace.receipts == []
    assert list(repo.pdf_dir.iterdir()) == []
