"""Live Snowflake execution via the official Python connector.

Only SQL that passed `sql_guard` should reach `run_validated`; metadata queries
use `run_metadata` with bind parameters. No code path returns fabricated rows.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import snowflake.connector
from snowflake.connector import errors as sf_errors
from snowflake.connector.constants import FIELD_ID_TO_NAME

from config.settings import SnowflakeSettings
from src.utils.errors import ErrorCategory, SnowflakeError

log = logging.getLogger(__name__)


@dataclass
class QueryResult:
    sql: str
    query_id: str | None
    columns: list[str]
    column_types: list[str]
    rows: list[tuple[Any, ...]]
    total_rows: int | None  # rows Snowflake produced (may exceed fetched rows)
    truncated: bool
    elapsed_ms: int
    status: str = "SUCCESS"
    session_info: dict[str, str] = field(default_factory=dict)

    @property
    def fetched_rows(self) -> int:
        return len(self.rows)


def _normalise(value: Any) -> Any:
    """Convert connector types to plain Python without losing precision.

    Integral decimals become int; fractional decimals stay Decimal (exact). Charts convert to
    float only for plot coordinates, never for displayed or exported values.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else value
    if isinstance(value, (dt.datetime, dt.date)):
        return value
    return value


def classify_snowflake_error(exc: Exception) -> SnowflakeError:
    """Map connector exceptions to user-safe categories (no credentials in messages)."""
    errno = getattr(exc, "errno", None)
    msg = str(getattr(exc, "msg", "") or exc)
    low = msg.lower()
    diag = f"errno={errno} sqlstate={getattr(exc, 'sqlstate', None)} msg={msg[:500]}"
    if errno in (390100, 390144, 390190, 390191, 390318, 390422) or "incorrect username or password" in low \
            or "authentication" in low or "jwt" in low:
        return SnowflakeError("Snowflake authentication failed. Check the account, user and credential settings in .env.",
                              diag, ErrorCategory.SNOWFLAKE_AUTH)
    if errno in (630, 604) or "timeout" in low or "canceled" in low:
        return SnowflakeError("The query exceeded the statement timeout and was cancelled. Try a narrower question.",
                              diag, ErrorCategory.QUERY_TIMEOUT)
    if errno == 606 or "no active warehouse" in low or "warehouse" in low and "suspend" in low:
        return SnowflakeError("No usable Snowflake warehouse. Check SNOWFLAKE_WAREHOUSE and that the role may use it.",
                              diag, ErrorCategory.SNOWFLAKE_WAREHOUSE)
    if errno == 2003 or "does not exist or not authorized" in low or "insufficient privileges" in low:
        return SnowflakeError("The configured role cannot access an object this query needs.",
                              diag, ErrorCategory.SNOWFLAKE_PERMISSION)
    if isinstance(exc, (sf_errors.OperationalError, sf_errors.InterfaceError)) or errno in (250001, 250003):
        return SnowflakeError("Could not reach Snowflake (network or account identifier problem).",
                              diag, ErrorCategory.SNOWFLAKE_CONNECTION)
    return SnowflakeError("Snowflake rejected the query.", diag, ErrorCategory.SNOWFLAKE_SQL)


class SnowflakeService:
    def __init__(self, cfg: SnowflakeSettings, max_rows: int):
        self.cfg = cfg
        self.max_rows = max_rows
        self._conn: snowflake.connector.SnowflakeConnection | None = None

    # -- connection -------------------------------------------------------
    def _connect(self) -> snowflake.connector.SnowflakeConnection:
        missing = self.cfg.missing()
        if missing:
            raise SnowflakeError("Snowflake is not configured. Missing: " + ", ".join(missing),
                                 "missing config", ErrorCategory.CONFIGURATION)
        params: dict[str, Any] = {
            "account": self.cfg.account,
            "user": self.cfg.user,
            "warehouse": self.cfg.warehouse,
            "login_timeout": self.cfg.login_timeout_s,
            "network_timeout": self.cfg.statement_timeout_s + 30,
            "application": "DataMind",
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": self.cfg.statement_timeout_s,
                "QUERY_TAG": "datamind",
                "USE_CACHED_RESULT": True,
            },
        }
        if self.cfg.role:
            params["role"] = self.cfg.role
        if self.cfg.private_key_file:
            params["private_key_file"] = self.cfg.private_key_file
            if self.cfg.private_key_passphrase:
                params["private_key_file_pwd"] = self.cfg.private_key_passphrase
        elif self.cfg.password:
            params["password"] = self.cfg.password
        if self.cfg.authenticator:
            params["authenticator"] = self.cfg.authenticator
        try:
            return snowflake.connector.connect(**params)
        except Exception as exc:  # noqa: BLE001 - classified below
            err = classify_snowflake_error(exc)
            log.warning("Snowflake connect failed: %s", err.diagnostic)
            raise err from exc

    def connection(self) -> snowflake.connector.SnowflakeConnection:
        if self._conn is None or self._conn.is_closed():
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # -- execution --------------------------------------------------------
    def _execute(self, sql: str, params: Any = None, max_rows: int | None = None) -> QueryResult:
        limit = max_rows or self.max_rows
        for attempt in (1, 2):
            cur = None
            start = time.perf_counter()
            try:
                cur = self.connection().cursor()
                cur.execute(sql, params)
                rows = cur.fetchmany(limit + 1)
                truncated = len(rows) > limit
                rows = [tuple(_normalise(v) for v in r) for r in rows[:limit]]
                desc = cur.description or []
                return QueryResult(
                    sql=sql,
                    query_id=cur.sfqid,
                    columns=[d.name for d in desc],
                    column_types=[FIELD_ID_TO_NAME.get(d.type_code, str(d.type_code)) for d in desc],
                    rows=rows,
                    total_rows=cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None,
                    truncated=truncated,
                    elapsed_ms=int((time.perf_counter() - start) * 1000),
                )
            except sf_errors.Error as exc:
                err = classify_snowflake_error(exc)
                # A dropped session is worth exactly one reconnect; nothing else is retried.
                if attempt == 1 and err.category == ErrorCategory.SNOWFLAKE_CONNECTION:
                    self.close()
                    continue
                log.warning("Snowflake query failed (qid=%s): %s", getattr(exc, "sfqid", None), err.diagnostic)
                err.query_id = getattr(exc, "sfqid", None)  # type: ignore[attr-defined]
                raise err from exc
            finally:
                if cur is not None:
                    cur.close()
        raise SnowflakeError("Could not reach Snowflake.", "retry exhausted", ErrorCategory.SNOWFLAKE_CONNECTION)

    def run_validated(self, sql: str) -> QueryResult:
        """Execute SQL that has already passed `sql_guard.validate` (caller's responsibility)."""
        return self._execute(sql)

    def run_metadata(self, sql: str, params: Any = None, max_rows: int = 10000) -> QueryResult:
        """Metadata/introspection queries written by DataMind itself, with bind parameters."""
        return self._execute(sql, params, max_rows=max_rows)

    def health_check(self) -> dict[str, str]:
        """Run a real query and report the session context (proves the connection is live)."""
        res = self._execute(
            "SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_VERSION()")
        acct, user, role, wh, ver = res.rows[0]
        return {"account": acct, "user": user, "role": role, "warehouse": wh or "(none)",
                "version": ver, "query_id": res.query_id or ""}
