"""Parser-based SQL guard. Fails closed.

Supported subset: a single SELECT (optionally with CTEs or UNION/INTERSECT/EXCEPT)
over allowlisted tables in the configured database/schema. Rejects DML, DDL,
session commands, table functions, LATERAL, SELECT INTO, Snowflake system
functions, unknown tables and unknown columns, and enforces a row LIMIT.

This is defence in depth on top of a read-only Snowflake role; it is not a proof
that every possible hostile SQL string is harmless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from src.utils.errors import QueryValidationError

if TYPE_CHECKING:  # annotation only; avoids an import cycle with snowflake_service
    from src.services.schema_service import SchemaSnapshot

MAX_SQL_CHARS = 20_000

_FORBIDDEN_NODE_NAMES = [
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "Command", "Set",
    "Use", "Copy", "Into", "Transaction", "Commit", "Rollback", "Grant", "Revoke", "Describe", "Show",
    "Lateral", "LoadData", "TruncateTable", "Pragma", "Analyze", "Cache", "Uncache", "Refresh",
]
_FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))

_FORBIDDEN_FUNCS = {
    "RESULT_SCAN", "GENERATOR", "IDENTIFIER", "GET_DDL", "TABLE", "FLATTEN", "EXTERNAL_FUNCTION",
    "INFER_SCHEMA", "VALIDATE", "GET_PRESIGNED_URL", "BUILD_SCOPED_FILE_URL", "BUILD_STAGE_FILE_URL",
    "SYSTEM_FUNCTION", "CURRENT_SECONDARY_ROLES", "READ_FILE", "PARSE_JSON_FILE",
}


@dataclass
class GuardResult:
    sql: str
    tables: list[str]
    columns: list[str]
    limit: int
    notes: list[str] = field(default_factory=list)


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).upper()
    return node.sql_name().upper() if hasattr(node, "sql_name") else type(node).__name__.upper()


def _ident_upper(identifier: exp.Expression | None) -> str:
    """Snowflake folds unquoted identifiers to upper case; quoted ones keep their case."""
    if identifier is None:
        return ""
    if isinstance(identifier, exp.Identifier):
        return identifier.this if identifier.quoted else identifier.this.upper()
    return str(identifier.name).upper()


def assert_read_only(sql: str) -> None:
    """Schema-free check used at the execution boundary: one parseable, read-only SELECT, nothing else.

    Snowflake execution calls this for every statement, independently of `validate` and of any model output,
    so a caller that forgot the full guard still cannot run a data-changing statement.
    """
    _read_only_tree(sql)


def _read_only_tree(sql: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise QueryValidationError("No SQL was produced for this question.", "empty sql")
    if len(sql) > MAX_SQL_CHARS:
        raise QueryValidationError("The generated query is too long to run safely.", f"len={len(sql)}")
    try:
        statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except ParseError as exc:
        raise QueryValidationError("The generated SQL could not be parsed, so it was not run.", str(exc)[:300]) from exc
    if len(statements) != 1:
        raise QueryValidationError("Only a single SQL statement is allowed.", f"statements={len(statements)}")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise QueryValidationError("Only read-only SELECT queries are allowed.", f"root={type(tree).__name__}")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise QueryValidationError("This operation is outside the supported read-only query scope.",
                                       f"forbidden node {type(node).__name__}")
        if isinstance(node, exp.Func):
            name = _func_name(node)
            if name in _FORBIDDEN_FUNCS or name.startswith("SYSTEM$") or name.startswith("SYSTEM_"):
                raise QueryValidationError(f"The function {name} is not allowed.", f"forbidden func {name}")
    return tree


def validate(sql: str, snapshot: SchemaSnapshot, max_rows: int) -> GuardResult:
    """Validate and normalise SQL. Returns the exact SQL to execute or raises QueryValidationError."""
    tree = _read_only_tree(sql)

    # --- tables -----------------------------------------------------------------
    cte_names = {_ident_upper(c.args.get("alias").this) for c in tree.find_all(exp.CTE) if c.args.get("alias")}
    referenced: list[str] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise QueryValidationError("Table functions are not allowed.", f"table expr {table.sql()[:100]}")
        name = _ident_upper(table.this)
        db = _ident_upper(table.args.get("db"))
        catalog = _ident_upper(table.args.get("catalog"))
        if not db and not catalog and name in cte_names:
            continue
        if catalog and catalog != snapshot.database or db and db != snapshot.schema:
            raise QueryValidationError(f"Only tables in {snapshot.database}.{snapshot.schema} may be queried.",
                                       f"out-of-scope table {catalog}.{db}.{name}")
        if name not in snapshot.tables:
            raise QueryValidationError(f"This request requires a table that is not accessible: {name}.",
                                       f"table not allowlisted {name}")
        # Qualify so execution never depends on the session's current database/schema.
        table.set("catalog", exp.to_identifier(snapshot.database))
        table.set("db", exp.to_identifier(snapshot.schema))
        if snapshot.fqn(name) not in referenced:
            referenced.append(snapshot.fqn(name))

    if not referenced:
        raise QueryValidationError("The query does not read from any dataset table.", "no tables")

    # --- columns ----------------------------------------------------------------
    ref_tables = [snapshot.tables[r.split(".")[-1]] for r in referenced]
    known_cols = {c for t in ref_tables for c in t.columns}
    aliases = {_ident_upper(a.args.get("alias")) for a in tree.find_all(exp.Alias)}
    for ta in tree.find_all(exp.TableAlias):
        aliases.add(_ident_upper(ta.this))
        aliases.update(_ident_upper(c) for c in ta.args.get("columns") or [])
    aliases |= cte_names
    used_cols: list[str] = []
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        cname = _ident_upper(col.this)
        if cname in known_cols:
            owner = next((t.name for t in ref_tables if cname in t.columns), "")
            q = f"{owner}.{cname}"
            if q not in used_cols:
                used_cols.append(q)
        elif cname not in aliases:
            raise QueryValidationError(f"The query references an unknown column: {cname}.", f"unknown column {cname}")

    # --- row limit ----------------------------------------------------------------
    notes: list[str] = []
    limit = max_rows
    if isinstance(tree, exp.Select):
        current = tree.args.get("limit")
        existing = None
        if current is not None:
            try:
                existing = int(current.expression.name)
            except (AttributeError, ValueError, TypeError):
                existing = None
        if existing is None or existing > max_rows:
            if existing is not None:
                notes.append(f"Row limit lowered from {existing} to {max_rows}.")
            tree = tree.limit(max_rows, copy=False)
        else:
            limit = existing
    else:
        tree = exp.select("*").from_(tree.subquery("bounded")).limit(max_rows)

    # Comments are dropped so the executed text is exactly the validated statement.
    return GuardResult(sql=tree.sql(dialect="snowflake", pretty=True, comments=False), tables=referenced,
                       columns=used_cols, limit=limit, notes=notes)
