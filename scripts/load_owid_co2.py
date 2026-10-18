"""Load the Our World in Data CO2 dataset into Snowflake (second dataset, used via config only).

    python scripts/load_owid_co2.py --role ACCOUNTADMIN

Source: https://github.com/owid/co2-data (CC BY 4.0; credit "Our World in Data"; some underlying
series come from third parties such as the Global Carbon Project). The CSV is downloaded at setup
time and written to DATAMIND_DEMO.OWID.CO2; it is not stored in this repository.
Then run DataMind with DATAMIND_DATASET_CONFIG=datasets/owid_co2.toml.
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings  # noqa: E402
from src.services.snowflake_service import SnowflakeService  # noqa: E402

URL = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
COLUMNS = ["country", "year", "iso_code", "population", "gdp", "co2", "co2_per_capita", "coal_co2", "oil_co2",
           "gas_co2", "cement_co2", "flaring_co2", "methane", "nitrous_oxide", "share_global_co2",
           "primary_energy_consumption", "energy_per_capita"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="ACCOUNTADMIN", help="role allowed to create DATAMIND_DEMO.OWID")
    args = ap.parse_args()
    from snowflake.connector.pandas_tools import write_pandas

    print(f"Downloading {URL} ...")
    df = pd.read_csv(io.StringIO(requests.get(URL, timeout=120).text), usecols=COLUMNS)
    df.columns = [c.upper() for c in df.columns]
    print(f"{len(df):,} rows, {df['COUNTRY'].nunique()} countries/aggregates, years {df.YEAR.min()}-{df.YEAR.max()}")

    s = load_settings()
    sf = SnowflakeService(replace(s.snowflake, role=args.role), s.max_rows)
    conn = sf.connection()
    cur = conn.cursor()
    for stmt in ["CREATE DATABASE IF NOT EXISTS DATAMIND_DEMO", "CREATE SCHEMA IF NOT EXISTS DATAMIND_DEMO.OWID",
                 "USE SCHEMA DATAMIND_DEMO.OWID"]:
        cur.execute(stmt)
    ok, _, nrows, _ = write_pandas(conn, df, "CO2", database="DATAMIND_DEMO", schema="OWID",
                                   auto_create_table=True, overwrite=True, quote_identifiers=False)
    cur.execute("COMMENT ON TABLE DATAMIND_DEMO.OWID.CO2 IS 'Our World in Data CO2 and greenhouse gas emissions, "
                "CC BY 4.0. One row per country (or aggregate region) and year.'")
    for stmt in ["GRANT USAGE ON DATABASE DATAMIND_DEMO TO ROLE DATAMIND_READER",
                 "GRANT USAGE ON SCHEMA DATAMIND_DEMO.OWID TO ROLE DATAMIND_READER",
                 "GRANT SELECT ON ALL TABLES IN SCHEMA DATAMIND_DEMO.OWID TO ROLE DATAMIND_READER"]:
        cur.execute(stmt)
    sf.close()
    print(f"Loaded={ok} rows={nrows:,} into DATAMIND_DEMO.OWID.CO2")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
