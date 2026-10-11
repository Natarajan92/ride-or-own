"""Validated ZIP backups containing JSON plus byte-for-byte original PDFs."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile
from zlib import error as ZlibError

from pydantic import ValidationError

from rideorown.domain.models import Workspace
from rideorown.storage.files import pdf_path, validate_payload
from rideorown.storage.repositories import Repository
from rideorown.storage.validation import validate_workspace

MAX_BACKUP_BYTES = 500 * 1024 * 1024
MAX_WORKSPACE_BYTES = 20 * 1024 * 1024
MAX_BACKUP_FILES = 10_001


def export_backup(repo: Repository, workspace: Workspace) -> bytes:
    workspace = validate_workspace(workspace)
    original_files: dict[str, bytes] = {}
    total_size = 0
    for receipt in workspace.receipts:
        data = repo.get_pdf(receipt)
        if data is None:
            raise ValueError(
                f"The original PDF for {receipt.filename} is missing; backup is incomplete."
            )
        total_size += len(data)
        original_files[f"receipts/{receipt.file_hash}.pdf"] = data
    payload = workspace.model_dump_json(indent=2).encode("utf-8")
    if len(payload) > MAX_WORKSPACE_BYTES or total_size + len(payload) > MAX_BACKUP_BYTES:
        raise ValueError("The backup exceeds the 500 MB decoded-data or 20 MB metadata limit.")
    if len(original_files) + 1 > MAX_BACKUP_FILES:
        raise ValueError("The backup contains too many files.")
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("workspace.json", payload)
        for name, data in original_files.items():
            archive.writestr(name, data)
    return output.getvalue()


def restore_backup(repo: Repository, data: bytes) -> Workspace:
    """Validate every member and relationship before touching files or SQLite."""
    if len(data) > MAX_BACKUP_BYTES:
        raise ValueError("The backup exceeds the 500 MB upload limit.")
    try:
        with ZipFile(BytesIO(data), "r") as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > MAX_BACKUP_FILES or len(set(names)) != len(names):
                raise ValueError("The backup has duplicate members or too many files.")
            if "workspace.json" not in names:
                raise ValueError("The backup must contain workspace.json.")
            if any(entry.flag_bits & 1 for entry in entries):
                raise ValueError("Encrypted backups are unsupported.")
            if sum(entry.file_size for entry in entries) > MAX_BACKUP_BYTES:
                raise ValueError("The backup exceeds the 500 MB decoded-data limit.")
            if archive.getinfo("workspace.json").file_size > MAX_WORKSPACE_BYTES:
                raise ValueError("The backup workspace metadata exceeds 20 MB.")
            workspace = validate_workspace(
                Workspace.model_validate_json(archive.read("workspace.json"))
            )
            required = {f"receipts/{receipt.file_hash}.pdf" for receipt in workspace.receipts}
            if set(names) != required | {"workspace.json"}:
                raise ValueError("The backup contains missing PDFs or unexpected paths/files.")
            originals = {}
            for receipt in workspace.receipts:
                member = f"receipts/{receipt.file_hash}.pdf"
                if archive.getinfo(member).file_size > 5 * 1024 * 1024:
                    raise ValueError("A backup receipt exceeds the 5 MB per-file limit.")
                original = archive.read(member)
                validate_payload(receipt.file_hash, original)
                originals[receipt.file_hash] = original
    except (
        BadZipFile,
        OSError,
        RuntimeError,
        ValidationError,
        KeyError,
        NotImplementedError,
        ZlibError,
    ) as exc:
        raise ValueError(
            "The backup is damaged or its workspace format/version is unsupported."
        ) from exc

    # Preflight existing content too, before the first write. Old unreferenced
    # PDFs remain available until users deliberately clear their local data.
    for receipt in workspace.receipts:
        repo.get_pdf(receipt)
    new_paths = [
        pdf_path(repo.pdf_dir, receipt.file_hash)
        for receipt in workspace.receipts
        if not pdf_path(repo.pdf_dir, receipt.file_hash).exists()
    ]
    try:
        for receipt in workspace.receipts:
            repo.put_pdf(receipt, originals[receipt.file_hash])
        repo.save(workspace)
    except Exception:
        # Files are immutable and content-addressed; remove only newly created
        # files if persistence fails, leaving the previous workspace intact.
        for path in new_paths:
            path.unlink(missing_ok=True)
        raise
    return workspace
