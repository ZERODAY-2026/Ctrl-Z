# Demo script (≈5 minutes)

Before: `ollama serve` running, `python scripts/check_connections.py` shows ALL GATES PASSED, `streamlit run app.py`.
Warm Gemma up with one question so the model is loaded.

1. **Context (30 s).** Header shows *Snowflake connected · DATAMIND_READER · DATAMIND_WH* and *Gemma verified*:
   both are live checks, not config checks. Sidebar: tables and columns discovered from `INFORMATION_SCHEMA`.
2. **Simple question (45 s).** "How many customers are there in each market segment?" → bar chart. Open
   *SQL* (exact executed SQL) and *Query details* (Snowflake query ID). Paste the ID into Snowsight → Query History.
3. **Join + filter + ranking (60 s).** "Which region had the highest revenue in 1995?" → open *How this was computed*:
   4 joins, the year filter, the revenue formula, and the disclosed choice "NATION linked through CUSTOMER".
4. **Trend (30 s).** "How did monthly order count change during 1996?" → line chart.
5. **Honesty (60 s).**
   * "What is the average age of our customers?" → *Not answerable from this data* (no age column).
   * "How many customers are in the RETAIL segment?" → clarification listing the real segment values.
   * "List the orders placed in 2005" → query ran, **no matching rows**, explained without overclaiming.
   * "Delete all orders from 1992" → refused; nothing executed.
6. **Answer changes when data changes (60 s).** With `DATAMIND_DATABASE=DATAMIND_DEMO`, `DATAMIND_SCHEMA=TPCH`, ask
   "Which customer nation had the highest revenue in 1998?". Run STEP A of `scripts/demo_data_change.sql` in Snowsight,
   ask again: KENYA now leads, with a new query ID. Run STEP B to revert.
7. **Second dataset by config (30 s).** Restart with `DATAMIND_DATASET_CONFIG=datasets/owid_co2.toml` and ask
   "Which 10 countries emitted the most CO2 in 2022?" No code changed.
8. **Limits (15 s).** Small local model: about 15–35 s per question (median 17 s in the evaluation); advanced (model-written) SQL is validated for safety,
   not proven correct, and is labelled as such.
