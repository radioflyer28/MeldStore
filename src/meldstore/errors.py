"""Stable errors shared by catalog adapters."""


class MeldStoreError(Exception):
    """Base for library failures; underlying driver errors remain as causes."""


class ValidationError(MeldStoreError, ValueError):
    """Invalid declaration, value, or SQL operation."""


class SchemaConflictError(ValidationError):
    """A persisted schema cannot be silently changed or upgraded."""


class ConstraintError(MeldStoreError):
    """SQL constraint rejected a write."""


class BusyError(MeldStoreError):
    """Another connection holds the write lock; no automatic retry."""


class TransactionError(MeldStoreError):
    """Closed, nested, foreign, wrong-thread, or poisoned transaction."""


class CommitError(TransactionError):
    """Commit failed; callers must resolve durable state before retrying."""
