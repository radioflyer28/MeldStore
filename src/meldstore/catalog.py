"""Thread-confined, explicit SQL transactions over public MeldDB or sqlite3 APIs."""

import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .errors import (
    BusyError,
    CommitError,
    ConstraintError,
    MeldStoreError,
    TransactionError,
    ValidationError,
)


def _check_sql(statement):
    if not isinstance(statement, str):
        raise ValidationError("SQL must be text")
    remaining = statement.lstrip()
    while remaining.startswith(("--", "/*")):
        if remaining.startswith("--"):
            _, _, remaining = remaining.partition("\n")
        else:
            _, end, remaining = remaining.partition("*/")
            if not end:
                raise ValidationError("Unclosed SQL comment")
        remaining = remaining.lstrip()
    match = re.match(r"[A-Za-z]+", remaining)
    if not match or match[0].upper() in {
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "END",
        "SAVEPOINT",
        "RELEASE",
        "VACUUM",
        "PRAGMA",
        "ATTACH",
        "DETACH",
    }:
        raise ValidationError("Transaction and connection control SQL is not supported")


def _translate(exc):
    if isinstance(exc, MeldStoreError):
        return exc
    if isinstance(exc, sqlite3.IntegrityError) or type(exc).__name__ in {
        "AlreadyExistsError",
        "ConstraintError",
    }:
        return ConstraintError(str(exc))
    code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or type(exc).__name__ == "BusyError":
        return BusyError(str(exc))
    return ValidationError(str(exc))


class Transaction:
    """Live only inside its owning Catalog.transaction() context."""

    def __init__(self, catalog, execute):
        self._catalog = catalog
        self._execute = execute
        self._failed = False
        self._live = True

    def sql(self, statement: str, params=()) -> list[dict]:
        self._catalog.require_transaction(self)
        try:
            _check_sql(statement)
            if not isinstance(params, (tuple, list, dict)):
                raise ValidationError("Bindings must be a tuple, list, or dict")
            return self._execute(statement, params)
        except BaseException as exc:
            self._failed = True
            if not isinstance(exc, Exception):
                raise
            raise _translate(exc) from exc

    @contextmanager
    def operation(self):
        """Compose library validation and SQL; any failure poisons this transaction."""
        self._catalog.require_transaction(self)
        try:
            yield self
        except BaseException:
            self._failed = True
            raise


class Catalog:
    """Owns one connection. Opening does not install a schema or create managed tables."""

    def __init__(self, path: str | Path, *, adapter: str = "melddb", timeout: float = 5.0):
        self._owner = threading.get_ident()
        self._active = None
        self._closed = False
        self._broken = False
        self.adapter = adapter
        if adapter == "sqlite":
            self._connection = sqlite3.connect(path, timeout=timeout, autocommit=True)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
        elif adapter == "melddb":
            import melddb

            self._connection = melddb.open(str(path), timeout=timeout)
        else:
            raise ValidationError("adapter must be 'melddb' or 'sqlite'")

    def _check(self):
        if threading.get_ident() != self._owner:
            raise TransactionError("Catalog is confined to its creating thread")
        if self._closed:
            raise TransactionError("Catalog is closed")
        if self._broken:
            raise TransactionError("Catalog needs closing after a failed rollback")

    def require_transaction(self, tx: Transaction) -> Transaction:
        self._check()
        if not isinstance(tx, Transaction) or tx._catalog is not self:
            if self._active is not None:
                self._active._failed = True
            raise TransactionError("Transaction belongs to another catalog")
        if not tx._live or self._active is not tx or tx._failed:
            if self._active is not None:
                self._active._failed = True
            raise TransactionError("Transaction is expired or failed")
        return tx

    def require_idle(self):
        """Reject storage work while this catalog owns an active SQL transaction."""
        self._check()
        if self._active is not None:
            self._active._failed = True
            raise TransactionError("Storage I/O must occur outside a catalog transaction")

    @contextmanager
    def transaction(self):
        self._check()
        if self._active is not None:
            self._active._failed = True
            raise TransactionError("Nested transactions are not supported")
        manager = None
        try:
            if self.adapter == "melddb":
                manager = self._connection.transaction(write=True)
                raw = manager.__enter__()
                execute = raw.sql
            else:
                self._connection.execute("BEGIN IMMEDIATE")

                def execute(statement, params):
                    cursor = self._connection.execute(statement, params)
                    try:
                        return (
                            [dict(row) for row in cursor.fetchall()] if cursor.description else []
                        )
                    finally:
                        cursor.close()
        except Exception as exc:
            raise _translate(exc) from exc
        tx = Transaction(self, execute)
        self._active = tx
        try:
            yield tx
            if tx._failed:
                raise TransactionError("Failed transaction cannot commit")
        except BaseException as exc:
            try:
                if manager is not None:
                    manager.__exit__(type(exc), exc, exc.__traceback__)
                else:
                    self._connection.execute("ROLLBACK")
            except Exception as rollback_error:
                self._broken = True
                raise TransactionError("Rollback failed; close this catalog") from rollback_error
            raise
        else:
            try:
                if manager is not None:
                    manager.__exit__(None, None, None)
                else:
                    self._connection.execute("COMMIT")
            except BaseException as exc:
                if self.adapter == "sqlite" and self._connection.in_transaction:
                    try:
                        self._connection.execute("ROLLBACK")
                    except Exception:
                        self._broken = True
                raise CommitError("Commit failed; resolve state before retrying") from exc
        finally:
            tx._live = False
            self._active = None

    def sql(self, statement: str, params=()) -> list[dict]:
        """One owned transaction; use tx.sql to compose multiple operations."""
        with self.transaction() as tx:
            return tx.sql(statement, params)

    def install_schema(self, schema, *, tx: Transaction | None = None) -> str:
        from .installation import install

        if tx is None:
            with self.transaction() as owned:
                return self.install_schema(schema, tx=owned)
        self.require_transaction(tx)
        try:
            return install(schema, tx)
        except BaseException:
            tx._failed = True
            raise

    def close(self):
        if threading.get_ident() != self._owner or self._closed:
            raise TransactionError("Catalog is closed or used from another thread")
        if self._active is not None:
            self._active._failed = True
            raise TransactionError("Cannot close an active catalog")
        self._connection.close()
        self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *_):
        self.close()
