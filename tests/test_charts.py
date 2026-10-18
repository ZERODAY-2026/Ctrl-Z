"""Regression tests: charts plot exactly the returned rows; labels and numbers stay consistent.

All rows here are SYNTHETIC values with known labels (some copied from a real TPC-H run for realism).
"""

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest

from config.settings import DatasetConfig
from src.schemas.query_plan import QueryPlan
from src.security.sql_guard import validate
from src.services.grounding_service import (build_evidence, deterministic_summary, fmt_number, unverified_labels,
                                            unverified_numbers)
from src.services.snowflake_service import QueryResult, _normalise
from src.services.sql_service import SqlCompiler
from src.services.visualization_service import choose_chart, fmt_compact

ROOT = Path(__file__).resolve().parent.parent

PRIORITY = [("5-LOW", Decimal("45501158695.79")), ("2-HIGH", Decimal("45479776243.03")),
            ("1-URGENT", Decimal("45418729437.08")), ("4-NOT SPECIFIED", Decimal("45276033983.10")),
            ("3-MEDIUM", Decimal("45153608088.46"))]
CUSTOMERS = [("Customer#000102022", 102022, 41), ("Customer#000003451", 3451, 41), ("Customer#000102004", 102004, 41),
             ("Customer#000003373", 3373, 40), ("Customer#000117082", 117082, 40)]


def evidence(cols, types, rows):
    r = QueryResult("SELECT 1", "qid", cols, types, rows, len(rows), False, 1)
    return build_evidence("q", r, sql_origin="compiled_from_plan", tables=[], columns_used=[], joins=[], filters=[],
                          groupings=[], calculations=[], steps=[], assumptions=[], warnings=[])


def figure_points(chart):
    tr = chart.figure.data[0]
    return list(tr.y), list(tr.x)


# ---------------------------------------------------------------- order priority (narrow spread)
def test_priority_chart_values_match_rows_exactly():
    ch = choose_chart(["O_ORDERPRIORITY", "TOTAL_SALES"], ["TEXT", "FIXED"], PRIORITY)
    labels, xs = figure_points(ch)
    assert labels == [p for p, _ in PRIORITY]                       # same rows, same order, no transposition
    assert xs == [float(v) for _, v in PRIORITY]                     # plotted coordinates
    assert ch.plotted == PRIORITY                                    # exact Decimals retained
    assert list(ch.figure.data[0].customdata) == [fmt_number(v) for _, v in PRIORITY]  # hover = full precision
    assert fmt_number(PRIORITY[0][1]) == "45,501,158,695.79"


def test_priority_uses_disclosed_dot_plot_not_flat_bars():
    ch = choose_chart(["O_ORDERPRIORITY", "TOTAL_SALES"], ["TEXT", "FIXED"], PRIORITY)
    assert ch.kind == "dot"
    assert "not zero" in ch.reason and "%" in ch.reason             # truncated axis is disclosed
    lo, hi = ch.figure.layout.xaxis.range
    assert lo > 0 and lo < float(PRIORITY[-1][1])                     # every point is inside the axis
    assert ch.figure.layout.title.text == "Total sales by order priority"
    assert all(t.endswith("B") for t in ch.figure.layout.xaxis.ticktext)
    assert list(ch.figure.data[0].text)[0] == "45.50B"


def test_wide_spread_uses_zero_based_bars():
    ch = choose_chart(["SEGMENT", "N"], ["TEXT", "FIXED"], [("A", 10), ("B", 50), ("C", 25)])
    assert ch.kind == "bar" and ch.figure.layout.xaxis.range[0] == 0


# ---------------------------------------------------------------- top customers
def test_customer_labels_identical_in_table_chart_and_figure_json():
    rows = [(n, k, c) for n, k, c in CUSTOMERS]
    ch = choose_chart(["C_NAME", "C_CUSTKEY", "ORDER_COUNT"], ["TEXT", "FIXED", "FIXED"], rows)
    labels, xs = figure_points(ch)
    assert labels == [n for n, _, _ in CUSTOMERS]
    assert xs == [c for _, _, c in CUSTOMERS]                        # ORDER_COUNT, not the C_CUSTKEY id column
    spec = ch.figure.to_json()                                        # what is sent to the browser
    for name, _, _ in CUSTOMERS:
        assert name in spec
    assert "ffabab" not in spec


def test_app_disables_streamlit_theme_color_substitution():
    """Streamlit's default theme rewrites '#000001'..'#000010' inside the figure JSON (placeholder colours),
    turning 'Customer#000003451' into 'Customer#ffabab451'. Every st.plotly_chart call must pass theme=None."""
    sources = [ROOT / "app.py", *sorted((ROOT / "ui").glob("*.py"))]
    trees = [ast.parse(f.read_text(encoding="utf-8")) for f in sources]
    calls = [n for t in trees for n in ast.walk(t) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "plotly_chart"]
    assert calls
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert "theme" in kw and isinstance(kw["theme"], ast.Constant) and kw["theme"].value is None


def test_narrative_label_check_catches_rewritten_customer():
    ev = evidence(["C_NAME", "C_CUSTKEY", "ORDER_COUNT"], ["TEXT", "FIXED", "FIXED"], CUSTOMERS)
    good = "Customer#000102022 and Customer#000003451 each placed 41 orders."
    assert unverified_labels(good, ev) == [] and unverified_numbers(good, ev) == []
    assert unverified_labels("Customer#ffabab451 placed 41 orders.", ev) == ["Customer#ffabab451"]
    assert unverified_labels("Customer#3451 placed 41 orders.", ev) == ["Customer#3451"]  # dropped leading zeros


def test_number_attributed_with_wrong_precision_is_caught():
    ev = evidence(["O_ORDERPRIORITY", "TOTAL_SALES"], ["TEXT", "FIXED"], PRIORITY)
    assert unverified_numbers("5-LOW had 45,501,158,695.79.", ev) == []
    assert unverified_numbers("5-LOW had about 45.50 billion.", ev) == []
    assert unverified_numbers("5-LOW had 45,600,000,000.", ev)        # was accepted under the old 0.5% tolerance


def test_tie_break_is_deterministic_and_uses_stable_key(tpch_snapshot):
    ds = DatasetConfig.load(ROOT / "datasets" / "tpch_sf1.toml")
    plan = QueryPlan.model_validate({
        "intent": "aggregate", "tables": ["ORDERS", "CUSTOMER"], "dimensions": [{"column": "CUSTOMER.C_NAME"}],
        "measures": [{"alias": "order_count", "metric": "order_count"}],
        "order_by": [{"field": "order_count", "direction": "DESC"}], "limit": 5})
    c = SqlCompiler(ds).compile(plan, tpch_snapshot, 500)
    validate(c.sql, tpch_snapshot, 500)
    flat = " ".join(c.sql.split())
    assert "CUSTOMER.C_CUSTKEY AS c_custkey" in flat
    assert "GROUP BY CUSTOMER.C_NAME, CUSTOMER.C_CUSTKEY" in flat
    assert "ORDER BY order_count DESC NULLS LAST, c_custkey ASC, c_name ASC" in flat


# ---------------------------------------------------------------- precision, nulls, duplicates, empty
def test_decimal_precision_preserved_from_connector():
    big = Decimal("123456789012345678.91")                            # beyond float precision
    assert _normalise(big) == big and isinstance(_normalise(big), Decimal)
    assert _normalise(Decimal("41")) == 41 and isinstance(_normalise(Decimal("41")), int)
    assert fmt_number(big) == "123,456,789,012,345,678.91"
    ev = evidence(["K", "V"], ["TEXT", "FIXED"], [("a", big), ("b", Decimal("1.50"))])
    assert "123,456,789,012,345,678.91" in deterministic_summary(ev)


def test_null_measure_rows_not_plotted_and_disclosed():
    ch = choose_chart(["SEG", "V"], ["TEXT", "FIXED"], [("A", 10), ("B", None), ("C", 30)])
    labels, xs = figure_points(ch)
    assert labels == ["A", "C"] and xs == [10, 30]
    assert "NULL" in ch.reason


def test_duplicate_labels_disambiguated_by_key_not_merged():
    rows = [("Customer#1", 1, 5), ("Customer#1", 2, 7), ("Customer#3", 3, 9)]
    ch = choose_chart(["C_NAME", "C_CUSTKEY", "N"], ["TEXT", "FIXED", "FIXED"], rows)
    labels, xs = figure_points(ch)
    assert labels == ["Customer#1 (1)", "Customer#1 (2)", "Customer#3 (3)"] and xs == [5, 7, 9]
    ch2 = choose_chart(["NAME", "N"], ["TEXT", "FIXED"], [("x", 1), ("x", 20)])
    assert figure_points(ch2)[0] == ["x (row 1)", "x (row 2)"]


def test_empty_result():
    assert choose_chart(["A", "B"], ["TEXT", "FIXED"], []).kind == "none"


def test_compact_format():
    assert fmt_compact(45501158695.79) == "45.50B"
    assert fmt_compact(1_234_567) == "1.23M"
    assert fmt_compact(41, 0) == "41"


def test_dataset_configs_parse_top_level_keys():
    """Regression: keys placed after a [table] header silently became part of that table."""
    tpch = DatasetConfig.load(ROOT / "datasets" / "tpch_sf1.toml")
    assert len(tpch.categorical_columns) >= 5 and len(tpch.example_questions) >= 5
    assert set(json.loads(json.dumps(list(tpch.metrics["quantity_sold"])))) == {"expression", "description"}
    assert DatasetConfig.load(ROOT / "datasets" / "owid_co2.toml").example_questions


# ---------------------------------------------------------------- tie at the top-N cut-off
def _result(rows):
    return QueryResult("SELECT ... LIMIT 6", "qid", ["C_NAME", "C_CUSTKEY", "ORDER_COUNT"], ["TEXT", "FIXED", "FIXED"],
                       list(rows), len(rows), False, 1)


def test_tie_probe_discloses_split_tie_and_trims_extra_row():
    from src.orchestrator import apply_tie_probe
    rows = [(n, k, c) for n, k, c in CUSTOMERS] + [("Customer#000122623", 122623, 40)]
    r = _result(rows)
    note = apply_tie_probe(r, (5, "order_count"))
    assert r.rows == [(n, k, c) for n, k, c in CUSTOMERS]          # exactly the 5 shown rows
    assert note and "ORDER_COUNT = 40" in note


def test_tie_probe_silent_without_tie():
    from src.orchestrator import apply_tie_probe
    rows = [(n, k, c) for n, k, c in CUSTOMERS] + [("Customer#000000009", 9, 39)]
    r = _result(rows)
    assert apply_tie_probe(r, (5, "order_count")) is None and len(r.rows) == 5
    r2 = _result(rows[:4])                                           # fewer rows than N: nothing to trim
    assert apply_tie_probe(r2, (5, "order_count")) is None and len(r2.rows) == 4


def test_count_axis_uses_whole_number_ticks_and_readable_labels(monkeypatch):
    from src.services import visualization_service as vs
    monkeypatch.setattr(vs, "COLUMN_LABELS", {"C_NAME": "Customer"})
    rows = [(n, k, c) for n, k, c in CUSTOMERS]
    ch = vs.choose_chart(["C_NAME", "C_CUSTKEY", "ORDER_COUNT"], ["TEXT", "FIXED", "FIXED"], rows)
    assert all(float(t).is_integer() for t in ch.figure.layout.xaxis.tickvals)
    assert list(ch.figure.data[0].text) == ["41", "41", "41", "40", "40"]
    assert ch.figure.layout.title.text == "Order count by customer"
    monkeypatch.setattr(vs, "COLUMN_LABELS", {})
    assert vs.humanize("C_NAME") == "C_NAME"            # never the ambiguous bare "Name"
