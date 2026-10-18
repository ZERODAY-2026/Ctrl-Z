"""Run the evaluation set against LIVE Snowflake + Gemma and compare with independent ground truth.

    python scripts/run_eval.py [--only Q01,Q05]

Ground-truth SQL in eval/questions.json was written by hand, independently of DataMind's
compiler, and is executed in Snowflake at evaluation time (so expected answers are never
hard-coded). Results go to eval/results/latest.json and docs/evaluation_results.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import load_settings  # noqa: E402
from src.orchestrator import Orchestrator, Status  # noqa: E402
from src.services.gemma_service import GemmaService  # noqa: E402
from src.services.schema_service import SchemaService  # noqa: E402
from src.services.snowflake_service import SnowflakeService  # noqa: E402


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def _value_in_row(gv, row) -> bool:
    g = _num(gv)
    for v in row:
        if g is not None and _num(v) is not None:
            if abs(g - _num(v)) <= max(1e-6 * abs(g), 0.01):
                return True
        elif str(gv).strip() == str(v).strip():
            return True
    return False


def rows_match(gt_rows, dm_rows, mode: str) -> tuple[bool, str]:
    if mode == "first_row":
        gt_rows = gt_rows[:1]
        dm_rows = dm_rows[:1]
    if mode == "labels":  # compare only non-numeric values
        gt_rows = [tuple(v for v in r if _num(v) is None) for r in gt_rows]
    if not dm_rows:
        return False, "DataMind returned no rows"
    if mode != "labels" and len(gt_rows) != len(dm_rows):
        return False, f"row count {len(dm_rows)} != expected {len(gt_rows)}"
    unmatched = [r for r in gt_rows if not any(all(_value_in_row(v, d) for v in r) for d in dm_rows)]
    if unmatched:
        return False, f"{len(unmatched)}/{len(gt_rows)} expected rows not found, e.g. {unmatched[0]}"
    return True, f"all {len(gt_rows)} expected rows matched"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    s = load_settings()
    sf = SnowflakeService(s.snowflake, s.max_rows)
    schema = SchemaService(sf, s.dataset)
    gemma = GemmaService(s.gemma)
    orch = Orchestrator(s, sf, schema, gemma)
    db = f"{s.dataset.database}.{s.dataset.schema}"
    cases = json.loads((ROOT / "eval" / "questions.json").read_text())
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]

    results = []
    for c in cases:
        t = time.perf_counter()
        resp = orch.ask(c["question"])
        latency = time.perf_counter() - t
        ev = resp.evidence
        rec = {"id": c["id"], "category": c["category"], "question": c["question"], "expect": c["expect"],
               "status": resp.status.value, "answer": resp.answer, "answer_source": resp.answer_source,
               "sql": ev.sql if ev else None, "sql_origin": ev.sql_origin if ev else None,
               "query_id": ev.query_id if ev else None, "rows": ev.fetched_rows if ev else None,
               "latency_s": round(latency, 1), "gemma_calls": len(resp.gemma_calls),
               "gemma_invalid_output": any(x.attempts > 1 for x in resp.gemma_calls),
               "explanation_withheld": bool(ev and ev.unverified_claims)}
        exp = c["expect"]
        if exp == "answered":
            if resp.status != Status.ANSWERED or ev is None:
                rec["pass"], rec["detail"] = False, f"expected an answer, got {resp.status.value}: {resp.answer[:120]}"
            else:
                gt = sf.run_metadata(c["ground_truth_sql"].format(db=db))
                rec["ground_truth_query_id"] = gt.query_id
                rec["pass"], rec["detail"] = rows_match(gt.rows, ev.rows, c.get("compare", "rows"))
        elif exp == "clarify_or_assumption":
            rec["pass"] = resp.status == Status.CLARIFY or bool(ev and ev.assumptions)
            rec["detail"] = "asked for clarification" if resp.status == Status.CLARIFY else (
                "answered with stated assumptions" if rec["pass"] else "answered without stating an assumption")
        elif exp == "clarify_or_unsupported":
            rec["pass"] = resp.status in (Status.CLARIFY, Status.UNSUPPORTED)
            rec["detail"] = resp.answer[:160]
        else:
            rec["pass"] = resp.status.value == exp
            rec["detail"] = resp.answer[:160]
        results.append(rec)
        print(f"{c['id']} {'PASS' if rec['pass'] else 'FAIL'} [{resp.status.value}] {latency:.0f}s  {rec['detail'][:110]}",
              flush=True)

    sf.close()
    passed = sum(r["pass"] for r in results)
    summary = {
        "run_at": dt.datetime.now().isoformat(timespec="seconds"), "dataset": db, "gemma_model": s.gemma.model,
        "cases": len(results), "passed": passed,
        "answer_cases": sum(r["expect"] == "answered" for r in results),
        "answer_cases_correct": sum(r["pass"] for r in results if r["expect"] == "answered"),
        "queries_executed": sum(r["query_id"] is not None for r in results),
        "median_latency_s": sorted(r["latency_s"] for r in results)[len(results) // 2] if results else None,
        "gemma_repairs_needed": sum(r["gemma_invalid_output"] for r in results),
        "explanations_withheld_by_grounding_check": sum(r["explanation_withheld"] for r in results),
    }
    out = ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
    lines = ["# Evaluation results (generated by scripts/run_eval.py)", "",
             f"Run at {summary['run_at']} against `{db}` with Gemma `{s.gemma.model}` — **live Snowflake + live Gemma**.", "",
             f"**{passed}/{len(results)} passed.** Answer accuracy vs independent ground truth: "
             f"{summary['answer_cases_correct']}/{summary['answer_cases']}. Median latency {summary['median_latency_s']} s.", "",
             "| ID | Category | Question | Expected | Got | Pass | Detail | Query ID |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        detail = str(r["detail"]).replace("|", "\\|")[:120]
        lines.append(f"| {r['id']} | {r['category']} | {r['question']} | {r['expect']} | {r['status']} | "
                     f"{'✅' if r['pass'] else '❌'} | {detail} | `{r['query_id'] or '-'}` |")
    (ROOT / "docs" / "evaluation_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
