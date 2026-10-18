# Evaluation

## Methodology

* **Unit tests** (`pytest -m "not live"`, no network): plan validation, SQL guard (DDL/DML/session/multi-statement,
  table functions, unknown tables/columns, out-of-scope databases, LIMIT enforcement), compiler (joins, date filters,
  categorical value checks, injection in values, share-of-total, fan-out warnings), grounding checks, chart choice and
  the chart studio's refusals, follow-up suggestions, error classification. The TPC-H *schema* used there is a
  hand-written **mock of metadata only** (`tests/conftest.py`).
* **Live integration tests** (`pytest -m live`): real Snowflake connection, schema discovery, query ID capture,
  real Gemma plan, full end-to-end question. They **skip with a reason** when credentials or the model are missing.
* **Evaluation set** (`eval/questions.json`, `python scripts/run_eval.py`): 38 questions run through the same
  `Orchestrator.ask()` the UI uses, against live Snowflake and live Gemma.
  * Answerable cases cover counts, sums, averages, distinct counts, top-N and bottom-1 rankings, grouped comparisons,
    yearly and monthly time series, 3- and 4-table joins, filters on coded values (`O` = open, `R` = returned),
    share-of-total, top-within-group (advanced path), informal phrasing with typos, and **follow-ups** that are asked
    after a real earlier question with its conversation context ("Now just for 1997", "show only the top 2").
  * Non-answerable cases cover ambiguity, nonexistent columns, a forecast outside the data, an empty result, write
    requests (delete, update), an unknown filter value, and a SQL-injection attempt inside a value.
  * Expected answers are **not hard-coded**. Each answerable case has hand-written ground-truth SQL, written
    independently of DataMind's compiler, that the runner executes in Snowflake at evaluation time and compares
    row-by-row with DataMind's result. Before adding a top-N case, the ground truth was checked for ties at the cut-off.
  * Comparison modes: `rows` (every expected row must appear, same row count), `first_row` (the top answer only),
    `labels` (non-numeric values only, for top-within-group), `numbers` (numeric values only, where a month may come
    back as a date or a number).

Output: `docs/evaluation_results.md` and `eval/results/latest.json`. Metrics recorded: pass rate, answer accuracy vs
ground truth, executed queries (with query IDs), median latency, Gemma repair attempts, and explanations withheld by
the grounding check.

## Results

See [evaluation_results.md](evaluation_results.md) for the latest run, with a Snowflake query ID for every executed
answer.

Latest full run (2026-10-18, TPC-H SF1, live Snowflake + `gemma4:e4b`):

| Metric | Result |
|---|---|
| Cases passed | **37 / 38** (Q06 dropped its year grouping; it passed 3/3 on re-runs) |
| Answers matching independent ground truth | **27 / 28** |
| Snowflake queries executed (each with a query ID) | 31 |
| Median latency per question | 22.9 s (includes the action-intent check) |
| Gemma outputs that needed one repair | 3 (Q08, Q34, Q36) |
| Explanations withheld by the grounding check | 1 (Q19: the explanation cited a number not in the result, so the deterministic summary was shown; the answer itself matched ground truth) |

Limits of this evidence: all 38 cases use one dataset (TPC-H SF1) and were written by the team, so they are a
regression suite, not a guarantee on unseen questions. The second dataset (OWID CO₂) has not been evaluated yet.

## Environment

Windows 11, Python 3.11.0, Ollama 0.40.5 with `gemma4:e4b` (Q4_K_M, fully on an RTX 4060 Laptop 8 GB GPU,
`num_ctx=6144`), Snowflake trial account, role `DATAMIND_READER`, X-Small warehouse.

## History

Early probes (before live Snowflake credentials were available) used `gemma2:9b` on Ollama 0.17.1 with compile-and-guard
only. They showed two problems that shaped the design: the small model sometimes dropped the grouping column (now caught
by a deterministic sanity check that re-plans once), and model-written SQL for "top nation per region" passed the guard
but was semantically wrong (the prompt was improved; that case, Q09, now matches ground truth).
