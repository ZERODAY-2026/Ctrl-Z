"""Evidence objects, deterministic summaries, and numeric grounding checks.

A Gemma explanation is shown only if every number in it can be matched to a value
in the returned rows, the query metadata, or the question. Otherwise the
deterministic summary is shown instead and the rejected claims are recorded.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.services.snowflake_service import QueryResult

PROMPT_ROWS = 30

_NUM_RE = re.compile(
    r"(?<![\w#.])([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|percent|per cent|million|billion|thousand|bn|k\b|m\b)?",
    re.IGNORECASE)
_SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3, "k": 1e3}


@dataclass
class Evidence:
    question: str
    sql: str
    sql_origin: str  # "compiled_from_plan" or "gemma_sql_validated"
    query_id: str | None
    status: str
    tables: list[str]
    columns_used: list[str]
    joins: list[str]
    filters: list[str]
    groupings: list[str]
    calculations: list[str]
    steps: list[str]
    result_columns: list[str]
    result_types: list[str]
    rows: list[tuple[Any, ...]]
    fetched_rows: int
    total_rows: int | None
    truncated: bool
    elapsed_ms: int
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)

    def as_prompt_text(self) -> str:
        """Bounded, data-only view of the result for the explanation model."""
        lines = ["How the result was computed:"] + [f"- {s}" for s in self.steps]
        if self.limitations:
            lines += ["Caveats:"] + [f"- {s}" for s in self.limitations]
        lines.append(f"Result columns: {', '.join(f'{c} ({t})' for c, t in zip(self.result_columns, self.result_types))}")
        lines.append(f"Rows returned: {self.fetched_rows}" + (" (TRUNCATED: more rows exist)" if self.truncated else ""))
        if self.rows:
            lines.append("Rows (pipe-separated):")
            lines.append(" | ".join(self.result_columns))
            for r in self.rows[:PROMPT_ROWS]:
                lines.append(" | ".join(_fmt(v) for v in r))
            if self.fetched_rows > PROMPT_ROWS:
                lines.append(f"... {self.fetched_rows - PROMPT_ROWS} more rows not shown here; do not describe them.")
        return "\n".join(lines)


def is_identifier_column(name: str) -> bool:
    """Numeric key/ID columns (e.g. C_CUSTKEY, ORDER_ID) identify rows; they are not measures."""
    n = name.upper()
    return n.endswith("KEY") or n.endswith("_ID") or n == "ID"


def _fmt(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return str(v)


def fmt_number(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        return _fmt(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, Decimal):
        return f"{v:,.2f}"
    if math.isnan(v):
        return "NaN"
    return f"{v:,.2f}"


def build_evidence(question: str, result: QueryResult, *, sql_origin: str, tables: list[str], columns_used: list[str],
                   joins: list[str], filters: list[str], groupings: list[str], calculations: list[str],
                   steps: list[str], assumptions: list[str], warnings: list[str]) -> Evidence:
    limitations = list(warnings)
    if result.truncated:
        limitations.append(f"Only the first {result.fetched_rows} rows were fetched; the answer describes those rows only.")
    null_cols = [c for i, c in enumerate(result.columns) if any(r[i] is None for r in result.rows)]
    if null_cols:
        limitations.append(f"Some returned values are NULL (missing) in: {', '.join(null_cols)}. NULLs are not treated as zero.")
    return Evidence(
        question=question, sql=result.sql, sql_origin=sql_origin, query_id=result.query_id, status=result.status,
        tables=tables, columns_used=columns_used, joins=joins, filters=filters, groupings=groupings,
        calculations=calculations, steps=steps, result_columns=result.columns, result_types=result.column_types,
        rows=result.rows, fetched_rows=result.fetched_rows, total_rows=result.total_rows, truncated=result.truncated,
        elapsed_ms=result.elapsed_ms, assumptions=assumptions, limitations=limitations,
    )


# ---------------------------------------------------------------------- deterministic summary
def deterministic_summary(ev: Evidence) -> str:
    if ev.fetched_rows == 0:
        return ("The query ran successfully in Snowflake but returned no matching rows. "
                "This means no records satisfied the filters shown below; it does not prove the underlying event never happened "
                "outside this dataset.")
    cols = ev.result_columns
    if ev.fetched_rows == 1:
        parts = [f"{c} = {fmt_number(v)}" for c, v in zip(cols, ev.rows[0])]
        return "Result: " + "; ".join(parts) + "."
    num_idx = [i for i, t in enumerate(ev.result_types) if t in ("FIXED", "REAL", "DECFLOAT")
               and all(isinstance(r[i], (int, float, Decimal)) or r[i] is None for r in ev.rows)
               and not is_identifier_column(cols[i])]
    label_idx = [i for i in range(len(cols)) if i not in num_idx]
    scope = f"{ev.fetched_rows} rows were returned" + (" (truncated)" if ev.truncated else "")
    if num_idx and label_idx:
        m, lab = num_idx[-1], label_idx[0]
        valid = [r for r in ev.rows if r[m] is not None]
        if valid:
            hi = max(valid, key=lambda r: r[m])
            lo = min(valid, key=lambda r: r[m])
            return (f"{scope}. Highest {cols[m]}: {_fmt(hi[lab])} ({fmt_number(hi[m])}); "
                    f"lowest: {_fmt(lo[lab])} ({fmt_number(lo[m])}).")
    return f"{scope}. See the table for the full result."


# ---------------------------------------------------------------------- grounding check
def _numbers_in_text(text: str) -> list[tuple[str, float, str | None]]:
    out = []
    for m in _NUM_RE.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").lower() or None
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        out.append((m.group(0).strip(), val, unit))
    return out


def _allowed_values(ev: Evidence) -> list[float]:
    vals: list[float] = [float(ev.fetched_rows)]
    if ev.total_rows is not None:
        vals.append(float(ev.total_rows))
    vals += [float(i) for i in range(1, ev.fetched_rows + 1)]  # ranks / positions ("top 3")
    for r in ev.rows:
        for v in r:
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float, Decimal)):
                vals.append(float(v))
            elif isinstance(v, (dt.date, dt.datetime)):
                vals += [float(v.year), float(v.month), float(v.day)]
            else:
                vals += [n for _, n, _ in _numbers_in_text(str(v))]
    for text in [ev.question, ev.sql, *ev.filters, *ev.result_columns]:
        vals += [n for _, n, _ in _numbers_in_text(text)]
    return vals


def _matches(claim: float, decimals: int, allowed: list[float]) -> bool:
    # Tolerance is the rounding implied by how the claim is written, nothing more: with values as close as
    # 45.50B vs 45.48B a relative tolerance would let a number be attributed to the wrong row.
    half_unit = 0.5 * 10 ** (-decimals)
    for v in allowed:
        tol = max(half_unit, abs(v) * 1e-12, 1e-9)
        if abs(claim - v) <= tol:
            return True
    return False


def unverified_numbers(text: str, ev: Evidence) -> list[str]:
    """Numbers in `text` that cannot be traced to the evidence (empty list = fully grounded)."""
    allowed = _allowed_values(ev)
    bad = []
    for raw, val, unit in _numbers_in_text(text):
        digits = raw.split()[0].replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        candidates = [val]
        if unit in _SCALE:
            candidates = [val * _SCALE[unit]]
            decimals = -int(math.log10(_SCALE[unit])) + decimals  # "1.2 million" is precise to 0.1 million
        elif unit in ("%", "percent", "per cent"):
            candidates = [val, val / 100]
        if not any(_matches(c, decimals if c == candidates[0] else decimals + 2, allowed) for c in candidates):
            bad.append(raw)
    return bad


_LABEL_RE = re.compile(r"\b[A-Za-z][A-Za-z_-]*#[A-Za-z0-9_-]+")


def unverified_labels(text: str, ev: Evidence) -> list[str]:
    """Identifier-like labels in `text` (e.g. Customer#000003451, Brand#13) that do not appear verbatim
    in any returned text value. Catches a model rewriting or inventing row labels."""
    cells = {str(v) for r in ev.rows for v in r if isinstance(v, str)}
    bad = []
    for m in _LABEL_RE.finditer(text):
        label = m.group(0)
        if label not in cells and not any(label in c for c in cells):
            bad.append(label)
    return bad
