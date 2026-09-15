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


class NotFoundError(MeldStoreError):
    """No ready record exists for the requested ID."""


class ConflictError(MeldStoreError):
    """An identity or prepared object is already associated with a different operation."""


class IntegrityError(MeldStoreError):
    """Stored payload is missing or does not match its recorded size and digest."""


class StorageError(MeldStoreError):
    """Object I/O failed; this is not evidence that the object is absent."""
