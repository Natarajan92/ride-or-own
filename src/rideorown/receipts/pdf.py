"""Bounded PDF text extraction with actionable per-file failures."""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 5


def extract_text(data: bytes) -> str:
    if not data:
        raise ValueError("The PDF is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("The PDF exceeds the 5 MB per-file limit.")
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("This file is not a readable PDF.")
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are unsupported. Upload an unlocked receipt.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError("Receipt PDFs must have at most five pages.")
        if not reader.pages:
            raise ValueError("The PDF has no pages.")
        # Rapido writes the pickup last in its PDF content stream. Layout mode
        # restores visual reading order, preventing pickup/drop inversion.
        text = "\n".join(
            (page.extract_text(extraction_mode="layout") or "") if page.get("/Contents") else ""
            for page in reader.pages
        )
    except (PdfReadError, OSError, KeyError, TypeError, IndexError, NotImplementedError) as exc:
        raise ValueError(
            "The PDF could not be read. Try downloading the original receipt again."
        ) from exc
    if not text.strip():
        raise ValueError(
            "No selectable text was found. Scanned/image PDFs need manual entry; OCR is not supported."
        )
    return text
