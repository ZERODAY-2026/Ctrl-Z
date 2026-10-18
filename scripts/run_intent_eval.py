"""Regression evaluation of the action-intent check against LIVE Gemma (and optionally the full pipeline).

    python scripts/run_intent_eval.py                 # classifier only (Gemma, no Snowflake)
    python scripts/run_intent_eval.py --e2e           # full orchestrator: live Gemma + live Snowflake
    python scripts/run_intent_eval.py --only I01,I05

Cases are in eval/intent_cases.json. Each has an `expected` intent and optionally `also_acceptable` intents for
genuinely borderline wording. Besides accuracy it reports the two errors that matter most:
  * missed modification: a MODIFY_DATA request labelled READ_ANALYSIS or HYPOTHETICAL (it then goes on to the
    planner; the SQL guard still blocks any write, but the user gets no clear refusal)
  * false refusal: a READ_ANALYSIS or HYPOTHETICAL request labelled MODIFY_DATA or UNSUPPORTED (a valid question
    is not answered)
Results go to eval/results/intent_latest.json and docs/intent_evaluation_results.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import load_settings  # noqa: E402
from src.services.gemma_service import GemmaService  # noqa: E402
from src.utils.errors import DataMindError  # noqa: E402

LABELS = ["READ_ANALYSIS", "HYPOTHETICAL", "MODIFY_DATA", "AMBIGUOUS", "UNSUPPORTED"]
PROCEEDS = {"READ_ANALYSIS", "HYPOTHETICAL"}
BLOCKS = {"MODIFY_DATA", "UNSUPPORTED"}


def score(results: list[dict]) -> dict:
    n = len(results)
    mod = [r for r in results if r["expected"] == "MODIFY_DATA"]
    benign = [r for r in results if r["expected"] in PROCEEDS]
    missed = [r for r in mod if r["predicted"] in PROCEEDS or r["predicted"] is None]
    false_ref = [r for r in benign if r["predicted"] in BLOCKS]
    return {
        "cases": n,
        "exact_accuracy": f"{sum(r['predicted'] == r['expected'] for r in results)}/{n}",
        "acceptable_accuracy": f"{sum(r['acceptable'] for r in results)}/{n}",
        "modification_cases": len(mod),
        "missed_modifications": len(missed),
        "missed_modification_rate": round(len(missed) / len(mod), 3) if mod else None,
        "modifications_sent_to_clarification": sum(r["predicted"] == "AMBIGUOUS" for r in mod),
        "read_or_hypothetical_cases": len(benign),
        "false_refusals": len(false_ref),
        "false_refusal_rate": round(len(false_ref) / len(benign), 3) if benign else None,
        "unnecessary_clarifications": sum(r["predicted"] == "AMBIGUOUS" for r in benign),
        "classifier_failures": sum(r["predicted"] is None for r in results),
        "missed_ids": [r["id"] for r in missed],
        "false_refusal_ids": [r["id"] for r in false_ref],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--e2e", action="store_true", help="run the full orchestrator (live Snowflake)")
    args = ap.parse_args()
    s = load_settings()
    gemma = GemmaService(s.gemma)
    cases = json.loads((ROOT / "eval" / "intent_cases.json").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]
    orch = None
    if args.e2e:
        from src.orchestrator import Orchestrator, history_entry
        from src.services.schema_service import SchemaService
        from src.services.snowflake_service import SnowflakeService
        sf = SnowflakeService(s.snowflake, s.max_rows)
        orch = Orchestrator(s, sf, SchemaService(sf, s.dataset), gemma)

    results = []
    for c in cases:
        t = time.perf_counter()
        rec = {"id": c["id"], "category": c["category"], "question": " → ".join([*c.get("setup", []), c["question"]]),
               "expected": c["expected"], "also_acceptable": c.get("also_acceptable", [])}
        if orch is None:
            history = [{"question": q, "plan": ""} for q in c.get("setup", [])] or None
            try:
                got = gemma.classify_intent(c["question"], s.dataset.description, history)
                rec.update(predicted=got.intent.value, confidence=got.confidence, reason=got.reason)
            except DataMindError as exc:
                rec.update(predicted=None, confidence=None, reason=f"classifier failed: {exc.user_message}")
        else:
            history = []
            for prior in c.get("setup", []):
                entry = history_entry(orch.ask(prior, history))
                if entry:
                    history.append(entry)
            resp = orch.ask(c["question"], history or None)
            ai = resp.action_intent
            ev = resp.evidence
            rec.update(predicted=ai.intent.value if ai else None, confidence=ai.confidence if ai else None,
                       reason=ai.reason if ai else "; ".join(resp.notes), status=resp.status.value,
                       answer=resp.answer[:200], query_id=ev.query_id if ev else None,
                       executed_sql_read_only=(ev.sql.lstrip().upper().startswith(("SELECT", "WITH", "(")))
                       if ev else None)
        rec["latency_s"] = round(time.perf_counter() - t, 1)
        rec["acceptable"] = rec["predicted"] in [c["expected"], *rec["also_acceptable"]]
        results.append(rec)
        extra = f" status={rec['status']}" if "status" in rec else ""
        print(f"{c['id']} {'ok ' if rec['acceptable'] else 'BAD'} expected={c['expected']:<13} "
              f"got={str(rec['predicted']):<13}{extra} {rec['latency_s']:>5}s  {c['question'][:60]}", flush=True)

    summary = {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "gemma_model": s.gemma.model,
               "mode": "end-to-end" if orch else "classifier only", **score(results)}
    if orch:
        summary["e2e_mutations_executed"] = sum(r.get("executed_sql_read_only") is False for r in results)
        summary["e2e_modify_cases_that_ran_a_query"] = sum(
            r["expected"] == "MODIFY_DATA" and r.get("query_id") is not None for r in results)
        orch.sf.close()
    by_cat = {}
    for r in results:
        ok, tot = by_cat.get(r["category"], (0, 0))
        by_cat[r["category"]] = (ok + r["acceptable"], tot + 1)
    confusion = Counter((r["expected"], r["predicted"] or "FAILED") for r in results)

    out = ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    name = "intent_e2e_latest.json" if orch else "intent_latest.json"
    (out / name).write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str),
                            encoding="utf-8")

    pred_labels = LABELS + (["FAILED"] if any(r["predicted"] is None for r in results) else [])
    lines = ["# Intent evaluation results (generated by scripts/run_intent_eval.py)", "",
             f"Run at {summary['run_at']} with Gemma `{s.gemma.model}` — mode: **{summary['mode']}**, "
             f"{len(results)} cases from `eval/intent_cases.json`.", "",
             "| Metric | Value |", "|---|---|"]
    for k in ("exact_accuracy", "acceptable_accuracy", "missed_modifications", "missed_modification_rate",
              "modifications_sent_to_clarification", "false_refusals", "false_refusal_rate",
              "unnecessary_clarifications", "classifier_failures", "e2e_mutations_executed",
              "e2e_modify_cases_that_ran_a_query"):
        if k in summary:
            lines.append(f"| {k.replace('_', ' ')} | {summary[k]} |")
    lines += ["", "Missed modification = MODIFY_DATA labelled READ_ANALYSIS/HYPOTHETICAL. False refusal = "
              "READ_ANALYSIS/HYPOTHETICAL labelled MODIFY_DATA/UNSUPPORTED. \"Acceptable\" also counts the "
              "alternative labels listed for borderline cases.", "",
              "## Confusion matrix (rows = expected, columns = predicted)", "",
              "| expected \\ predicted | " + " | ".join(pred_labels) + " |", "|---" * (len(pred_labels) + 1) + "|"]
    for e in LABELS:
        lines.append(f"| {e} | " + " | ".join(str(confusion.get((e, p), 0)) for p in pred_labels) + " |")
    lines += ["", "## By category", "", "| Category | Acceptable |", "|---|---|"]
    lines += [f"| {k} | {ok}/{tot} |" for k, (ok, tot) in sorted(by_cat.items())]
    lines += ["", "## Cases", "", "| ID | Category | Request | Expected | Got | OK | Model's reason |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        reason = str(r["reason"] or "").replace("|", "\\|")[:110]
        q = r["question"].replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['category']} | {q} | {r['expected']} | {r['predicted']} | "
                     f"{'✅' if r['acceptable'] else '❌'} | {reason} |")
    doc = "intent_evaluation_e2e_results.md" if orch else "intent_evaluation_results.md"
    (ROOT / "docs" / doc).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
