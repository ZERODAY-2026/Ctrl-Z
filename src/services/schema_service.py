"""Discovers the accessible schema from Snowflake INFORMATION_SCHEMA and caches it.

Only tables in the dataset allowlist (and actually visible to the role) are exposed.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass, field

from config.settings import DatasetConfig
from src.services.snowflake_service import SnowflakeService
from src.utils.errors import ConfigurationError, SchemaMismatchError

log = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

NUMERIC_TYPES = {"NUMBER", "DECIMAL", "NUMERIC", "INT", "INTEGER", "BIGINT", "SMALLINT", "FLOAT", "DOUBLE", "REAL", "FIXED"}
DATE_TYPES = {"DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIMESTAMP", "DATETIME"}


@dataclass(frozen=True)
class ColumnInfo:
    table: str
    name: str
    data_type: str
    nullable: bool
    comment: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.table}.{self.name}"

    @property
    def is_numeric(self) -> bool:
        return self.data_type.upper() in NUMERIC_TYPES

    @property
    def is_date(self) -> bool:
        return self.data_type.upper() in DATE_TYPES


@dataclass
class TableInfo:
    name: str
    row_count: int | None
    comment: str
    columns: dict[str, ColumnInfo] = field(default_factory=dict)


@dataclass
class SchemaSnapshot:
    database: str
    schema: str
    tables: dict[str, TableInfo]
    categorical_values: dict[str, list[str]]
    fetched_at: float
    query_ids: list[str]

    def fqn(self, table: str) -> str:
        return f"{self.database}.{self.schema}.{table}"

    def all_column_names(self) -> set[str]:
        return {c for t in self.tables.values() for c in t.columns}

    def resolve_table(self, name: str) -> TableInfo:
        key = name.strip().strip('"').split(".")[-1].upper()
        if key in self.tables:
            return self.tables[key]
        close = difflib.get_close_matches(key, list(self.tables), n=3)
        hint = f" Did you mean {', '.join(close)}?" if close else ""
        raise SchemaMismatchError(f"Table '{name}' is not available in this dataset.{hint}", f"unknown table {name}")

    def resolve_column(self, ref: str, preferred_tables: list[str] | None = None) -> ColumnInfo:
        """Resolve 'TABLE.COLUMN' or bare 'COLUMN' against the discovered schema."""
        parts = [p.strip().strip('"').upper() for p in ref.split(".") if p.strip()]
        if not parts:
            raise SchemaMismatchError("An empty column reference was produced.", "empty column ref")
        col = parts[-1]
        if len(parts) >= 2:
            table = self.resolve_table(parts[-2])
            if col in table.columns:
                return table.columns[col]
            close = difflib.get_close_matches(col, list(table.columns), n=3)
            hint = f" Closest columns: {', '.join(close)}." if close else ""
            raise SchemaMismatchError(f"Column '{col}' does not exist in table {table.name}.{hint}",
                                      f"unknown column {ref}")
        owners = [t for t in self.tables.values() if col in t.columns]
        if preferred_tables:
            pref = [t for t in owners if t.name in {p.upper() for p in preferred_tables}]
            owners = pref or owners
        if len(owners) == 1:
            return owners[0].columns[col]
        if len(owners) > 1:
            raise SchemaMismatchError(
                f"Column '{col}' exists in several tables ({', '.join(t.name for t in owners)}); it must be qualified.",
                f"ambiguous column {ref}")
        close = difflib.get_close_matches(col, sorted(self.all_column_names()), n=3)
        hint = f" Closest columns: {', '.join(close)}." if close else ""
        raise SchemaMismatchError(f"I couldn't find a column named '{col}' in this dataset.{hint}",
                                  f"unknown column {ref}")


class SchemaService:
    def __init__(self, snowflake: SnowflakeService, dataset: DatasetConfig, ttl_s: int = 900):
        for ident in (dataset.database, dataset.schema, *dataset.tables):
            if not _IDENT_RE.match(ident):
                raise ConfigurationError(f"Invalid identifier in dataset config: {ident!r}")
        self.sf = snowflake
        self.dataset = dataset
        self.ttl_s = ttl_s
        self._snapshot: SchemaSnapshot | None = None

    def get(self, force_refresh: bool = False) -> SchemaSnapshot:
        if force_refresh or self._snapshot is None or time.time() - self._snapshot.fetched_at > self.ttl_s:
            self._snapshot = self._discover()
        return self._snapshot

    def _discover(self) -> SchemaSnapshot:
        db, sch = self.dataset.database, self.dataset.schema
        info = f"{db}.INFORMATION_SCHEMA"  # identifiers validated in __init__
        tables_res = self.sf.run_metadata(
            f"SELECT TABLE_NAME, ROW_COUNT, COMMENT FROM {info}.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW') ORDER BY TABLE_NAME",
            (sch,))
        cols_res = self.sf.run_metadata(
            f"SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT FROM {info}.COLUMNS "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION",
            (sch,), max_rows=20000)
        allow = set(self.dataset.tables)
        tables: dict[str, TableInfo] = {}
        for name, row_count, comment in tables_res.rows:
            if allow and name not in allow:
                continue
            tables[name] = TableInfo(name=name, row_count=row_count,
                                     comment=comment or self.dataset.table_descriptions.get(name, ""))
        for tname, cname, dtype, nullable, comment in cols_res.rows:
            if tname in tables:
                desc = comment or self.dataset.column_descriptions.get(f"{tname}.{cname}", "")
                tables[tname].columns[cname] = ColumnInfo(tname, cname, _base_type(dtype), nullable == "YES", desc)
        if not tables:
            raise SchemaMismatchError(
                f"No accessible tables found in {db}.{sch}. Check the dataset config and the role's privileges.",
                f"allowlist={sorted(allow)}")
        missing = allow - set(tables)
        if missing:
            log.warning("Allowlisted tables not visible to this role: %s", sorted(missing))
        snap = SchemaSnapshot(db, sch, tables, {}, time.time(),
                              [q for q in (tables_res.query_id, cols_res.query_id) if q])
        snap.categorical_values = self._categorical_values(snap)
        return snap

    def _categorical_values(self, snap: SchemaSnapshot) -> dict[str, list[str]]:
        """One bounded DISTINCT query per configured categorical column (cached with the schema)."""
        values: dict[str, list[str]] = {}
        for ref in self.dataset.categorical_columns:
            try:
                col = snap.resolve_column(ref)
            except SchemaMismatchError:
                log.warning("Configured categorical column %s not found; skipping", ref)
                continue
            res = self.sf.run_metadata(
                f'SELECT DISTINCT "{col.name}" FROM {snap.fqn(col.table)} '
                f'WHERE "{col.name}" IS NOT NULL ORDER BY 1 LIMIT 61', max_rows=61)
            vals = [str(r[0]) for r in res.rows]
            values[col.qualified] = vals[:60]
            if res.query_id:
                snap.query_ids.append(res.query_id)
        return values

    # -- prompt context -----------------------------------------------------
    def prompt_context(self, snap: SchemaSnapshot, question: str, max_tables: int = 12) -> str:
        """Compact schema description for Gemma: only allowlisted metadata, never row data beyond
        configured categorical values."""
        tables = _rank_tables(snap, question)[:max_tables]
        lines = [f"Dataset: {self.dataset.name}", self.dataset.description.strip(), "", "Tables:"]
        for t in tables:
            rc = f", ~{t.row_count:,} rows" if t.row_count is not None else ""
            lines.append(f"- {t.name}{rc}: {t.comment}")
            for c in t.columns.values():
                extra = ""
                vals = snap.categorical_values.get(c.qualified)
                if vals:
                    shown = ", ".join(vals[:25]) + (" ..." if len(vals) > 25 else "")
                    extra = f" values: [{shown}]"
                desc = f" -- {c.comment}" if c.comment else ""
                lines.append(f"    {c.name} {c.data_type}{desc}{extra}")
        if self.dataset.relationships:
            lines += ["", "Join relationships (many -> one), joins are added automatically:"]
            lines += [f"- {m} -> {o}" for m, o in self.dataset.relationships]
        if self.dataset.metrics:
            lines += ["", "Predefined metrics (use by name in measures[].metric):"]
            lines += [f"- {k}: {v.get('description', '')} = {v['expression']}" for k, v in self.dataset.metrics.items()]
        return "\n".join(lines)


def _base_type(dtype: str) -> str:
    return (dtype or "").split("(")[0].strip().upper()


def _rank_tables(snap: SchemaSnapshot, question: str) -> list[TableInfo]:
    """Order tables by lexical overlap with the question; keeps prompts small for wide schemas."""
    words = {w for w in re.findall(r"[a-z]+", question.lower()) if len(w) > 2}

    def score(t: TableInfo) -> int:
        text = (t.name + " " + t.comment + " " + " ".join(t.columns)).lower()
        return sum(1 for w in words if w in text or w.rstrip("s") in text)

    return sorted(snap.tables.values(), key=lambda t: -score(t))
