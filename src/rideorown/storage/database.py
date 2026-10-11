"""Small SQLite store: one transactional, versioned personal workspace."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout = 10000")
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS workspace ("
            "id INTEGER PRIMARY KEY CHECK(id = 1), payload TEXT NOT NULL)"
        )
