import datetime as dt

from src.services.grounding_service import build_evidence, deterministic_summary, unverified_numbers
from src.services.snowflake_service import QueryResult, classify_snowflake_error
from src.services.visualization_service import choose_chart
from src.utils.errors import ErrorCategory


def result(cols, types, rows, truncated=False):
    return QueryResult(sql="SELECT ... LIMIT 5", query_id="01b-test", columns=cols, column_types=types, rows=rows,
                       total_rows=len(rows), truncated=truncated, elapsed_ms=12)


def ev(cols, types, rows, question="q", **kw):
    return build_evidence(question, result(cols, types, rows, **kw), sql_origin="compiled_from_plan", tables=[],
                          columns_used=[], joins=[], filters=[], groupings=[], calculations=[], steps=[],
                          assumptions=[], warnings=[])


REGION_ROWS = [("ASIA", 21780379983.45), ("EUROPE", 21766212461.92), ("AMERICA", 21720347651.1)]


def test_grounded_text_passes():
    e = ev(["R_NAME", "REVENUE"], ["TEXT", "FIXED"], REGION_ROWS, question="top 3 regions by revenue")
    text = "ASIA leads with revenue of 21,780,379,983.45, followed by EUROPE (21,766,212,461.92). These are the top 3."
    assert unverified_numbers(text, e) == []


def test_rounded_and_scaled_numbers_pass():
    e = ev(["R_NAME", "REVENUE"], ["TEXT", "FIXED"], REGION_ROWS)
    assert unverified_numbers("ASIA had about 21.78 billion in revenue.", e) == []


def test_fabricated_number_detected():
    e = ev(["R_NAME", "REVENUE"], ["TEXT", "FIXED"], REGION_ROWS)
    bad = unverified_numbers("ASIA revenue grew 12% to 25,000,000,000.", e)
    assert "12%" in bad and "25,000,000,000" in bad[1] if len(bad) > 1 else bad


def test_derived_difference_is_flagged():
    e = ev(["R_NAME", "REVENUE"], ["TEXT", "FIXED"], REGION_ROWS)
    assert unverified_numbers("ASIA beat EUROPE by 14,167,521.53.", e)


def test_dates_and_years_allowed():
    e = ev(["MONTH", "ORDERS"], ["DATE", "FIXED"], [(dt.date(1996, 1, 1), 19385), (dt.date(1996, 2, 1), 18034)])
    assert unverified_numbers("In 1996, orders went from 19,385 in month 1 to 18,034.", e) == []


def test_empty_result_summary_is_honest():
    e = ev(["X"], ["FIXED"], [])
    s = deterministic_summary(e)
    assert "no matching rows" in s and "does not prove" in s


def test_null_values_disclosed():
    e = ev(["NAME", "VAL"], ["TEXT", "FIXED"], [("a", None), ("b", 2)])
    assert any("NULL" in l for l in e.limitations)
    assert "Highest VAL: b (2)" in deterministic_summary(e)


def test_truncation_disclosed():
    e = ev(["A"], ["FIXED"], [(1,), (2,)], truncated=True)
    assert any("first 2 rows" in l for l in e.limitations)


def test_single_row_summary():
    assert deterministic_summary(ev(["N"], ["FIXED"], [(150000,)])) == "Result: N = 150,000."


# ---------------------------------------------------------------- visualization
def test_chart_selection():
    assert choose_chart(["N"], ["FIXED"], [(5,)]).kind == "metric"
    assert choose_chart(["R_NAME", "REV"], ["TEXT", "FIXED"], REGION_ROWS).kind == "dot"  # values within 0.3%
    assert choose_chart(["R_NAME", "REV"], ["TEXT", "FIXED"], [("A", 10), ("B", 50)]).kind == "bar"
    assert choose_chart(["M", "C"], ["DATE", "FIXED"], [(dt.date(1996, 1, 1), 1), (dt.date(1996, 2, 1), 2)]).kind == "line"
    assert choose_chart(["X"], ["FIXED"], []).kind == "none"
    many = [(f"c{i}", i) for i in range(100)]
    assert choose_chart(["NAME", "V"], ["TEXT", "FIXED"], many).kind == "table"
    assert choose_chart(["O_YEAR", "REV"], ["FIXED", "FIXED"], [(1995, 1.0), (1996, 2.0)]).kind == "line"


# ---------------------------------------------------------------- error classification
class FakeSfError(Exception):
    def __init__(self, errno, msg):
        super().__init__(msg)
        self.errno, self.msg, self.sqlstate = errno, msg, None


def test_error_classification():
    assert classify_snowflake_error(FakeSfError(390100, "Incorrect username or password was specified.")).category == ErrorCategory.SNOWFLAKE_AUTH
    assert classify_snowflake_error(FakeSfError(630, "Statement reached its statement or warehouse timeout")).category == ErrorCategory.QUERY_TIMEOUT
    assert classify_snowflake_error(FakeSfError(606, "No active warehouse selected")).category == ErrorCategory.SNOWFLAKE_WAREHOUSE
    assert classify_snowflake_error(FakeSfError(2003, "Object does not exist or not authorized")).category == ErrorCategory.SNOWFLAKE_PERMISSION
    err = classify_snowflake_error(FakeSfError(390100, "Incorrect username or password"))
    assert "password" not in err.user_message.lower() or "credential" in err.user_message.lower()
