"""Deterministic compiler: validated QueryPlan + discovered schema -> Snowflake SQL.

Gemma never writes SQL on this path. Every identifier is resolved against the
discovered schema; every value becomes a typed, escaped literal; joins come from
configured relationships only.
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from config.settings import DatasetConfig
from src.schemas.query_plan import AggFunc, Direction, FilterOp, Intent, Measure, QueryPlan, TimeGrain
from src.services.schema_service import ColumnInfo, SchemaSnapshot
from src.utils.errors import QueryValidationError, SchemaMismatchError

_SAFE_IDENT = re.compile(r"^[A-Z_][A-Z0-9_$]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")
DEFAULT_LIST_LIMIT = 50


class ClarificationNeeded(Exception):
    """Raised when deterministic checks find the request ambiguous (e.g. unknown filter value)."""

    def __init__(self, question: str):
        super().__init__(question)
        self.question = question


@dataclass
class CompiledQuery:
    sql: str
    tables: list[str]
    columns: list[str]
    joins: list[str]
    filters: list[str]
    groupings: list[str]
    calculations: list[str]
    steps: list[str]
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tie_probe: tuple[int, str] | None = None  # (rows to show, sort column) when one extra row was fetched


def ident(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=not bool(_SAFE_IDENT.match(name)))


def col_expr(c: ColumnInfo) -> exp.Column:
    return exp.Column(this=ident(c.name), table=ident(c.table))


def _agg(func: AggFunc, arg: exp.Expression) -> exp.Expression:
    if func == AggFunc.COUNT:
        return exp.Count(this=arg)
    if func == AggFunc.COUNT_DISTINCT:
        return exp.Count(this=exp.Distinct(expressions=[arg]))
    if func == AggFunc.SUM:
        return exp.Sum(this=arg)
    if func == AggFunc.AVG:
        return exp.Avg(this=arg)
    if func == AggFunc.MIN:
        return exp.Min(this=arg)
    if func == AggFunc.MAX:
        return exp.Max(this=arg)
    return exp.Anonymous(this="MEDIAN", expressions=[arg])


_GRAIN_FUNC = {TimeGrain.YEAR: "YEAR", TimeGrain.QUARTER: "QUARTER", TimeGrain.MONTH: "MONTH",
               TimeGrain.WEEK: "WEEK", TimeGrain.DAY: "DAY"}


class SqlCompiler:
    def __init__(self, dataset: DatasetConfig):
        self.dataset = dataset

    # ---------------------------------------------------------------- public
    def compile(self, plan: QueryPlan, snap: SchemaSnapshot, max_rows: int) -> CompiledQuery:
        if plan.intent not in (Intent.AGGREGATE, Intent.LIST):
            raise QueryValidationError("This plan cannot be compiled into SQL.", f"intent={plan.intent}")
        self.snap = snap
        self.pref = [t.upper() for t in plan.tables]
        self.used_tables: list[str] = []
        self.used_cols: list[str] = []
        steps: list[str] = []
        calcs: list[str] = []
        assumptions: list[str] = []
        warnings: list[str] = []

        select: list[exp.Expression] = []
        group: list[exp.Expression] = []
        alias_exprs: dict[str, exp.Expression] = {}
        groupings: list[str] = []
        time_dims: list[str] = []

        tiebreak: list[str] = []  # dimension aliases used to order tied rows deterministically
        # dimensions
        for d in plan.dimensions:
            c = self._col(d.column)
            if d.time_grain:
                if not c.is_date:
                    raise SchemaMismatchError(f"{c.qualified} is not a date column, so it can't be grouped by {d.time_grain.value}.")
                e: exp.Expression = (exp.Anonymous(this="YEAR", expressions=[col_expr(c)]) if d.time_grain == TimeGrain.YEAR
                                     else exp.Anonymous(this="DATE_TRUNC", expressions=[exp.Literal.string(d.time_grain.value), col_expr(c)]))
                alias = (d.alias or f"{c.name}_{d.time_grain.value}").lower()
                groupings.append(f"{c.qualified} by {d.time_grain.value}")
                time_dims.append(alias)
            else:
                e = col_expr(c)
                alias = (d.alias or c.name).lower()
                groupings.append(c.qualified)
            alias = _safe_alias(alias)
            select.append(exp.alias_(e, alias))
            group.append(e.copy())
            alias_exprs[alias] = e
            tiebreak.append(alias)
            # Group by the entity's stable key too, so two entities sharing a display name never merge and
            # every row carries an unambiguous identifier.
            key_ref = self.dataset.label_keys.get(c.qualified) if not d.time_grain else None
            if key_ref:
                k = self._col(key_ref)
                k_alias = _safe_alias(k.name)
                if k_alias not in alias_exprs:
                    select.append(exp.alias_(col_expr(k), k_alias))
                    group.append(col_expr(k))
                    alias_exprs[k_alias] = col_expr(k)
                    tiebreak.insert(len(tiebreak) - 1, k_alias)

        # measures
        measure_tables: list[str] = []
        measure_meta: list[tuple[str, AggFunc | None, list[str]]] = []
        for m in plan.measures:
            e, desc, tables, func = self._measure(m)
            measure_tables.extend(tables)
            if m.share_of_total:
                e = exp.Round(this=exp.Div(this=exp.Mul(this=e.copy(), expression=exp.Literal.number(100)),
                                           expression=exp.Window(this=exp.Sum(this=e.copy()))),
                              decimals=exp.Literal.number(4))
                desc = f"{desc} as a percentage of the total across all returned groups (denominator = grand total)"
            select.append(exp.alias_(e, m.alias))
            alias_exprs[m.alias] = e
            calcs.append(f"{m.alias} = {desc}")
            measure_meta.append((m.alias, func, tables))

        # list columns
        if plan.intent == Intent.LIST:
            for ref in plan.columns:
                c = self._col(ref)
                select.append(col_expr(c))
                alias_exprs[c.name.lower()] = col_expr(c)

        # filters (resolved before joins so their tables are included)
        where: list[exp.Expression] = []
        filter_text: list[str] = []
        for f in plan.filters:
            cond, text, note = self._filter(f)
            where.append(cond)
            filter_text.append(text)
            if note:
                assumptions.append(note)

        # tables + joins
        base = (measure_tables[0] if measure_tables else (self.pref[0] if self.pref and self.pref[0] in self.used_tables
                                                           else self.used_tables[0]))
        required = list(dict.fromkeys([base, *self.used_tables, *[t for t in self.pref if t in snap.tables]]))
        join_edges, join_notes = self._join_path(base, required)
        assumptions.extend(join_notes)
        _fanout_warnings(measure_meta, join_edges, warnings)

        q = exp.select(*select).from_(exp.table_(ident(base), db=ident(snap.schema), catalog=ident(snap.database),
                                                 alias=ident(base)))
        steps.append(f"Read from table {base} ({snap.fqn(base)}).")
        joins_text: list[str] = []
        for new_t, new_c, old_t, old_c, _, _ in join_edges:
            on = exp.EQ(this=exp.Column(this=ident(new_c), table=ident(new_t)),
                        expression=exp.Column(this=ident(old_c), table=ident(old_t)))
            q = q.join(exp.table_(ident(new_t), db=ident(snap.schema), catalog=ident(snap.database), alias=ident(new_t)),
                       on=on, join_type="inner")
            joins_text.append(f"{old_t}.{old_c} = {new_t}.{new_c}")
            steps.append(f"Joined {new_t} on {old_t}.{old_c} = {new_t}.{new_c} (inner join: rows without a match are excluded).")
        if where:
            q = q.where(exp.and_(*where))
            steps.append("Kept only rows where " + " AND ".join(filter_text) + ".")
        if group and plan.intent == Intent.AGGREGATE:
            q = q.group_by(*group)
            steps.append("Grouped rows by " + ", ".join(groupings) + ".")
        if calcs:
            steps.append("Computed " + "; ".join(calcs) + ".")
        for h in plan.having:
            target = alias_exprs[h.measure_alias.lower()].copy()
            q = q.having(_compare(target, h.op, exp.Literal.number(h.value)))
            steps.append(f"Kept only groups where {h.measure_alias} {h.op.value} {h.value}.")

        # ordering
        order: list[exp.Expression] = []
        order_text: list[str] = []
        for o in plan.order_by:
            key = o.field.strip().lower()
            if key in alias_exprs:
                target: exp.Expression = exp.column(ident(key) if not _SAFE_IDENT.match(key.upper()) else key)
            else:
                c = self._col(o.field)
                if plan.intent == Intent.AGGREGATE and c.qualified not in groupings:
                    raise QueryValidationError(f"Cannot sort by {c.qualified}: it is not part of the grouped result.")
                target = col_expr(c)
            order.append(exp.Ordered(this=target, desc=o.direction == Direction.DESC, nulls_first=False))
            order_text.append(f"{o.field} {'descending' if o.direction == Direction.DESC else 'ascending'}")
        if not order and plan.intent == Intent.AGGREGATE and group:
            if time_dims:
                order = [exp.Ordered(this=exp.column(a), desc=False) for a in time_dims]
                order_text = [f"{a} ascending" for a in time_dims]
            elif plan.measures:
                first = plan.measures[0].alias
                order = [exp.Ordered(this=exp.column(first), desc=True, nulls_first=False)]
                order_text = [f"{first} descending"]
                assumptions.append(f"No sort order was given; results are sorted by {first}, highest first.")
        if order and plan.intent == Intent.AGGREGATE:
            # Deterministic tie-break: rows with equal sort values are ordered by their key/label, so LIMIT
            # always returns the same rows and rank positions are reproducible.
            used = {o.this.name.lower() for o in order if isinstance(o.this, exp.Column)}
            extra = [a for a in tiebreak if a not in used]
            if extra:
                order += [exp.Ordered(this=exp.column(a), desc=False) for a in extra]
                order_text.append("ties broken by " + ", ".join(f"{a} ascending" for a in extra))
        if order:
            q = q.order_by(*order)
        limit = plan.limit or (DEFAULT_LIST_LIMIT if plan.intent == Intent.LIST else max_rows)
        if limit > max_rows:
            warnings.append(f"Requested {limit} rows; DataMind returns at most {max_rows}.")
            limit = max_rows
        tie_probe = None
        if plan.intent == Intent.AGGREGATE and plan.limit and order and limit < max_rows                 and isinstance(order[0].this, exp.Column):
            # Fetch one extra row so a tie straddling the "top N" cut-off can be detected and disclosed.
            tie_probe = (limit, order[0].this.name)
            q = q.limit(limit + 1)
        else:
            q = q.limit(limit)
        if order_text:
            steps.append("Sorted by " + ", ".join(order_text) + f" and kept at most {limit} rows.")
        if tie_probe:
            steps.append(f"The SQL fetches {limit + 1} rows (one extra) only to check for a tie at the cut-off; "
                         f"the first {limit} are shown.")
        elif plan.intent == Intent.LIST:
            steps.append(f"Returned at most {limit} rows (no particular order).")
            if not plan.limit:
                assumptions.append(f"No row count was given; showing up to {limit} records.")

        return CompiledQuery(
            sql=q.sql(dialect="snowflake", pretty=True),
            tables=[snap.fqn(t) for t in dict.fromkeys([base, *[e[0] for e in join_edges]])],
            columns=list(dict.fromkeys(self.used_cols)),
            joins=joins_text,
            filters=filter_text,
            groupings=groupings,
            calculations=calcs,
            steps=steps,
            assumptions=list(dict.fromkeys([*plan.assumptions, *assumptions])),
            warnings=warnings,
            tie_probe=tie_probe,
        )

    # ---------------------------------------------------------------- helpers
    def _col(self, ref: str) -> ColumnInfo:
        c = self.snap.resolve_column(ref, self.pref)
        if c.table not in self.used_tables:
            self.used_tables.append(c.table)
        self.used_cols.append(c.qualified)
        return c

    def _metric_for_expression(self, m: Measure) -> str | None:
        """Name of the configured metric whose formula Gemma wrote into `column` instead of naming it."""
        if not m.column or m.column.strip() == "*" or m.function is None:
            return None
        func = "COUNT(DISTINCT {})" if m.function == AggFunc.COUNT_DISTINCT else f"{m.function.value}({{}})"
        try:
            candidates = {_norm_sql(m.column), _norm_sql(func.format(m.column))}
        except sqlglot.errors.ParseError:
            return None
        for name, spec in self.dataset.metrics.items():
            if _norm_sql(spec["expression"]) in candidates:
                return name
        return None

    def _measure(self, m: Measure) -> tuple[exp.Expression, str, list[str], AggFunc | None]:
        if not m.metric and (name := self._metric_for_expression(m)):
            m = m.model_copy(update={"metric": name, "function": None, "column": None})
        if m.metric:
            spec = self.dataset.metrics.get(m.metric.lower())
            if spec is None:
                known = ", ".join(self.dataset.metrics) or "none"
                raise SchemaMismatchError(f"There is no predefined metric called '{m.metric}'. Known metrics: {known}.")
            tree = sqlglot.parse_one(spec["expression"], read="snowflake")
            tables: list[str] = []
            for c in list(tree.find_all(exp.Column)):
                info = self._col(f"{c.table}.{c.name}" if c.table else c.name)
                c.replace(col_expr(info))
                tables.append(info.table)
            return tree, f"{spec['expression']} ({spec.get('description', m.metric)})", tables, None
        assert m.function is not None and m.column is not None
        if m.column.strip() == "*":
            table = self.pref[0] if self.pref else None
            if table:
                self.snap.resolve_table(table)
                if table not in self.used_tables:
                    self.used_tables.append(table)
            return exp.Count(this=exp.Star()), "COUNT(*) (number of rows)", ([table] if table else []), m.function
        c = self._col(m.column)
        if m.function in (AggFunc.SUM, AggFunc.AVG, AggFunc.MEDIAN) and not c.is_numeric:
            raise SchemaMismatchError(f"{c.qualified} is a {c.data_type} column, so {m.function.value} is not meaningful.")
        e = _agg(m.function, col_expr(c))
        label = {AggFunc.COUNT_DISTINCT: f"COUNT(DISTINCT {c.qualified})"}.get(m.function, f"{m.function.value}({c.qualified})")
        if m.function == AggFunc.AVG:
            label += " (NULLs ignored)"
        return e, label, [c.table], m.function

    def _filter(self, f) -> tuple[exp.Expression, str, str | None]:
        c = self._col(f.column)
        target: exp.Expression = col_expr(c)
        label = c.qualified
        note = None
        value = f.value
        part = f.time_part
        # "O_ORDERDATE = 1995" almost always means the calendar year.
        if c.is_date and part is None and f.op not in (FilterOp.IS_NULL, FilterOp.IS_NOT_NULL):
            vals = value if isinstance(value, list) else [value]
            if all(_is_year(v) for v in vals):
                part = TimeGrain.YEAR
                note = f"Interpreted {vals[0]} as the calendar year of {c.qualified}."
        if part:
            if not c.is_date:
                raise SchemaMismatchError(f"{c.qualified} is not a date column.")
            target = exp.Anonymous(this=_GRAIN_FUNC[part], expressions=[target])
            label = f"{_GRAIN_FUNC[part]}({c.qualified})"
        if f.op in (FilterOp.IS_NULL, FilterOp.IS_NOT_NULL):
            cond = exp.Is(this=target, expression=exp.Null())
            if f.op == FilterOp.IS_NOT_NULL:
                cond = exp.Not(this=cond)
            return cond, f"{label} is {'not ' if f.op == FilterOp.IS_NOT_NULL else ''}missing (NULL)", note

        is_date_value = c.is_date and part is None
        numeric = c.is_numeric or part is not None
        vals = value if isinstance(value, list) else [value]
        if c.qualified in self.snap.categorical_values and f.op in (FilterOp.EQ, FilterOp.NE, FilterOp.IN, FilterOp.NOT_IN):
            vals = [self._match_category(c, v) for v in vals]
        lits = [_literal(v, numeric=numeric, is_date=is_date_value, col=c.qualified) for v in vals]
        shown = ", ".join(repr(v) if isinstance(v, str) else str(v) for v in vals)

        if f.op in (FilterOp.CONTAINS, FilterOp.STARTS_WITH):
            if numeric or is_date_value:
                raise QueryValidationError(f"Text matching is not valid on {c.qualified}.")
            raw = str(vals[0]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{raw}%" if f.op == FilterOp.CONTAINS else f"{raw}%"
            cond = exp.ILike(this=target, expression=exp.Literal.string(pattern))
            return cond, f"{label} {'contains' if f.op == FilterOp.CONTAINS else 'starts with'} '{vals[0]}' (case-insensitive)", note
        if f.op == FilterOp.BETWEEN:
            return (exp.Between(this=target, low=lits[0], high=lits[1]),
                    f"{label} between {vals[0]} and {vals[1]} (inclusive)", note)
        if f.op in (FilterOp.IN, FilterOp.NOT_IN):
            cond = exp.In(this=target, expressions=lits)
            if f.op == FilterOp.NOT_IN:
                cond = exp.Not(this=cond)
            return cond, f"{label} {'not ' if f.op == FilterOp.NOT_IN else ''}in ({shown})", note
        return _compare(target, f.op, lits[0]), f"{label} {f.op.value} {shown}", note

    def _match_category(self, c: ColumnInfo, v: Any) -> Any:
        known = self.snap.categorical_values[c.qualified]
        if str(v) in known:
            return v
        for k in known:
            if k.lower() == str(v).strip().lower():
                return k
        sample = ", ".join(known[:15]) + (" ..." if len(known) > 15 else "")
        raise ClarificationNeeded(f"'{v}' is not a value of {c.qualified}. Known values are: {sample}. Which one did you mean?")

    def _join_path(self, base: str, required: list[str]):
        """Connect required tables with configured many->one relationships.

        Greedy Steiner-tree approximation: Dijkstra from the connected set where passing
        through a table the question did not mention costs more, and shared dimensions
        (tables referenced by several others, e.g. NATION) are connected last so they
        attach through a table the question did mention.
        """
        graph: dict[str, list[tuple[str, str, str, str, str]]] = {}
        indegree: dict[str, int] = {}
        for many, one in self.dataset.relationships:
            mt, mc = many.split(".")
            ot, oc = one.split(".")
            if mt not in self.snap.tables or ot not in self.snap.tables:
                continue
            graph.setdefault(mt, []).append((ot, oc, mc, mt, ot))
            graph.setdefault(ot, []).append((mt, mc, oc, mt, ot))
            indegree[ot] = indegree.get(ot, 0) + 1
        req = set(required)
        connected = {base}
        edges: list[tuple[str, str, str, str, str, str]] = []
        notes: list[str] = []
        while req - connected:
            dist: dict[str, int] = {n: 0 for n in connected}
            prev: dict[str, tuple[str, str, str, str, str]] = {}
            heap = [(0, n) for n in connected]
            heapq.heapify(heap)
            while heap:
                d, n = heapq.heappop(heap)
                if d > dist.get(n, 1 << 30):
                    continue
                for nxt, nxt_col, cur_col, mt, ot in graph.get(n, []):
                    nd = d + (1 if nxt in req else 2)
                    if nd < dist.get(nxt, 1 << 30):
                        dist[nxt] = nd
                        prev[nxt] = (n, cur_col, nxt_col, mt, ot)
                        heapq.heappush(heap, (nd, nxt))
            candidates = [t for t in req - connected if t in dist]
            if not candidates:
                missing = ", ".join(sorted(req - connected))
                raise SchemaMismatchError(f"There is no configured join path between {base} and {missing}.")
            target = min(candidates, key=lambda t: (dist[t], indegree.get(t, 0), t))
            path = []
            node = target
            while node not in connected:
                p, cur_col, nxt_col, mt, ot = prev[node]
                path.append((node, nxt_col, p, cur_col, mt, ot))
                node = p
            for step in reversed(path):
                edges.append(step)
                connected.add(step[0])
            # Disclose the choice when a shared dimension could have been reached another way.
            new_t, _, via_t, _, _, _ = path[0]
            if indegree.get(new_t, 0) > 1:
                others = sorted({m.split(".")[0] for m, o in self.dataset.relationships if o.split(".")[0] == new_t}
                                - {via_t})
                others = [o for o in others if o in self.snap.tables and o not in req]
                if others:
                    notes.append(f"{new_t} was linked through {via_t}; it could also relate to {', '.join(others)}. "
                                 f"Ask explicitly (e.g. 'customer nation' vs 'supplier nation') if you meant another link.")
        return edges, notes


def _fanout_warnings(measure_meta, edges, warnings: list[str]) -> None:
    """Warn when a measure's rows are duplicated by joining a table on the 'many' side."""
    for alias, func, tables in measure_meta:
        if func in (AggFunc.COUNT_DISTINCT, AggFunc.MIN, AggFunc.MAX):
            continue
        for t in tables:
            multiplied_by = [mt for (_, _, _, _, mt, ot) in edges if ot == t]
            if multiplied_by:
                warnings.append(
                    f"{alias} aggregates {t} after joining {', '.join(multiplied_by)} (one {t} row per matching "
                    f"{multiplied_by[0]} row), so the value may be inflated. Treat it with caution.")


def _norm_sql(sql: str) -> str:
    return re.sub(r"\s+", "", sqlglot.parse_one(sql, read="snowflake").sql(dialect="snowflake").upper())


def _safe_alias(a: str) -> str:
    a = re.sub(r"[^a-z0-9_]", "_", a.lower())
    return a if a and a[0].isalpha() else f"c_{a}"


def _is_year(v: Any) -> bool:
    try:
        return isinstance(v, (int, str)) and not isinstance(v, bool) and 1900 <= int(str(v)) <= 2100 and len(str(v).strip()) == 4
    except ValueError:
        return False


def _literal(v: Any, *, numeric: bool, is_date: bool, col: str) -> exp.Expression:
    if numeric:
        if isinstance(v, bool):
            raise QueryValidationError(f"Expected a number for {col}, got {v}.")
        try:
            num = float(str(v).replace(",", ""))
        except ValueError as exc:
            raise QueryValidationError(f"Expected a number for {col}, got '{v}'.") from exc
        return exp.Literal.number(int(num) if num.is_integer() else num)
    if is_date:
        s = str(v).strip()
        if not _DATE_RE.match(s):
            raise QueryValidationError(f"Expected a date like 1995-01-31 for {col}, got '{v}'.")
        return exp.cast(exp.Literal.string(s), "DATE")
    return exp.Literal.string(str(v))


def _compare(target: exp.Expression, op: FilterOp, lit: exp.Expression) -> exp.Expression:
    cls = {FilterOp.EQ: exp.EQ, FilterOp.NE: exp.NEQ, FilterOp.GT: exp.GT, FilterOp.GTE: exp.GTE,
           FilterOp.LT: exp.LT, FilterOp.LTE: exp.LTE}.get(op)
    if cls is None:
        raise QueryValidationError(f"Operator {op.value} is not valid here.")
    return cls(this=target, expression=lit)
