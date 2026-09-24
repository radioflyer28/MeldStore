"""Thread-confined, explicit SQL transactions over public MeldDB or sqlite3 APIs."""

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .access import Lease, canonical
from .errors import (
    BusyError,
    CommitError,
    ConstraintError,
    MeldStoreError,
    RollbackError,
    TransactionError,
    TransactionOutcomeError,
    ValidationError,
)


def _translate_melddb_outcome(exc):
    """Map public upstream outcome evidence without importing MeldDB for sqlite use."""
    from melddb.errors import CommitError as MeldDBCommitError
    from melddb.errors import RollbackError as MeldDBRollbackError
    from melddb.errors import TransactionOutcomeError as MeldDBTransactionOutcomeError

    if not isinstance(exc, MeldDBTransactionOutcomeError):
        return None
    kind = TransactionOutcomeError
    if isinstance(exc, MeldDBCommitError):
        kind = CommitError
    elif isinstance(exc, MeldDBRollbackError):
        kind = RollbackError
    return kind(
        str(exc),
        phase=exc.phase,
        outcome=exc.outcome,
        initiating_error=exc.initiating_error,
        backend_error=exc.backend_error,
    )


def _check_sql(statement, *, write=True):
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
    # Some PRAGMAs take effect during preparation even under EXPLAIN. Limit
    # explained statements to SELECT rather than letting EXPLAIN bypass controls.
    if match[0].upper() == "EXPLAIN" and not re.match(
        r"EXPLAIN\s+(?:QUERY\s+PLAN\s+)?SELECT\b", remaining, re.IGNORECASE
    ):
        raise ValidationError("EXPLAIN supports SELECT statements only")


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

    def __init__(self, catalog, execute, *, write=True):
        self._catalog = catalog
        self._execute = execute
        self._failed = False
        self._live = True
        self._write = write

    def sql(self, statement: str, params=()) -> list[dict]:
        self._catalog.require_transaction(self)
        try:
            _check_sql(statement, write=self._write)
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

    def __init__(
        self,
        path: str | Path,
        *,
        adapter: str = "melddb",
        timeout: float = 5.0,
        maintenance: bool = False,
        journal_mode: str = "wal",
    ):
        if adapter not in {"sqlite", "melddb"}:
            raise ValidationError("adapter must be 'melddb' or 'sqlite'")
        if not isinstance(journal_mode, str) or journal_mode not in {"wal", "delete"}:
            raise ValidationError("journal_mode must be 'wal' or 'delete'")
        if adapter == "sqlite":
            version = sqlite3.sqlite_version_info
            patched = (
                version >= (3, 51, 3)
                or ((3, 50, 7) <= version < (3, 51, 0))
                or ((3, 44, 6) <= version < (3, 45, 0))
            )
            if str(path) != ":memory:" and journal_mode == "wal" and not patched:
                raise ValidationError(
                    "WAL requires SQLite with the WAL-reset fix (3.51.3+, 3.50.7+, "
                    "or 3.44.6+ on those release branches). Upgrade Python's SQLite "
                    "runtime or explicitly select journal_mode='delete'."
                )
        if type(maintenance) is not bool:
            raise ValidationError("maintenance must be boolean")
        self._owner = threading.get_ident()
        self._active = None
        self._closed = False
        self._broken = False
        self._outcome_error = None
        self._readers = 0
        self._exclusive = maintenance
        self._leases = {}
        self.path = None if str(path) == ":memory:" else canonical(path)
        if maintenance and self.path is None:
            raise ValidationError("Maintenance requires a file-backed catalog")
        self._access = (
            Lease(self.path + ".meldstore-access", owner=self.path, exclusive=maintenance)
            if self.path
            else None
        )
        self.adapter = adapter
        self._timeout = timeout
        try:
            if self.path is not None and adapter == "sqlite":
                # Journal mode is a file property. Use only public driver APIs;
                # the direct adapter owns its independent setup policy.
                control = sqlite3.connect(self.path, timeout=timeout, autocommit=True)
                try:
                    mode = control.execute("PRAGMA journal_mode").fetchone()[0]
                    if mode != journal_mode:
                        mode = control.execute(f"PRAGMA journal_mode={journal_mode}").fetchone()[0]
                    if mode != journal_mode:
                        raise ValidationError(f"Could not enable journal mode {journal_mode}")
                finally:
                    control.close()
            self._open(path, adapter, timeout, journal_mode)
            expected_mode = journal_mode if self.path is not None else "memory"
            if adapter == "melddb":
                runtime = self._connection.sqlite_runtime()
                if (
                    runtime["journal_mode"] != expected_mode
                    or runtime["synchronous"] != 2
                    or runtime["foreign_keys"] is not True
                ):
                    raise ValidationError("Live catalog SQLite settings do not match policy")
            elif self.sql("SELECT * FROM pragma_journal_mode", write=False) != [
                {"journal_mode": expected_mode}
            ]:
                raise ValidationError("Live catalog journal mode does not match requested policy")
        except BaseException as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            if self._access:
                self._access.close()
            if isinstance(exc, sqlite3.Error) or type(exc).__module__.startswith("melddb"):
                raise _translate(exc) from exc
            raise

    def _open(self, path, adapter, timeout, journal_mode):
        if adapter == "sqlite":
            self._connection = sqlite3.connect(path, timeout=timeout, autocommit=True)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
        elif adapter == "melddb":
            import melddb

            options = {"timeout": timeout}
            if str(path) != ":memory:":
                options["journal_mode"] = journal_mode
            self._connection = melddb.open(str(path), **options)
        else:
            raise ValidationError("adapter must be 'melddb' or 'sqlite'")

    def bind_storage(self, root):
        self.require_idle()
        if self.path is None:
            raise ValidationError("Blob storage requires a file-backed catalog")
        key = canonical(root)
        if key not in self._leases:
            self._leases[key] = Lease(
                Path(root) / "meldstore-access.sqlite", owner=self.path, exclusive=self._exclusive
            )

    def require_maintenance(self, root):
        self.require_idle()
        if not self._exclusive or canonical(root) not in self._leases or self._readers:
            raise TransactionError(
                "Open a separate Catalog(..., maintenance=True) after closing all participants"
            )

    def _check(self):
        if threading.get_ident() != self._owner:
            raise TransactionError("Catalog is confined to its creating thread")
        if self._closed:
            raise TransactionError("Catalog is closed")
        if self._broken:
            raise TransactionError("Catalog needs closing after a failed commit or rollback")

    def _quarantine(self, error):
        self._broken = True
        self._outcome_error = error
        return error

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
    def transaction(self, *, write=True):
        self._check()
        if type(write) is not bool:
            raise ValidationError("write must be boolean")
        if self._active is not None:
            self._active._failed = True
            raise TransactionError("Nested transactions are not supported")
        if self.adapter == "melddb":
            with self._melddb_transaction(write) as tx:
                yield tx
            return
        with self._sqlite_transaction(write) as tx:
            yield tx

    def _query_only(self):
        cursor = self._connection.execute("PRAGMA query_only")
        try:
            return int(cursor.fetchone()[0])
        finally:
            cursor.close()

    def _set_query_only(self, enabled):
        cursor = self._connection.execute(f"PRAGMA query_only={'ON' if enabled else 'OFF'}")
        cursor.close()
        expected = int(enabled)
        if self._query_only() != expected:
            raise RuntimeError("SQLite query-only state could not be verified")

    @contextmanager
    def _sqlite_transaction(self, write):
        previous_query_only = None
        try:
            if not write:
                previous_query_only = self._query_only()
                self._set_query_only(True)
            self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        except BaseException as initiating:
            try:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                if previous_query_only is not None:
                    self._set_query_only(bool(previous_query_only))
            except BaseException as backend:
                error = TransactionOutcomeError(
                    "Transaction start failed and recovery is uncertain; close and reopen",
                    phase="begin",
                    outcome="unknown",
                    initiating_error=initiating,
                    backend_error=backend,
                )
                raise self._quarantine(error) from initiating
            if isinstance(initiating, Exception):
                raise _translate(initiating) from initiating
            raise

        def execute(statement, params):
            cursor = self._connection.execute(statement, params)
            try:
                return [dict(row) for row in cursor.fetchall()] if cursor.description else []
            finally:
                cursor.close()

        tx = Transaction(self, execute, write=write)
        self._active = tx
        try:
            yield tx
            if tx._failed:
                raise TransactionError("Failed transaction cannot commit")
        except BaseException as initiating:
            try:
                self._connection.execute("ROLLBACK")
            except BaseException as backend:
                error = RollbackError(
                    "Rollback failed; close and reopen before retrying",
                    phase="rollback",
                    outcome="unknown",
                    initiating_error=initiating,
                    backend_error=backend,
                )
                raise self._quarantine(error) from initiating
            if not write:
                try:
                    self._set_query_only(bool(previous_query_only))
                except BaseException as backend:
                    error = RollbackError(
                        "Transaction rolled back but connection cleanup failed; close and reopen",
                        phase="cleanup",
                        outcome="rolled_back",
                        initiating_error=initiating,
                        backend_error=backend,
                    )
                    raise self._quarantine(error) from initiating
            raise
        else:
            try:
                self._commit(None)
            except BaseException as backend:
                error = CommitError(
                    "Commit outcome is uncertain; close and reopen before retrying",
                    phase="commit",
                    outcome="unknown",
                    backend_error=backend,
                )
                raise self._quarantine(error) from backend
            if not write:
                try:
                    self._set_query_only(bool(previous_query_only))
                except BaseException as backend:
                    error = CommitError(
                        "Transaction committed but connection cleanup failed; close and reopen",
                        phase="cleanup",
                        outcome="committed",
                        backend_error=backend,
                    )
                    raise self._quarantine(error) from backend
        finally:
            tx._live = False
            self._active = None

    @contextmanager
    def _melddb_transaction(self, write):
        manager = self._connection.transaction(write=write)
        try:
            raw = manager.__enter__()
        except BaseException as exc:
            mapped = _translate_melddb_outcome(exc)
            if mapped is not None:
                raise self._quarantine(mapped) from (exc.__cause__ or exc)
            if isinstance(exc, Exception):
                raise _translate(exc) from exc
            raise

        tx = Transaction(self, raw.sql, write=write)
        self._active = tx
        try:
            yield tx
            if tx._failed:
                raise TransactionError("Failed transaction cannot commit")
        except BaseException as initiating:
            try:
                manager.__exit__(type(initiating), initiating, initiating.__traceback__)
            except BaseException as exc:
                mapped = _translate_melddb_outcome(exc)
                if mapped is not None:
                    raise self._quarantine(mapped) from (exc.__cause__ or exc)
                if not isinstance(exc, Exception):
                    raise
                error = RollbackError(
                    "Rollback failed; close and reopen before retrying",
                    phase="rollback",
                    outcome="unknown",
                    initiating_error=initiating,
                    backend_error=exc,
                )
                raise self._quarantine(error) from initiating
            raise
        else:
            try:
                self._commit(manager)
            except BaseException as exc:
                mapped = _translate_melddb_outcome(exc)
                if mapped is not None:
                    raise self._quarantine(mapped) from (exc.__cause__ or exc)
                if not isinstance(exc, Exception):
                    raise
                error = CommitError(
                    "Commit failed; close and reopen before retrying",
                    phase="commit",
                    outcome="unknown",
                    backend_error=exc,
                )
                raise self._quarantine(error) from exc
        finally:
            tx._live = False
            self._active = None

    def _commit(self, manager):
        if manager is not None:
            manager.__exit__(None, None, None)
        else:
            self._connection.execute("COMMIT")

    def sql(self, statement: str, params=(), *, write=True) -> list[dict]:
        """One owned transaction; use tx.sql to compose multiple operations."""
        with self.transaction(write=write) as tx:
            return tx.sql(statement, params)

    def snapshot(self, destination):
        """Standalone whole-database SQLite snapshot, under exclusive access.

        Includes application SQL objects. This is not MeldDB's managed export.
        Store.backup additionally coordinates and verifies payloads.
        """
        self.require_idle()
        if not self._exclusive or self._readers or self.path is None:
            raise TransactionError("Snapshot requires an idle exclusive file-backed catalog")
        destination = Path(destination)
        if self.adapter == "melddb":
            self._connection.backup(destination)
        else:
            # Reserve a new file; never let sqlite3.connect overwrite a caller's DB.
            with destination.open("xb"):
                pass
            target = sqlite3.connect(destination, autocommit=True)
            try:
                self._connection.backup(target)
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValidationError("Catalog snapshot integrity check failed")
                if target.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValidationError("Catalog snapshot has invalid references")
                target.execute("PRAGMA journal_mode=DELETE")
            finally:
                target.close()
        with destination.open("r+b") as stream:
            os.fsync(stream.fileno())
        return destination

    def maintain_sqlite(self, *, analyze=False, checkpoint="passive"):
        """Explicit statistics/checkpoint work under cooperative exclusive access.

        FULL durability and SQLite's automatic checkpoint threshold are unchanged.
        A busy checkpoint is reported, not mistaken for a successful truncation.
        """
        self.require_idle()
        if not self._exclusive or self._readers or self.path is None:
            raise TransactionError("SQLite maintenance requires an exclusive file-backed catalog")
        if (
            type(analyze) is not bool
            or not isinstance(checkpoint, str)
            or checkpoint not in {"passive", "truncate"}
        ):
            raise ValidationError("Use boolean analyze and passive/truncate checkpoint")
        if self.adapter == "melddb":
            try:
                result = self._connection.maintain_sqlite(
                    statistics="analyze" if analyze else "optimize",
                    checkpoint=checkpoint,
                )
                upstream = result["checkpoint"]

                def frame(value):
                    return -1 if value is None else int(value)

                return {
                    "statistics": result["statistics"]["action"],
                    "checkpoint": {
                        "busy": int(upstream["busy"]),
                        "log_frames": frame(upstream["log_frames"]),
                        "checkpointed_frames": frame(upstream["checkpointed_frames"]),
                    },
                }
            except Exception as exc:
                raise _translate(exc) from exc
        control = sqlite3.connect(self.path, timeout=self._timeout, autocommit=True)
        try:
            control.execute("PRAGMA synchronous=FULL")
            control.execute("ANALYZE" if analyze else "PRAGMA optimize=0x10002").fetchall()
            # ANALYZE on the control connection updates persistent statistics.
            # Reload them on the actual application connection as well.
            self.sql("ANALYZE sqlite_schema")
            busy, log, completed = control.execute(
                f"PRAGMA wal_checkpoint({checkpoint})"
            ).fetchone()
            return {
                "statistics": "analyze" if analyze else "optimize",
                "checkpoint": {"busy": busy, "log_frames": log, "checkpointed_frames": completed},
            }
        except sqlite3.Error as exc:
            raise _translate(exc) from exc
        finally:
            control.close()

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

    def _close(self, primary=None):
        if threading.get_ident() != self._owner or self._closed:
            raise TransactionError("Catalog is closed or used from another thread")
        if self._active is not None or self._readers:
            if self._active is not None:
                self._active._failed = True
            raise TransactionError("Cannot close an active catalog")
        cleanup_errors = []
        resources = [self._connection, *self._leases.values()]
        if self._access:
            resources.append(self._access)
        for resource in resources:
            try:
                resource.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        self._closed = True
        if not cleanup_errors:
            return
        propagating = primary is not None
        primary = primary or self._outcome_error
        if primary is not None:
            existing = tuple(getattr(primary, "cleanup_errors", ()))
            primary.cleanup_errors = existing + tuple(cleanup_errors)
            if not propagating and primary is self._outcome_error:
                raise primary
            return
        error = TransactionError("Catalog close encountered cleanup failures")
        error.cleanup_errors = tuple(cleanup_errors)
        raise error from cleanup_errors[0]

    def close(self):
        self._close()

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._close(primary=exc)
