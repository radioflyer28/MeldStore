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


class TransactionOutcomeError(TransactionError):
    """A transaction boundary left durable state or safe reuse uncertain."""

    def __init__(
        self,
        message,
        *,
        phase=None,
        outcome=None,
        initiating_error=None,
        backend_error=None,
        cleanup_errors=(),
    ):
        super().__init__(message)
        self.phase = phase
        self.outcome = outcome
        self.initiating_error = initiating_error
        self.backend_error = backend_error
        self.cleanup_errors = tuple(cleanup_errors)


class CommitError(TransactionOutcomeError):
    """Commit failed; callers must resolve durable state before retrying."""


class RollbackError(TransactionOutcomeError):
    """Rollback failed or its required cleanup could not be confirmed."""


class NotFoundError(MeldStoreError):
    """No ready record exists for the requested ID."""


class ConflictError(MeldStoreError):
    """An identity or prepared object is already associated with a different operation."""


class IntegrityError(MeldStoreError):
    """Stored payload is missing or does not match its recorded size and digest."""


class StorageError(MeldStoreError):
    """Object I/O failed; this is not evidence that the object is absent."""
