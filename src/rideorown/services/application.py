"""Local application lifecycle and idempotent receipt imports."""

from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256

from rideorown.config import (
    MAX_BATCH_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES_PER_BATCH,
    current_month,
    data_directory,
)
from rideorown.domain.models import Receipt, Workspace
from rideorown.receipts.rapido import extract_receipt
from rideorown.storage.files import pdf_path
from rideorown.storage.repositories import Repository


def get_repository() -> Repository:
    return Repository(data_directory())


def initialize_workspace(repo: Repository) -> Workspace:
    if repo.has_workspace():
        return repo.load()
    start = current_month()
    workspace = Workspace(forecast_start=start)
    repo.save(workspace)
    return workspace


def save_workspace(repo: Repository, workspace: Workspace) -> None:
    repo.save(Workspace.model_validate(workspace.model_dump()))


def _ride_identifiers(receipt: Receipt) -> set[str]:
    # Corrections do not erase the original identity of an uploaded document.
    return {
        value
        for value in (receipt.ride_id, receipt.original.get("ride_id"))
        if isinstance(value, str) and value
    }


def _extracted_values(receipt: Receipt) -> dict:
    """Compare like-for-like extraction evidence, regardless of later edits."""
    current = receipt.model_dump(mode="json")
    fields = ("selected_price", "ride_at", "mode", "pickup", "drop")
    values = {key: receipt.original.get(key, current[key]) for key in fields}
    if values["selected_price"] is not None:
        # Equivalent decimal representations in older backups are equal.
        values["selected_price"] = Decimal(str(values["selected_price"]))
    return values


def import_receipts(
    repo: Repository,
    workspace: Workspace,
    files: list[tuple[str, bytes]],
    progress: Callable | None = None,
) -> dict:
    result = {
        "processed": 0,
        "success": 0,
        "duplicates": 0,
        "conflicts": [],
        "errors": [],
        "imported_ids": [],
    }
    if len(files) > MAX_FILES_PER_BATCH or sum(len(data) for _, data in files) > MAX_BATCH_BYTES:
        result["errors"].append(
            {
                "filename": "Batch",
                "error": "Accepted batches are limited to 500 PDFs and 50 MB combined. Import smaller batches; existing receipts are retained.",
            }
        )
        return result
    hashes = {receipt.file_hash for receipt in workspace.receipts}
    ride_ids: dict[str, list[Receipt]] = {}
    for existing in workspace.receipts:
        for ride_id in _ride_identifiers(existing):
            ride_ids.setdefault(ride_id, []).append(existing)
    report_progress = progress
    for index, (filename, data) in enumerate(files, start=1):
        status = "Imported"
        try:
            if not filename.lower().endswith(".pdf"):
                raise ValueError("Select PDF files only.")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("PDF exceeds the 5 MB per-file limit.")
            if not data:
                raise ValueError("The file is empty.")
            digest = sha256(data).hexdigest()
            if digest in hashes:
                result["duplicates"] += 1
                status = "Duplicate"
            else:
                receipt = extract_receipt(data, filename)
                matches = ride_ids.get(receipt.ride_id, []) if receipt.ride_id else []
                if matches:
                    previous = matches[0]
                    if len(matches) == 1 and _extracted_values(previous) == _extracted_values(
                        receipt
                    ):
                        result["duplicates"] += 1
                        status = "Duplicate ride"
                    else:
                        result["conflicts"].append(
                            {
                                "filename": filename,
                                "ride_id": receipt.ride_id,
                                "message": "This ride ID already exists with different extracted values or an ambiguous corrected identity. Existing data was preserved; review it before importing a replacement.",
                                "existing_price": str(
                                    _extracted_values(previous)["selected_price"]
                                ),
                                "imported_price": str(_extracted_values(receipt)["selected_price"]),
                            }
                        )
                        status = "Conflicting ride"
                else:
                    original_exists = repo.get_pdf(receipt) is not None
                    try:
                        repo.put_pdf(receipt, data)
                        workspace.receipts.append(receipt)
                        was_confirmed = workspace.cab_forecast.coverage_confirmed
                        workspace.cab_forecast.coverage_confirmed = False
                        try:
                            save_workspace(repo, workspace)
                        except Exception:
                            workspace.receipts.pop()
                            workspace.cab_forecast.coverage_confirmed = was_confirmed
                            raise
                    except Exception:
                        if not original_exists:
                            # A failed transaction must not leave a new orphan
                            # original. Existing immutable PDFs are preserved.
                            try:
                                pdf_path(repo.pdf_dir, receipt.file_hash).unlink(missing_ok=True)
                            except OSError:
                                pass  # Preserve the primary write/save error.
                        raise
                    hashes.add(receipt.file_hash)
                    for ride_id in _ride_identifiers(receipt):
                        ride_ids.setdefault(ride_id, []).append(receipt)
                    result["success"] += 1
                    result["imported_ids"].append(receipt.id)
        except Exception as exc:
            status = "Needs attention"
            result["errors"].append({"filename": filename, "error": str(exc)})
        result["processed"] += 1
        if report_progress:
            try:
                report_progress(index, len(files), filename, status)
            except Exception as exc:
                result["errors"].append(
                    {
                        "filename": "Progress",
                        "error": f"Progress display failed; imports continued: {exc}",
                    }
                )
                report_progress = None
    return result
