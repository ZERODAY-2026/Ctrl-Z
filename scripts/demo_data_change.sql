-- DataMind demo: show that answers change when the underlying data changes.
-- Requires the DATAMIND_DEMO copy from snowflake_setup.sql and the app pointed at it:
--   DATAMIND_DATABASE=DATAMIND_DEMO  DATAMIND_SCHEMA=TPCH
-- Run these statements as ACCOUNTADMIN (DataMind's own role is read-only and cannot do this).
--
-- 1. In DataMind ask:  "Which customer nation had the highest revenue in 1998?"   -> note the answer + query ID
-- 2. Run STEP A below.
-- 3. Ask the same question again: KENYA now leads, with a new query ID and new rows.
-- 4. Run STEP B to restore the original data.

USE ROLE ACCOUNTADMIN;
USE WAREHOUSE DATAMIND_WH;

-- STEP A: add one very large 1998 order for a customer in KENYA.
INSERT INTO DATAMIND_DEMO.TPCH.ORDERS
  (O_ORDERKEY, O_CUSTKEY, O_ORDERSTATUS, O_TOTALPRICE, O_ORDERDATE, O_ORDERPRIORITY, O_CLERK, O_SHIPPRIORITY, O_COMMENT)
SELECT 9000000001, c.C_CUSTKEY, 'O', 5000000000, '1998-07-01', '1-URGENT', 'Clerk#DEMO', 0, 'DataMind demo row'
FROM DATAMIND_DEMO.TPCH.CUSTOMER c JOIN DATAMIND_DEMO.TPCH.NATION n ON c.C_NATIONKEY = n.N_NATIONKEY
WHERE n.N_NAME = 'KENYA' ORDER BY c.C_CUSTKEY LIMIT 1;

INSERT INTO DATAMIND_DEMO.TPCH.LINEITEM
  (L_ORDERKEY, L_PARTKEY, L_SUPPKEY, L_LINENUMBER, L_QUANTITY, L_EXTENDEDPRICE, L_DISCOUNT, L_TAX, L_RETURNFLAG,
   L_LINESTATUS, L_SHIPDATE, L_COMMITDATE, L_RECEIPTDATE, L_SHIPINSTRUCT, L_SHIPMODE, L_COMMENT)
VALUES (9000000001, 1, 1, 1, 1, 5000000000, 0, 0, 'N', 'O', '1998-07-02', '1998-07-02', '1998-07-03',
        'NONE', 'AIR', 'DataMind demo row');

-- STEP B: revert.
-- DELETE FROM DATAMIND_DEMO.TPCH.LINEITEM WHERE L_ORDERKEY = 9000000001;
-- DELETE FROM DATAMIND_DEMO.TPCH.ORDERS   WHERE O_ORDERKEY = 9000000001;
