"""Generic relational metadata and verified immutable local file storage."""

from .access import catalog_access
from .backup import restore_backup
from .catalog import Catalog, Transaction
from .errors import (
    BusyError,
    CommitError,
    ConflictError,
    ConstraintError,
    IntegrityError,
    MeldStoreError,
    NotFoundError,
    SchemaConflictError,
    StorageError,
    TransactionError,
    ValidationError,
)
from .handlers import Handler, HandlerRegistry, MissingDependencyError, UnsupportedHandlerError
from .migrations import MetadataMigration
from .query import FindCursor, Order, Predicate
from .schema import (
    BlobSchema,
    Boolean,
    Field,
    Float,
    Index,
    Integer,
    Text,
    Timestamp,
    quote_identifier,
)
from .storage import LocalStorage
from .store import PreparedFile, PreparedValue, Store

__all__ = [
    "restore_backup",
    "Handler",
    "HandlerRegistry",
    "MissingDependencyError",
    "UnsupportedHandlerError",
    "PreparedValue",
    "catalog_access",
    "BlobSchema",
    "Boolean",
    "BusyError",
    "Catalog",
    "CommitError",
    "ConflictError",
    "ConstraintError",
    "Field",
    "Float",
    "Index",
    "Integer",
    "MeldStoreError",
    "MetadataMigration",
    "IntegrityError",
    "NotFoundError",
    "SchemaConflictError",
    "StorageError",
    "Text",
    "Timestamp",
    "Transaction",
    "TransactionError",
    "ValidationError",
    "quote_identifier",
    "LocalStorage",
    "PreparedFile",
    "Store",
    "Predicate",
    "Order",
    "FindCursor",
]
