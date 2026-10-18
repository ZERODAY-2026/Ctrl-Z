# Evaluation

## Methodology

* **Unit tests** (`pytest -m "not live"`, no network): plan validation, SQL guard (DDL/DML/session/multi-statement,
  table functions, unknown tables/columns, out-of-scope databases, LIMIT enforcement), compiler (joins, date filters,
  categorical value checks, injection in values, share-of-total, fan-out warnings), grounding checks, chart choice,
  error classification. The TPC-H *schema* used there is a hand-written **mock of metadata only** (`tests/conftest.py`).
* **Live integration tests** (`pytest -m live`): real Snowflake connection, schema discovery, query ID capture,
  real Gemma plan, full end-to-end question. They **skip with a reason** when credentials or the model are missing.
* **Evaluation set** (`eval/questions.json`, `python scripts/run_eval.py`): 14 questions covering count, sum, average,
  top-N, grouped comparison, time series, 4-table join, share-of-total, top-within-group, ambiguity, a nonexistent column,
  an empty result, a write request, and an unknown filter value. Expected answers are **not hard-coded**: each answerable
  case has independently hand-written ground-truth SQL that the runner executes in Snowflake and compares row-by-row
  with DataMind's result. Output: `docs/evaluation_results.md` and `eval/results/latest.json`.

Metrics recorded: pass rate, answer accuracy vs ground truth, number of executed queries (with query IDs),
median latency, Gemma repair attempts, explanations withheld by the grounding check.

## Results recorded so far (2026-10-18)

| What | Environment | Result |
|---|---|---|
| Unit tests | Windows 11, Python 3.11.0 | **67 passed**, 6 live tests skipped (no Snowflake credentials yet) |
| Gemma inference | `gemma2:9b` on Ollama 0.17.1, RTX 4060 Laptop 8 GB, `num_ctx=6144` | Verified (health check "READY"); `num_ctx=8192` crashed with a CUDA error on this GPU |
| Gemma planning, 8 questions, **no Snowflake execution** (compiled + guard only) | same | 5/5 answerable questions produced correct SQL *after* the deterministic sanity check (2 needed one re-plan); "profit margin per employee" → unsupported ✔; "Delete all orders" → unsupported ✔; "Show sales" → answered with stated assumption (gross_sales) |
| Gemma advanced SQL (top nation per region) | same | Passed the guard but was **semantically wrong** (returned every nation, not the top one per region). Prompt was then improved; not re-measured yet |
| Gemma explanation + grounding check | same, on a synthetic 5-row result | 2/2 explanations fully grounded (no unverified numbers) |
| Planning latency | same | 22–66 s per plan (model is ~15–27 % CPU-offloaded on 8 GB VRAM) |
| **Live end-to-end evaluation set** | – | **NOT RUN YET — blocked on Snowflake credentials.** Run `python scripts/run_eval.py` |

These early numbers come from manual probes, not from the evaluation runner, and must not be read as the
final accuracy. Replace this table with `docs/evaluation_results.md` after the live run.
