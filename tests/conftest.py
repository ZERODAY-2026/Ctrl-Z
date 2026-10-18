"""Shared fixtures.

`tpch_snapshot` is a MOCK schema snapshot built from the public TPC-H column list.
It contains metadata only (no rows) and is used for offline unit tests; live tests
in test_integration.py discover the real schema from Snowflake instead.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import DatasetConfig  # noqa: E402
from src.services.schema_service import ColumnInfo, SchemaSnapshot, TableInfo  # noqa: E402

TPCH_COLUMNS = {
    "REGION": [("R_REGIONKEY", "NUMBER"), ("R_NAME", "TEXT"), ("R_COMMENT", "TEXT")],
    "NATION": [("N_NATIONKEY", "NUMBER"), ("N_NAME", "TEXT"), ("N_REGIONKEY", "NUMBER"), ("N_COMMENT", "TEXT")],
    "CUSTOMER": [("C_CUSTKEY", "NUMBER"), ("C_NAME", "TEXT"), ("C_ADDRESS", "TEXT"), ("C_NATIONKEY", "NUMBER"),
                 ("C_PHONE", "TEXT"), ("C_ACCTBAL", "NUMBER"), ("C_MKTSEGMENT", "TEXT"), ("C_COMMENT", "TEXT")],
    "ORDERS": [("O_ORDERKEY", "NUMBER"), ("O_CUSTKEY", "NUMBER"), ("O_ORDERSTATUS", "TEXT"), ("O_TOTALPRICE", "NUMBER"),
               ("O_ORDERDATE", "DATE"), ("O_ORDERPRIORITY", "TEXT"), ("O_CLERK", "TEXT"), ("O_SHIPPRIORITY", "NUMBER"),
               ("O_COMMENT", "TEXT")],
    "LINEITEM": [("L_ORDERKEY", "NUMBER"), ("L_PARTKEY", "NUMBER"), ("L_SUPPKEY", "NUMBER"), ("L_LINENUMBER", "NUMBER"),
                 ("L_QUANTITY", "NUMBER"), ("L_EXTENDEDPRICE", "NUMBER"), ("L_DISCOUNT", "NUMBER"), ("L_TAX", "NUMBER"),
                 ("L_RETURNFLAG", "TEXT"), ("L_LINESTATUS", "TEXT"), ("L_SHIPDATE", "DATE"), ("L_COMMITDATE", "DATE"),
                 ("L_RECEIPTDATE", "DATE"), ("L_SHIPINSTRUCT", "TEXT"), ("L_SHIPMODE", "TEXT"), ("L_COMMENT", "TEXT")],
    "PART": [("P_PARTKEY", "NUMBER"), ("P_NAME", "TEXT"), ("P_MFGR", "TEXT"), ("P_BRAND", "TEXT"), ("P_TYPE", "TEXT"),
             ("P_SIZE", "NUMBER"), ("P_CONTAINER", "TEXT"), ("P_RETAILPRICE", "NUMBER"), ("P_COMMENT", "TEXT")],
    "SUPPLIER": [("S_SUPPKEY", "NUMBER"), ("S_NAME", "TEXT"), ("S_ADDRESS", "TEXT"), ("S_NATIONKEY", "NUMBER"),
                 ("S_PHONE", "TEXT"), ("S_ACCTBAL", "NUMBER"), ("S_COMMENT", "TEXT")],
    "PARTSUPP": [("PS_PARTKEY", "NUMBER"), ("PS_SUPPKEY", "NUMBER"), ("PS_AVAILQTY", "NUMBER"),
                 ("PS_SUPPLYCOST", "NUMBER"), ("PS_COMMENT", "TEXT")],
}

CATEGORICAL = {
    "REGION.R_NAME": ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"],
    "CUSTOMER.C_MKTSEGMENT": ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"],
    "NATION.N_NAME": ["ALGERIA", "ARGENTINA", "BRAZIL", "CANADA", "CHINA", "EGYPT", "ETHIOPIA", "FRANCE", "GERMANY",
                      "INDIA", "INDONESIA", "IRAN", "IRAQ", "JAPAN", "JORDAN", "KENYA", "MOROCCO", "MOZAMBIQUE", "PERU",
                      "ROMANIA", "RUSSIA", "SAUDI ARABIA", "UNITED KINGDOM", "UNITED STATES", "VIETNAM"],
    "LINEITEM.L_SHIPMODE": ["AIR", "FOB", "MAIL", "RAIL", "REG AIR", "SHIP", "TRUCK"],
    "ORDERS.O_ORDERPRIORITY": ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"],
}


def make_snapshot() -> SchemaSnapshot:
    tables = {}
    for t, cols in TPCH_COLUMNS.items():
        info = TableInfo(t, None, "")
        for name, dtype in cols:
            info.columns[name] = ColumnInfo(t, name, dtype, True)
        tables[t] = info
    return SchemaSnapshot("SNOWFLAKE_SAMPLE_DATA", "TPCH_SF1", tables, dict(CATEGORICAL), time.time(), [])


@pytest.fixture
def tpch_snapshot() -> SchemaSnapshot:
    return make_snapshot()


@pytest.fixture
def tpch_dataset() -> DatasetConfig:
    return DatasetConfig.load(ROOT / "datasets" / "tpch_sf1.toml")
