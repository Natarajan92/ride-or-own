"""Persistence boundary used by Streamlit and import/backup services."""

from pathlib import Path

from pydantic import ValidationError

from rideorown.domain.models import Receipt, Workspace
from rideorown.storage.database import connect, initialize
from rideorown.storage.files import pdf_path, validate_payload, write_pdf
from rideorown.storage.validation import validate_workspace


class Repository:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir = self.data_dir / "receipts"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "rideorown.sqlite3"
        initialize(self.database_path)

    def has_workspace(self) -> bool:
        with connect(self.database_path) as connection:
            return connection.execute("SELECT 1 FROM workspace WHERE id = 1").fetchone() is not None

    def load(self) -> Workspace:
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT payload FROM workspace WHERE id = 1").fetchone()
        if row is None:
            return Workspace()
        try:
            return validate_workspace(Workspace.model_validate_json(row[0]))
        except (ValidationError, ValueError) as exc:
            raise ValueError(
                "Stored data is invalid or uses an unsupported version. Restore a valid backup."
            ) from exc

    def save(self, workspace: Workspace) -> None:
        validated = validate_workspace(workspace)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO workspace(id, payload) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (validated.model_dump_json(),),
            )

    def put_pdf(self, receipt: Receipt, data: bytes) -> None:
        write_pdf(self.pdf_dir, receipt.file_hash, data)

    def get_pdf(self, receipt: Receipt) -> bytes | None:
        path = pdf_path(self.pdf_dir, receipt.file_hash)
        if not path.exists():
            return None
        data = path.read_bytes()
        validate_payload(receipt.file_hash, data)
        return data
