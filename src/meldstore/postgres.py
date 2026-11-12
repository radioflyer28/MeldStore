"""Optional PostgreSQL connection boundary; payload coordination is separate."""

import math
from contextlib import contextmanager

from .errors import CatalogConnectionError, MeldStoreError, ValidationError


def _safe_error(error):
    """Retain categories and outcome evidence, never driver messages or arguments."""
    from melddb.errors import MeldDBError, TransactionOutcomeError

    if isinstance(error, TransactionOutcomeError):
        result = type(error)(
            "PostgreSQL transaction outcome requires inspection; close and reopen",
            phase=error.phase, outcome=error.outcome,
            initiating_error=_safe_error(error.initiating_error) if error.initiating_error else None,
            backend_error=_safe_error(error.backend_error) if error.backend_error else None,
        )
    elif isinstance(error, (MeldDBError, MeldStoreError)):
        result = type(error)("PostgreSQL operation failed (driver detail redacted)")
    else:
        result = RuntimeError("PostgreSQL operation failed (driver detail redacted)")
    result.provider_type = type(error).__name__
    cause = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        code = getattr(cause, "sqlstate", None)
        if isinstance(code, str) and len(code) == 5 and code.isascii() and code.isalnum():
            result.sqlstate = code
            break
        cause = cause.__cause__
    return result


@contextmanager
def _redacted():
    from melddb.errors import MeldDBError
    from psycopg import Error as DriverError

    try:
        yield
    except (MeldDBError, DriverError) as exc:
        raise _safe_error(exc) from None


class _Transaction:
    def __init__(self, provider):
        self._provider = provider

    def sql(self, statement, params=()):
        with _redacted():
            return self._provider.sql(statement, params)

    def validate_constraints(self):
        with _redacted():
            return self._provider.validate_constraints()

    def sql_many(self, statement, params):
        with _redacted():
            return self._provider.sql_many(statement, params)


class _Connection:
    def __init__(self, provider):
        self._provider = provider

    @contextmanager
    def transaction(self, *, write=True):
        with _redacted():
            with self._provider.transaction(write=write) as tx:
                yield _Transaction(tx)

    def close(self):
        with _redacted():
            self._provider.close()


def connect(dsn, *, timeout, connect_timeout):
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValidationError("PostgreSQL requires an explicit nonempty DSN")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0.001:
        raise ValidationError("PostgreSQL lock timeout must be finite and at least 0.001 seconds")
    if type(connect_timeout) is not int or connect_timeout < 1:
        raise ValidationError("PostgreSQL connect_timeout must be a positive integer")
    try:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
    except ImportError:
        raise ValidationError("Install meldstore[postgres] to use PostgreSQL catalogs") from None
    try:
        config = conninfo_to_dict(dsn)
    except Exception:
        raise ValidationError("Invalid PostgreSQL DSN (detail redacted)") from None
    if not (config.get("host") or config.get("hostaddr")) or not all(
        config.get(key) for key in ("dbname", "user")
    ):
        raise ValidationError("PostgreSQL DSN must specify host or hostaddr, dbname, and user")
    if "service" in config:
        raise ValidationError("Use explicit PostgreSQL connection fields instead of a service")
    try:
        import melddb

        provider = melddb.connect(make_conninfo(dsn, connect_timeout=connect_timeout), timeout=timeout)
    except Exception:
        raise CatalogConnectionError("Unable to open PostgreSQL catalog (detail redacted)") from None
    return _Connection(provider)
