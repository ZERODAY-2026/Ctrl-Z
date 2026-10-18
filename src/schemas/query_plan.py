"""Structured query plan produced by Gemma and validated before any SQL is built.

The plan is deliberately small: it can express filters, joins (via configured
relationships), grouping, time buckets, aggregations, share-of-total, HAVING,
ordering and limits. Anything beyond that goes through the `advanced_sql` intent,
whose SQL is still checked by the SQL guard.
"""

from __future__ import annotations

from enum import Enum
from typing import Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Scalar = Union[str, int, float, bool]


class Intent(str, Enum):
    AGGREGATE = "aggregate"        # counts, sums, averages, rankings, grouped comparisons
    LIST = "list"                  # show individual records
    ADVANCED_SQL = "advanced_sql"  # needs SQL beyond this plan (window functions, nested comparisons)
    CLARIFY = "clarify"            # ambiguous: ask the user
    UNSUPPORTED = "unsupported"    # data not available or not a read-only analytics request


class AggFunc(str, Enum):
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    MEDIAN = "MEDIAN"


class TimeGrain(str, Enum):
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"


class FilterOp(str, Enum):
    EQ = "="
    NE = "!="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class Direction(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Measure(_Strict):
    """Either `function` + `column`, or a named `metric` from the dataset config."""

    alias: str = Field(description="snake_case output name, e.g. total_revenue")
    function: AggFunc | None = None
    column: str | None = Field(default=None, description="TABLE.COLUMN, or * for COUNT")
    metric: str | None = Field(default=None, description="Name of a predefined metric")
    share_of_total: bool = Field(default=False, description="Return percentage of the grand total instead")

    @field_validator("alias")
    @classmethod
    def _alias_ok(cls, v: str) -> str:
        v = v.strip().lower().replace(" ", "_")
        if not v.replace("_", "").isalnum() or not v[0].isalpha() or len(v) > 60:
            raise ValueError(f"invalid alias '{v}'")
        return v

    @model_validator(mode="after")
    def _one_source(self) -> "Measure":
        if self.metric and (self.function or self.column):
            raise ValueError("measure must use either a metric or function+column, not both")
        if not self.metric:
            if self.function is None or not self.column:
                raise ValueError("measure needs function and column (or a metric)")
            if self.column == "*" and self.function != AggFunc.COUNT:
                raise ValueError("'*' is only valid with COUNT")
        return self


class Dimension(_Strict):
    column: str = Field(description="TABLE.COLUMN to group by")
    time_grain: TimeGrain | None = Field(default=None, description="Bucket a date column")
    alias: str | None = None


class Filter(_Strict):
    column: str = Field(description="TABLE.COLUMN")
    op: FilterOp
    value: Scalar | list[Scalar] | None = None
    time_part: TimeGrain | None = Field(default=None, description="Compare YEAR/MONTH/... of a date column")

    @model_validator(mode="after")
    def _value_shape(self) -> "Filter":
        if self.op in (FilterOp.IS_NULL, FilterOp.IS_NOT_NULL):
            self.value = None
        elif self.op in (FilterOp.IN, FilterOp.NOT_IN):
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"{self.op.value} needs a non-empty list value")
        elif self.op == FilterOp.BETWEEN:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("between needs a two-element list")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError(f"{self.op.value} needs a single value")
        return self


class Having(_Strict):
    measure_alias: str
    op: FilterOp
    value: float

    @field_validator("op")
    @classmethod
    def _comparison_only(cls, v: FilterOp) -> FilterOp:
        if v not in (FilterOp.EQ, FilterOp.NE, FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE):
            raise ValueError("HAVING supports comparison operators only")
        return v


class OrderBy(_Strict):
    field: str = Field(description="A measure alias, dimension alias or TABLE.COLUMN")
    direction: Direction = Direction.DESC


class QueryPlan(_Strict):
    intent: Intent
    tables: list[str] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list, description="Columns to show for intent=list")
    filters: list[Filter] = Field(default_factory=list)
    having: list[Having] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=1000)
    assumptions: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    unsupported_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _columns_to_dimensions(cls, data):
        """Small models often put group-by columns in `columns` for aggregates; move them deterministically."""
        if isinstance(data, dict) and data.get("intent") == "aggregate" and data.get("columns"):
            cols = [c for c in data["columns"] if isinstance(c, str) and "[" not in c and "." in c]
            dims = list(data.get("dimensions") or [])
            have = {d.get("column") for d in dims if isinstance(d, dict)}
            dims += [{"column": c} for c in cols if c not in have]
            data = {**data, "dimensions": dims, "columns": []}
        return data

    @model_validator(mode="after")
    def _coherent(self) -> "QueryPlan":
        if self.intent == Intent.CLARIFY and not self.clarification_question:
            raise ValueError("intent=clarify requires clarification_question")
        if self.intent == Intent.UNSUPPORTED and not self.unsupported_reason:
            raise ValueError("intent=unsupported requires unsupported_reason")
        if self.intent == Intent.AGGREGATE and not self.measures:
            raise ValueError("intent=aggregate requires at least one measure")
        if self.intent == Intent.LIST and not self.columns:
            raise ValueError("intent=list requires columns")
        aliases = [m.alias for m in self.measures]
        if len(aliases) != len(set(aliases)):
            raise ValueError("measure aliases must be unique")
        for h in self.having:
            if h.measure_alias.lower() not in aliases:
                raise ValueError(f"having references unknown measure alias '{h.measure_alias}'")
        return self


class SqlDraft(_Strict):
    """Output of the advanced path: Gemma-authored SQL plus its own account of what it does."""

    sql: str
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
