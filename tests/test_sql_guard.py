import pytest

from src.security.sql_guard import validate
from src.utils.errors import QueryValidationError

DB = "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1"


def ok(sql, snap, max_rows=100):
    return validate(sql, snap, max_rows)


def test_simple_select_passes_and_gets_limit(tpch_snapshot):
    r = ok(f"SELECT COUNT(*) AS n FROM {DB}.ORDERS", tpch_snapshot)
    assert "LIMIT 100" in r.sql
    assert r.tables == [f"{DB}.ORDERS"]


def test_unqualified_table_is_qualified(tpch_snapshot):
    r = ok("SELECT o.O_ORDERKEY FROM ORDERS o", tpch_snapshot)
    assert f"{DB}.ORDERS" in r.sql


def test_existing_smaller_limit_kept_larger_lowered(tpch_snapshot):
    assert ok(f"SELECT C_NAME FROM {DB}.CUSTOMER LIMIT 5", tpch_snapshot).limit == 5
    r = ok(f"SELECT C_NAME FROM {DB}.CUSTOMER LIMIT 100000", tpch_snapshot)
    assert r.limit == 100 and "LIMIT 100" in r.sql and r.notes


def test_cte_and_join_and_window_pass(tpch_snapshot):
    sql = f"""WITH t AS (SELECT c.C_MKTSEGMENT, SUM(o.O_TOTALPRICE) AS total
              FROM {DB}.ORDERS o JOIN {DB}.CUSTOMER c ON o.O_CUSTKEY = c.C_CUSTKEY GROUP BY 1)
              SELECT C_MKTSEGMENT, total, RANK() OVER (ORDER BY total DESC) AS rnk FROM t"""
    r = ok(sql, tpch_snapshot)
    assert set(r.tables) == {f"{DB}.ORDERS", f"{DB}.CUSTOMER"}


def test_union_is_wrapped_with_limit(tpch_snapshot):
    r = ok(f"SELECT R_NAME FROM {DB}.REGION UNION ALL SELECT N_NAME FROM {DB}.NATION", tpch_snapshot)
    assert r.sql.strip().endswith("LIMIT 100")


@pytest.mark.parametrize("sql", [
    f"DELETE FROM {DB}.ORDERS",
    f"UPDATE {DB}.ORDERS SET O_TOTALPRICE = 0",
    f"INSERT INTO {DB}.ORDERS SELECT * FROM {DB}.ORDERS",
    f"DROP TABLE {DB}.ORDERS",
    f"CREATE TABLE x AS SELECT * FROM {DB}.ORDERS",
    f"MERGE INTO {DB}.ORDERS t USING {DB}.ORDERS s ON t.O_ORDERKEY = s.O_ORDERKEY WHEN MATCHED THEN DELETE",
    "ALTER SESSION SET QUERY_TAG = 'x'",
    "USE ROLE ACCOUNTADMIN",
    "GRANT ROLE ACCOUNTADMIN TO USER bob",
    "CALL some_proc()",
    "SHOW TABLES",
    f"COPY INTO @stage FROM {DB}.ORDERS",
    "TRUNCATE TABLE x",
    f"WITH d AS (DELETE FROM {DB}.ORDERS) SELECT * FROM d",
    f"WITH x AS (SELECT 1 AS a) INSERT INTO {DB}.ORDERS SELECT * FROM x",
    f"SELECT * INTO copy_of_orders FROM {DB}.ORDERS",
    f"EXECUTE IMMEDIATE 'DELETE FROM {DB}.ORDERS'",
    f"BEGIN DELETE FROM {DB}.ORDERS; END",
    f"EXPLAIN DELETE FROM {DB}.ORDERS",
    f"INSERT OVERWRITE INTO {DB}.ORDERS SELECT * FROM {DB}.ORDERS",
    f"CREATE OR REPLACE VIEW v AS SELECT * FROM {DB}.ORDERS",
    f"CREATE TABLE {DB}.ORDERS2 CLONE {DB}.ORDERS",
    f"ALTER TABLE {DB}.ORDERS DROP COLUMN O_COMMENT",
    "UNDROP TABLE ORDERS",
    "PUT file:///tmp/x @~",
    "REMOVE @~/x",
    "COMMIT",
    "SET x = 1",
])
def test_non_select_rejected(sql, tpch_snapshot):
    with pytest.raises(QueryValidationError):
        ok(sql, tpch_snapshot)


def test_multiple_statements_rejected(tpch_snapshot):
    with pytest.raises(QueryValidationError, match="single"):
        ok(f"SELECT 1 FROM {DB}.ORDERS; DROP TABLE {DB}.ORDERS", tpch_snapshot)


def test_comment_smuggling_rejected(tpch_snapshot):
    with pytest.raises(QueryValidationError):
        ok(f"SELECT 1 FROM {DB}.ORDERS /* hi */; DELETE FROM {DB}.ORDERS --", tpch_snapshot)


def test_unknown_table_rejected(tpch_snapshot):
    with pytest.raises(QueryValidationError, match="not accessible"):
        ok(f"SELECT * FROM {DB}.EMPLOYEES", tpch_snapshot)


def test_other_database_rejected(tpch_snapshot):
    with pytest.raises(QueryValidationError):
        ok("SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY", tpch_snapshot)
    with pytest.raises(QueryValidationError):
        ok("SELECT * FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1000.ORDERS", tpch_snapshot)


def test_unknown_column_rejected(tpch_snapshot):
    with pytest.raises(QueryValidationError, match="unknown column"):
        ok(f"SELECT O_PROFIT FROM {DB}.ORDERS", tpch_snapshot)


def test_alias_reference_allowed(tpch_snapshot):
    ok(f"SELECT SUM(O_TOTALPRICE) AS total FROM {DB}.ORDERS ORDER BY total DESC", tpch_snapshot)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))",
    "SELECT SYSTEM$WHITELIST()",
    "SELECT * FROM TABLE(GENERATOR(ROWCOUNT => 1000000000))",
    f"SELECT * FROM IDENTIFIER('{DB}.ORDERS')",
    f"SELECT f.value FROM {DB}.ORDERS, LATERAL FLATTEN(input => PARSE_JSON('[1]')) f",
])
def test_dangerous_functions_rejected(sql, tpch_snapshot):
    with pytest.raises(QueryValidationError):
        ok(sql, tpch_snapshot)


def test_garbage_fails_closed(tpch_snapshot):
    with pytest.raises(QueryValidationError):
        ok("SELEC FROM WHERE ((", tpch_snapshot)
    with pytest.raises(QueryValidationError):
        ok("", tpch_snapshot)


def test_select_without_table_rejected(tpch_snapshot):
    with pytest.raises(QueryValidationError):
        ok("SELECT 1", tpch_snapshot)


def test_comments_are_not_executed(tpch_snapshot):
    r = ok(f"SELECT O_ORDERKEY FROM {DB}.ORDERS -- ; DELETE FROM {DB}.ORDERS", tpch_snapshot)
    assert "DELETE" not in r.sql.upper() and "--" not in r.sql and "/*" not in r.sql
