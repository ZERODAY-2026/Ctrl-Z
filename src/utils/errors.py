"""Typed error categories used across DataMind.

Every error carries a safe, user-facing message and an internal diagnostic.
Neither may contain secrets; the internal diagnostic goes to logs only.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    CONFIGURATION = "configuration"
    SNOWFLAKE_AUTH = "snowflake_auth"
    SNOWFLAKE_PERMISSION = "snowflake_permission"
    SNOWFLAKE_CONNECTION = "snowflake_connection"
    SNOWFLAKE_WAREHOUSE = "snowflake_warehouse"
    SNOWFLAKE_SQL = "snowflake_sql"
    QUERY_TIMEOUT = "query_timeout"
    QUERY_VALIDATION = "query_validation"
    SCHEMA_MISMATCH = "schema_mismatch"
    GEMMA_UNAVAILABLE = "gemma_unavailable"
    GEMMA_INVALID_OUTPUT = "gemma_invalid_output"
    INPUT = "input"
    INTERNAL = "internal"


# Whether retrying the same request might reasonably succeed.
RETRYABLE = {
    ErrorCategory.SNOWFLAKE_CONNECTION,
    ErrorCategory.SNOWFLAKE_WAREHOUSE,
    ErrorCategory.QUERY_TIMEOUT,
    ErrorCategory.GEMMA_UNAVAILABLE,
    ErrorCategory.GEMMA_INVALID_OUTPUT,
}


class DataMindError(Exception):
    """Base error with a safe user message and a log-only diagnostic."""

    category: ErrorCategory = ErrorCategory.INTERNAL

    def __init__(self, user_message: str, diagnostic: str = "", category: ErrorCategory | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.diagnostic = diagnostic
        if category is not None:
            self.category = category

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE


class ConfigurationError(DataMindError):
    category = ErrorCategory.CONFIGURATION


class SnowflakeError(DataMindError):
    category = ErrorCategory.SNOWFLAKE_SQL


class QueryValidationError(DataMindError):
    category = ErrorCategory.QUERY_VALIDATION


class SchemaMismatchError(DataMindError):
    category = ErrorCategory.SCHEMA_MISMATCH


class GemmaError(DataMindError):
    category = ErrorCategory.GEMMA_UNAVAILABLE
