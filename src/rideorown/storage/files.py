"""Content-addressed original PDFs; user filenames never become filesystem paths."""

import hashlib
import os
import tempfile
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from rideorown.receipts.pdf import MAX_PDF_BYTES
from rideorown.storage.validation import validate_hash


def pdf_path(directory: Path, file_hash: str) -> Path:
    return directory / f"{validate_hash(file_hash)}.pdf"


def validate_payload(file_hash: str, data: bytes) -> None:
    validate_hash(file_hash)
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("A backup receipt exceeds the 5 MB per-file limit.")
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("A receipt's original file is not a PDF.")
    if hashlib.sha256(data).hexdigest() != file_hash:
        raise ValueError("A receipt PDF is corrupted: its SHA-256 hash does not match.")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 5:
            raise ValueError("A receipt original must be an unlocked PDF with one to five pages.")
    except (PdfReadError, OSError, KeyError, TypeError, IndexError, NotImplementedError) as exc:
        raise ValueError("A receipt PDF is corrupted or unreadable.") from exc


def write_pdf(directory: Path, file_hash: str, data: bytes) -> None:
    validate_payload(file_hash, data)
    destination = pdf_path(directory, file_hash)
    if destination.exists():
        validate_payload(file_hash, destination.read_bytes())
        return
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
