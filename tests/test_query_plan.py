import pytest
from pydantic import ValidationError

from src.schemas.query_plan import Intent, QueryPlan
from src.security.sql_guard import validate
from src.services.sql_service import ClarificationNeeded, SqlCompiler
from src.utils.errors import QueryValidationError, SchemaMismatchError


def plan(**kw):
    return QueryPlan.model_validate(kw)


# ---------------------------------------------------------------- schema validation
def test_rejects_extra_fields():
    with pytest.raises(ValidationError):
        plan(intent="aggregate", measures=[{"alias": "n", "function": "COUNT", "column": "*"}], drop_table=True)


def test_rejects_unknown_aggregation():
    with pytest.raises(ValidationError):
        plan(intent="aggregate", measures=[{"alias": "x", "function": "STDDEV", "column": "ORDERS.O_TOTALPRICE"}])


def test_aggregate_requires_measure_and_clarify_requires_question():
    with pytest.raises(ValidationError):
        plan(intent="aggregate")
    with pytest.raises(ValidationError):
        plan(intent="clarify")
    with pytest.raises(ValidationError):
        plan(intent="unsupported")


def test_filter_value_shapes():
    with pytest.raises(ValidationError):
        plan(intent="list", columns=["ORDERS.O_ORDERKEY"], filters=[{"column": "ORDERS.O_ORDERSTATUS", "op": "in", "value": "F"}])
    with pytest.raises(ValidationError):
        plan(intent="list", columns=["ORDERS.O_ORDERKEY"], filters=[{"column": "ORDERS.O_ORDERDATE", "op": "between", "value": ["1995-01-01"]}])


def test_bad_alias_rejected():
    with pytest.raises(ValidationError):
        plan(intent="aggregate", measures=[{"alias": "x; drop", "function": "COUNT", "column": "*"}])


def test_star_only_with_count():
    with pytest.raises(ValidationError):
        plan(intent="aggregate", measures=[{"alias": "s", "function": "SUM", "column": "*"}])


# ---------------------------------------------------------------- compilation
def compile_(p, snap, ds, max_rows=500):
    c = SqlCompiler(ds).compile(p, snap, max_rows)
    validate(c.sql, snap, max_rows)  # every compiled query must pass the guard
    return c


def test_top_n_revenue_by_nation_joins_through_supplier_with_disclosure(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", tables=["LINEITEM", "NATION"], dimensions=[{"column": "NATION.N_NAME"}],
             measures=[{"alias": "revenue", "metric": "revenue"}], order_by=[{"field": "revenue", "direction": "DESC"}], limit=5)
    c = compile_(p, tpch_snapshot, tpch_dataset)
    assert "JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.SUPPLIER" in c.sql
    assert "LIMIT 6" in c.sql and c.tie_probe == (5, "revenue")  # one extra row to detect a tie at the cut-off
    assert any("could also relate to CUSTOMER" in a for a in c.assumptions)


def test_customer_nation_routes_through_customer(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", tables=["LINEITEM", "ORDERS", "CUSTOMER", "NATION"],
             dimensions=[{"column": "NATION.N_NAME"}], measures=[{"alias": "revenue", "metric": "revenue"}])
    c = compile_(p, tpch_snapshot, tpch_dataset)
    assert "CUSTOMER.C_NATIONKEY" in c.sql and "SUPPLIER" not in c.sql


def test_year_filter_and_region_join(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", tables=["LINEITEM", "ORDERS", "REGION"],
             dimensions=[{"column": "REGION.R_NAME"}], measures=[{"alias": "revenue", "metric": "revenue"}],
             filters=[{"column": "ORDERS.O_ORDERDATE", "op": "=", "value": 1995}], limit=1)
    c = compile_(p, tpch_snapshot, tpch_dataset)
    assert "YEAR(ORDERS.O_ORDERDATE) = 1995" in c.sql
    assert "REGION" in c.sql and "NATION" in c.sql


def test_monthly_trend_sorted_by_time(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", dimensions=[{"column": "ORDERS.O_ORDERDATE", "time_grain": "month"}],
             measures=[{"alias": "orders", "function": "COUNT", "column": "*"}], tables=["ORDERS"],
             filters=[{"column": "ORDERS.O_ORDERDATE", "op": "between", "value": ["1996-01-01", "1996-12-31"]}])
    c = compile_(p, tpch_snapshot, tpch_dataset)
    assert "DATE_TRUNC('month', ORDERS.O_ORDERDATE)" in c.sql
    assert "CAST('1996-01-01' AS DATE)" in c.sql
    assert "ORDER BY\n  o_orderdate_month" in c.sql or "ORDER BY o_orderdate_month" in c.sql


def test_categorical_value_case_corrected_and_unknown_triggers_clarification(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", tables=["CUSTOMER"], measures=[{"alias": "n", "function": "COUNT", "column": "*"}],
             filters=[{"column": "CUSTOMER.C_MKTSEGMENT", "op": "=", "value": "building"}])
    assert "'BUILDING'" in compile_(p, tpch_snapshot, tpch_dataset).sql
    p2 = plan(intent="aggregate", tables=["CUSTOMER"], measures=[{"alias": "n", "function": "COUNT", "column": "*"}],
              filters=[{"column": "CUSTOMER.C_MKTSEGMENT", "op": "=", "value": "RETAIL"}])
    with pytest.raises(ClarificationNeeded):
        compile_(p2, tpch_snapshot, tpch_dataset)


def test_injection_in_filter_value_is_a_literal(tpch_snapshot, tpch_dataset):
    p = plan(intent="list", columns=["CUSTOMER.C_NAME"],
             filters=[{"column": "CUSTOMER.C_NAME", "op": "=", "value": "x'; DROP TABLE ORDERS; --"}])
    c = compile_(p, tpch_snapshot, tpch_dataset)
    assert "'x''; DROP TABLE ORDERS; --'" in c.sql or "'x\\'; DROP TABLE ORDERS; --'" in c.sql


def test_unknown_column_raises_schema_mismatch(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", measures=[{"alias": "p", "function": "SUM", "column": "ORDERS.O_PROFIT"}])
    with pytest.raises(SchemaMismatchError, match="O_PROFIT"):
        compile_(p, tpch_snapshot, tpch_dataset)


def test_sum_of_text_rejected(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", measures=[{"alias": "s", "function": "SUM", "column": "CUSTOMER.C_NAME"}])
    with pytest.raises(SchemaMismatchError):
        compile_(p, tpch_snapshot, tpch_dataset)


def test_non_numeric_value_for_numeric_column(tpch_snapshot, tpch_dataset):
    p = plan(intent="list", columns=["ORDERS.O_ORDERKEY"],
             filters=[{"column": "ORDERS.O_TOTALPRICE", "op": ">", "value": "lots"}])
    with pytest.raises(QueryValidationError):
        compile_(p, tpch_snapshot, tpch_dataset)


def test_share_of_total_and_having(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", dimensions=[{"column": "LINEITEM.L_SHIPMODE"}],
             measures=[{"alias": "revenue_share", "metric": "revenue", "share_of_total": True},
                       {"alias": "lines", "function": "COUNT", "column": "*"}],
             tables=["LINEITEM"], having=[{"measure_alias": "lines", "op": ">", "value": 10}])
    c = compile_(p, tpch_snapshot, tpch_dataset)
    assert "SUM(SUM(" in c.sql  # window over the grouped aggregate, valid in Snowflake
    assert "OVER ()" in c.sql and "HAVING" in c.sql


def test_fanout_warning(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", tables=["ORDERS", "LINEITEM"], dimensions=[{"column": "LINEITEM.L_SHIPMODE"}],
             measures=[{"alias": "avg_order", "function": "AVG", "column": "ORDERS.O_TOTALPRICE"}])
    c = compile_(p, tpch_snapshot, tpch_dataset)
    assert any("inflated" in w for w in c.warnings)


def test_limit_capped(tpch_snapshot, tpch_dataset):
    p = plan(intent="list", columns=["ORDERS.O_ORDERKEY"], limit=1000)
    c = compile_(p, tpch_snapshot, tpch_dataset, max_rows=200)
    assert "LIMIT 200" in c.sql and c.warnings


def test_unknown_metric(tpch_snapshot, tpch_dataset):
    p = plan(intent="aggregate", measures=[{"alias": "x", "metric": "profit_margin"}])
    with pytest.raises(SchemaMismatchError, match="profit_margin"):
        compile_(p, tpch_snapshot, tpch_dataset)


def test_metric_formula_in_column_maps_to_metric(tpch_snapshot, tpch_dataset):
    # Gemma 4 sometimes writes a metric's formula into `column` instead of naming the metric.
    by_name = compile_(plan(intent="aggregate", measures=[{"alias": "revenue", "metric": "revenue"}]),
                       tpch_snapshot, tpch_dataset)
    for column in ["LINEITEM.L_EXTENDEDPRICE * (1 - LINEITEM.L_DISCOUNT)",
                   "SUM(LINEITEM.L_EXTENDEDPRICE*(1-LINEITEM.L_DISCOUNT))"]:
        p = plan(intent="aggregate", measures=[{"alias": "revenue", "function": "SUM", "column": column}])
        assert compile_(p, tpch_snapshot, tpch_dataset).sql == by_name.sql


def test_intent_enum_values():
    assert {i.value for i in Intent} == {"aggregate", "list", "advanced_sql", "clarify", "unsupported"}
