from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from rideorown.domain.models import Workspace
from rideorown.receipts.rapido import extract_receipt
from rideorown.services.application import import_receipts, initialize_workspace
from rideorown.storage.repositories import Repository


def receipt_pdf(ride_id="RD123456789", price="100", metadata="one"):
    """A selectable synthetic receipt; no private receipt data is embedded."""
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
        f"Ride ID {ride_id}",
        "Mode of Vehicle Car",
        "Time of Ride Sep 11th 2026, 1:24 PM",
        "Selected Price",
        f"INR {price}",
        "",
        "Pickup Road, Bengaluru",
        "",
        "Drop Road, Bengaluru",
        "",
        "This document is issued on request by the passenger.",
    ]
    commands = ["BT /F1 12 Tf 20 760 Td"]
    for line in lines:
        commands.append(f"({line}) Tj 0 -20 Td")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": metadata})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_sequential_import_retains_successes_around_failures_and_reports_progress(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace()
    seen = []
    result = import_receipts(
        repo,
        workspace,
        [
            ("one.pdf", receipt_pdf()),
            ("broken.pdf", b"not a PDF"),
            ("two.PDF", receipt_pdf(ride_id="RD987654321", price="200")),
        ],
        progress=lambda *args: seen.append(args),
    )
    assert result["processed"] == 3
    assert result["success"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["filename"] == "broken.pdf"
    assert [row[3] for row in seen] == ["Imported", "Needs attention", "Imported"]
    assert repo.load() == workspace
    assert sum(receipt.spending for receipt in workspace.receipts) == 300
    assert len(list(repo.pdf_dir.iterdir())) == 2


def test_hash_and_reencoded_ride_duplicates_cannot_double_count(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace()
    original = receipt_pdf()
    rewritten = receipt_pdf(metadata="rewritten")
    result = import_receipts(
        repo,
        workspace,
        [("first.pdf", original), ("renamed.pdf", original), ("rewritten.pdf", rewritten)],
    )
    assert result["success"] == 1
    assert result["duplicates"] == 2
    assert not result["conflicts"]
    assert len(workspace.receipts) == 1
    assert len(list(repo.pdf_dir.iterdir())) == 1


def test_corrected_values_and_ride_identity_preserve_original_deduplication(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace()
    import_receipts(repo, workspace, [("one.pdf", receipt_pdf())])
    receipt = workspace.receipts[0]
    receipt.selected_price = Decimal("150")
    receipt.pickup = "Corrected address"
    receipt.ride_id = "RD111111111"
    receipt.corrections = {"selected_price": "150", "ride_id": "RD111111111"}
    repo.save(workspace)
    result = import_receipts(
        repo, workspace, [("rewritten.pdf", receipt_pdf(metadata="rewritten"))]
    )
    assert result["duplicates"] == 1
    assert result["success"] == 0
    assert not result["conflicts"]
    assert len(repo.load().receipts) == 1
    assert repo.load().receipts[0].selected_price == 150


def test_conflicting_extraction_uses_original_values_in_report(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace()
    import_receipts(repo, workspace, [("one.pdf", receipt_pdf())])
    workspace.receipts[0].selected_price = Decimal("150")
    repo.save(workspace)
    result = import_receipts(repo, workspace, [("conflict.pdf", receipt_pdf(price="160"))])
    assert result["success"] == result["duplicates"] == 0
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["existing_price"] == "100"
    assert result["conflicts"][0]["imported_price"] == "160"
    assert len(workspace.receipts) == 1
    assert len(list(repo.pdf_dir.iterdir())) == 1


def test_write_failure_keeps_prior_success_and_cleans_only_new_original(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    workspace = Workspace()
    save = repo.save
    calls = 0

    def fail_second_save(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk failure")
        save(value)

    monkeypatch.setattr(repo, "save", fail_second_save)
    result = import_receipts(
        repo,
        workspace,
        [
            ("one.pdf", receipt_pdf()),
            ("two.pdf", receipt_pdf(ride_id="RD222222222")),
            ("three.pdf", receipt_pdf(ride_id="RD333333333")),
        ],
    )
    assert result["success"] == 2
    assert result["processed"] == 3
    assert result["errors"][0]["error"] == "disk failure"
    assert [receipt.ride_id for receipt in workspace.receipts] == ["RD123456789", "RD333333333"]
    assert repo.load() == workspace
    assert len(list(repo.pdf_dir.iterdir())) == 2


def test_failed_import_never_removes_preexisting_original(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    workspace = Workspace()
    data = receipt_pdf()
    existing = extract_receipt(data, "one.pdf")
    repo.put_pdf(existing, data)

    def fail_save(_):
        raise OSError("disk failure")

    monkeypatch.setattr(repo, "save", fail_save)
    result = import_receipts(repo, workspace, [("one.pdf", data)])
    assert result["success"] == 0
    assert workspace.receipts == []
    assert repo.get_pdf(existing) == data


@pytest.mark.parametrize("boundary", ["count", "combined"])
def test_rejected_batch_is_not_partially_imported(tmp_path, monkeypatch, boundary):
    repo = Repository(tmp_path)
    workspace = Workspace()
    if boundary == "count":
        files = [(f"{i}.pdf", b"unused") for i in range(501)]
    else:
        monkeypatch.setattr("rideorown.services.application.MAX_BATCH_BYTES", 10)
        files = [("one.pdf", b"sixsix"), ("two.pdf", b"sixsix")]
    result = import_receipts(repo, workspace, files)
    assert result["processed"] == result["success"] == 0
    assert result["errors"][0]["filename"] == "Batch"
    assert workspace.receipts == []
    assert not repo.has_workspace()
    assert list(repo.pdf_dir.iterdir()) == []


def test_per_file_boundary_allows_other_files(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    workspace = Workspace()
    data = receipt_pdf()
    monkeypatch.setattr("rideorown.services.application.MAX_FILE_BYTES", len(data))
    result = import_receipts(
        repo,
        workspace,
        [
            ("too-large.pdf", data + b"x"),
            ("wrong.txt", data),
            ("empty.pdf", b""),
            ("exact.pdf", data),
        ],
    )
    assert result["processed"] == 4
    assert result["success"] == 1
    assert len(result["errors"]) == 3
    assert len(repo.load().receipts) == 1


def test_broken_progress_observer_does_not_interrupt_persisted_imports(tmp_path):
    repo = Repository(tmp_path)
    workspace = Workspace()

    def broken_progress(*_):
        raise RuntimeError("display disconnected")

    result = import_receipts(
        repo,
        workspace,
        [("one.pdf", receipt_pdf()), ("two.pdf", receipt_pdf(ride_id="RD222222222"))],
        progress=broken_progress,
    )
    assert result["success"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["filename"] == "Progress"
    assert repo.load() == workspace


def test_deleted_default_groups_are_not_recreated_on_restart(tmp_path):
    repo = Repository(tmp_path)
    workspace = initialize_workspace(repo)
    assert workspace.groups == []
    from tests.example_groups import example_groups

    workspace.groups = example_groups(10)
    repo.save(workspace)
    assert len(initialize_workspace(repo).groups) == 3
    workspace.groups = []
    repo.save(workspace)
    assert initialize_workspace(repo).groups == []
