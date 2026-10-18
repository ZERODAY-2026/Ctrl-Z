"""Visualization studio: charts plot only returned rows, and refuse forms that would mislead."""

import datetime as dt
from decimal import Decimal

from src.services import chart_studio as cs
from src.services.followup_service import suggest
from src.services.grounding_service import Evidence

PRIORITY_COLS, PRIORITY_TYPES = ["O_ORDERPRIORITY", "TOTAL_SALES"], ["TEXT", "FIXED"]
PRIORITY_ROWS = [("1-URGENT", Decimal("100.50")), ("2-HIGH", Decimal("80.25")), ("3-MEDIUM", Decimal("60.00")),
                 ("4-NOT SPECIFIED", Decimal("40.00")), ("5-LOW", Decimal("20.00"))]


def test_profile_and_recommendation():
    p = cs.profile(PRIORITY_COLS, PRIORITY_TYPES, PRIORITY_ROWS)
    assert p.categories == ["O_ORDERPRIORITY"] and p.measures == ["TOTAL_SALES"]
    assert cs.recommend(p) == "bar"
    p = cs.profile(["YEAR", "ORDERS"], ["FIXED", "FIXED"], [(1995, 10), (1996, 12)])
    assert p.times == ["YEAR"] and cs.recommend(p) == "line"


def test_bar_plots_exact_returned_values_in_query_order():
    chart = cs.build(cs.ChartSpec("bar"), PRIORITY_COLS, PRIORITY_TYPES, PRIORITY_ROWS)
    assert chart.problem is None
    assert chart.plotted == [(r[0], r[1]) for r in PRIORITY_ROWS]
    assert chart.caption == "Visualization of 5 returned rows"


def test_sort_and_top_n_are_disclosed():
    chart = cs.build(cs.ChartSpec("bar", sort="asc", top_n=2), PRIORITY_COLS, PRIORITY_TYPES, PRIORITY_ROWS)
    assert [v for _, v in chart.plotted] == [Decimal("20.00"), Decimal("40.00")]
    assert any("first 2 of 5" in n for n in chart.notes)


def test_line_requires_time_axis_and_suggests_alternative():
    chart = cs.build(cs.ChartSpec("line"), PRIORITY_COLS, PRIORITY_TYPES, PRIORITY_ROWS)
    assert chart.figure is None and "date" in chart.problem and chart.suggestion == "bar"


def test_line_sorted_by_time_with_dates():
    rows = [(dt.date(1996, 3, 1), 30), (dt.date(1996, 1, 1), 10), (dt.date(1996, 2, 1), 20)]
    chart = cs.build(cs.ChartSpec("line"), ["MONTH", "ORDERS"], ["DATE", "FIXED"], rows)
    assert [v for _, v in chart.plotted] == [10, 20, 30]


def test_donut_refuses_non_additive_measures():
    rows = [("A", Decimal("5.5")), ("B", Decimal("7.5"))]
    chart = cs.build(cs.ChartSpec("donut"), ["SEGMENT", "AVG_ORDER_VALUE"], ["TEXT", "FIXED"], rows)
    assert chart.figure is None and "adds up to a whole" in chart.problem and chart.suggestion == "bar"


def test_donut_refuses_too_many_slices_and_negative_values():
    many = [(f"N{i}", i + 1) for i in range(12)]
    assert "too many" in cs.build(cs.ChartSpec("donut"), ["NATION", "ORDERS"], ["TEXT", "FIXED"], many).problem
    neg = [("A", 5), ("B", -2)]
    assert "negative" in cs.build(cs.ChartSpec("donut"), ["NATION", "ORDERS"], ["TEXT", "FIXED"], neg).problem


def test_donut_on_partial_result_discloses_scope():
    chart = cs.build(cs.ChartSpec("donut"), PRIORITY_COLS, PRIORITY_TYPES, PRIORITY_ROWS, partial=True)
    assert chart.figure is not None and any("not of the whole dataset" in n for n in chart.notes)


def test_duplicate_x_is_refused_not_silently_summed():
    rows = [("1995", "AIR", 10), ("1995", "RAIL", 5), ("1996", "AIR", 8), ("1996", "RAIL", 9)]
    cols, types = ["SEGMENT", "MODE", "QTY"], ["TEXT", "TEXT", "FIXED"]
    chart = cs.build(cs.ChartSpec("bar", x="SEGMENT", y="QTY"), cols, types, rows)
    assert chart.figure is None and "never adds rows together" in chart.problem
    chart = cs.build(cs.ChartSpec("bar", x="SEGMENT", y="QTY", series="MODE"), cols, types, rows)
    assert chart.figure is not None and len(chart.figure.data) == 2 and len(chart.plotted) == 4


def test_scatter_needs_two_measures():
    assert "two numeric" in cs.build(cs.ChartSpec("scatter"), PRIORITY_COLS, PRIORITY_TYPES, PRIORITY_ROWS).problem
    rows = [("A", 1, 2.0), ("B", 2, 4.0), ("C", 3, 5.0)]
    chart = cs.build(cs.ChartSpec("scatter"), ["N", "ORDERS", "REVENUE"], ["TEXT", "FIXED", "REAL"], rows)
    assert chart.figure is not None and chart.plotted == [("1", 2.0), ("2", 4.0), ("3", 5.0)]


def test_null_values_not_plotted_and_disclosed():
    rows = [("A", 1), ("B", None), ("C", 3)]
    chart = cs.build(cs.ChartSpec("bar"), ["N", "ORDERS"], ["TEXT", "FIXED"], rows)
    assert len(chart.plotted) == 2 and any("NULL" in n for n in chart.notes)


def test_insights_come_from_rows():
    out = cs.insights(cs.ChartSpec("bar", "O_ORDERPRIORITY", "TOTAL_SALES"), PRIORITY_COLS, PRIORITY_ROWS,
                      partial=False)
    assert out[0] == "Highest Total sales: 1-URGENT (100.50)."
    assert any("33.4%" in s for s in out)  # 100.50 / 300.75


def _ev(cols, types, rows, tables=("ORDERS",)):
    return Evidence("q", "SELECT 1", "compiled_from_plan", "qid", "SUCCESS", list(tables), [], [], [], [], [], [],
                    cols, types, rows, len(rows), len(rows), False, 5)


def test_follow_ups_reflect_result_shape():
    s = suggest("What are total sales by order priority?", _ev(PRIORITY_COLS, PRIORITY_TYPES, PRIORITY_ROWS), [])
    assert "Show only the top 3 by total sales" in s and len(s) <= 4
    assert "What share of total sales does each order priority account for?" in s
    single = suggest("How many customers?", _ev(["CUSTOMERS"], ["FIXED"], [(150000,)], ("CUSTOMER",)),
                     ["CUSTOMER.C_MKTSEGMENT", "ORDERS.O_ORDERPRIORITY"])
    assert single == ["Break this down by market segment"]
    assert suggest("q", _ev(PRIORITY_COLS, PRIORITY_TYPES, []), []) == []
