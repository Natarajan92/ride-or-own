"""Runtime paths and conservative accepted-import limits."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_FILES_PER_BATCH = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_BATCH_BYTES = 50 * 1024 * 1024


def data_directory() -> Path:
    return Path(os.environ.get("RIDEOROWN_DATA_DIR", str(Path.cwd() / "data"))).resolve()


def current_month():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date().replace(day=1)
