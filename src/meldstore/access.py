"""Cross-process cooperative access leases, backed by SQLite rollback-mode locks."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import BusyError, ValidationError


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


class Lease:
    def __init__(self, path, *, owner, exclusive=False):
        self._connection = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            connection = self._connection
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ValidationError("Access gate must use rollback DELETE journal mode")
            if not connection.execute(
                "SELECT name FROM sqlite_master WHERE name='access_owner'"
            ).fetchone():
                connection.execute("BEGIN EXCLUSIVE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS access_owner (owner TEXT NOT NULL PRIMARY KEY)"
                )
                if not connection.execute("SELECT owner FROM access_owner").fetchall():
                    connection.execute("INSERT INTO access_owner VALUES (?)", (owner,))
                connection.execute("COMMIT")
            connection.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN")
            if connection.execute("SELECT owner FROM access_owner").fetchall() != [(owner,)]:
                raise ValidationError("Storage root/access gate belongs to another catalog path")
        except BaseException as exc:
            self._connection.close()
            if isinstance(exc, sqlite3.OperationalError) and getattr(
                exc, "sqlite_errorcode", 0
            ) & 0xFF in (5, 6):
                raise BusyError(
                    "Exclusive maintenance conflicts with another open participant"
                ) from exc
            raise

    def close(self):
        self._connection.close()


@contextmanager
def catalog_access(path):
    """Shared lease for a standalone SQL connection and all payload use derived from it."""
    name = canonical(path)
    lease = Lease(name + ".meldstore-access", owner=name)
    try:
        yield
    finally:
        lease.close()
