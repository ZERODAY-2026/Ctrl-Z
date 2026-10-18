"""Request lifecycle: question -> Gemma plan -> SQL -> guard -> Snowflake -> evidence -> grounded answer.

No UI code here. Every exit path returns a typed response; no path produces an
answer that did not come from an executed Snowflake query.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from config.settings import Settings
from src.schemas.intent import ActionIntent, IntentClassification
from src.schemas.query_plan import Intent, QueryPlan
from src.security import sql_guard
from src.services.gemma_service import GemmaCall, GemmaService, plan_summary
from src.services.grounding_service import (Evidence, build_evidence, deterministic_summary, unverified_labels,
                                            unverified_numbers)
from src.services.schema_service import SchemaService
from src.services.snowflake_service import QueryResult, SnowflakeService
from src.services.sql_service import ClarificationNeeded, SqlCompiler
from src.utils.errors import DataMindError, ErrorCategory, QueryValidationError, SchemaMismatchError

log = logging.getLogger(__name__)

MIN_QUESTION_CHARS = 3
MAX_QUESTION_CHARS = 500

# Pipeline stages reported to an optional progress callback, in order, as each one actually starts.
STAGES = ("interpret", "validate", "query", "verify")

# Intents answered without planning or running any query.
_NO_QUERY_INTENTS = (ActionIntent.MODIFY_DATA, ActionIntent.AMBIGUOUS, ActionIntent.UNSUPPORTED)
HYPOTHETICAL_HINT = ("This is a hypothetical what-if. Compute it with a read-only query from the current stored values "
                     "(use intent 'advanced_sql' if the scenario needs arithmetic the plan format cannot express). "
                     "Never change data.")


class Status(str, Enum):
    ANSWERED = "answered"
    EMPTY = "empty"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass
class DataMindResponse:
    question: str
    status: Status
    answer: str
    answer_source: str  # "gemma" (verified against evidence), "deterministic", or "system"
    plan: QueryPlan | None = None
    evidence: Evidence | None = None
    error_category: str | None = None
    retryable: bool = False
    timings_ms: dict[str, int] = field(default_factory=dict)
    gemma_calls: list[GemmaCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    action_intent: IntentClassification | None = None  # None when the intent check did not run or failed


class Orchestrator:
    def __init__(self, settings: Settings, snowflake: SnowflakeService, schema: SchemaService, gemma: GemmaService):
        self.settings = settings
        self.sf = snowflake
        self.schema = schema
        self.gemma = gemma
        self.compiler = SqlCompiler(settings.dataset)

    def ask(self, question: str, history: list[dict[str, str]] | None = None,
            progress: Callable[[str], None] | None = None) -> DataMindResponse:
        q = (question or "").strip()
        stage = progress or (lambda _name: None)
        timings: dict[str, int] = {}
        self.gemma.calls = []
        if len(q) < MIN_QUESTION_CHARS or len(q) > MAX_QUESTION_CHARS:
            return DataMindResponse(q, Status.ERROR, f"Please ask a question between {MIN_QUESTION_CHARS} and "
                                    f"{MAX_QUESTION_CHARS} characters long.", "system", error_category=ErrorCategory.INPUT.value)
        stage("interpret")
        intent, intent_note = self._classify(q, history, timings)
        if intent is not None and intent.intent in _NO_QUERY_INTENTS:
            resp = self._done(q, *intent_response(intent), "gemma", None, timings)
        else:
            hypothetical = intent is not None and intent.intent == ActionIntent.HYPOTHETICAL
            resp = self._answer(q, history, stage, timings, HYPOTHETICAL_HINT if hypothetical else None)
            if hypothetical and resp.evidence is not None:
                resp.notes.insert(0, "Hypothetical scenario: computed from the current stored values with a read-only "
                                     "query. No data was changed.")
        if intent_note:
            resp.notes.append(intent_note)
        resp.action_intent = intent
        return resp

    def _classify(self, q, history, timings) -> tuple[IntentClassification | None, str | None]:
        """Gemma's action-intent label. On failure, continue on the normal path: it is read-only by construction."""
        t = time.perf_counter()
        try:
            intent = self.gemma.classify_intent(q, self.settings.dataset.description, history)
        except DataMindError as exc:
            log.warning("Intent classification failed (%s); continuing on the read-only path", exc.diagnostic[:200])
            return None, ("The request-intent check was unavailable, so the question went straight to the read-only "
                          "query pipeline (the SQL guard still blocks any change to data).")
        finally:
            timings["gemma_intent"] = _ms(t)
        log.info("Intent %s (self-reported confidence %.2f): %s", intent.intent.value, intent.confidence, intent.reason)
        return intent, None

    def _answer(self, q, history, stage, timings, hint: str | None) -> DataMindResponse:
        """The analytical pipeline. `hint` is added to the model's copy of the question only."""
        model_q = f"{q}\n[{hint}]" if hint else q
        plan: QueryPlan | None = None
        try:
            t = time.perf_counter()
            snap = self.schema.get()
            context = self.schema.prompt_context(snap, q)
            timings["schema"] = _ms(t)

            t = time.perf_counter()
            plan = self.gemma.plan(model_q, context, history)
            problem = plan_sanity_problem(q, plan)
            if problem:
                log.info("Plan sanity check failed (%s); re-planning once", problem)
                plan = self.gemma.plan(model_q, context, history, feedback=problem)
            timings["gemma_plan"] = _ms(t)

            if plan.intent == Intent.CLARIFY:
                return self._done(q, Status.CLARIFY, plan.clarification_question or "Could you clarify the question?",
                                  "gemma", plan, timings)
            if plan.intent == Intent.UNSUPPORTED:
                return self._done(q, Status.UNSUPPORTED, f"I can't answer this from the available data: "
                                  f"{plan.unsupported_reason}", "gemma", plan, timings)

            stage("validate")
            t = time.perf_counter()
            if plan.intent == Intent.ADVANCED_SQL:
                built = self._advanced(model_q, context, snap)
            else:
                built = self._compile_with_repair(model_q, context, history, plan, snap, timings)
                plan = built.pop("plan")
            timings["sql_build"] = _ms(t)

            # Re-validate immediately before execution, whatever produced the SQL.
            guard = sql_guard.validate(built["sql"], snap, self.settings.max_rows)
            stage("query")
            t = time.perf_counter()
            result = self.sf.run_validated(guard.sql)
            timings["snowflake"] = _ms(t)
            tie_note = apply_tie_probe(result, built.get("tie_probe"))
            if tie_note:
                built["warnings"] = [*built.get("warnings", []), tie_note]

            evidence = build_evidence(
                q, result, sql_origin=built["origin"], tables=guard.tables,
                columns_used=built.get("columns") or guard.columns, joins=built.get("joins", []),
                filters=built.get("filters", []), groupings=built.get("groupings", []),
                calculations=built.get("calculations", []), steps=built["steps"],
                assumptions=built["assumptions"], warnings=[*built.get("warnings", []), *guard.notes])

            if evidence.fetched_rows == 0:
                return self._done(q, Status.EMPTY, deterministic_summary(evidence), "deterministic", plan, timings,
                                  evidence)

            stage("verify")
            t = time.perf_counter()
            answer, source, notes = self._explain(q, evidence)
            timings["gemma_explain"] = _ms(t)
            return self._done(q, Status.ANSWERED, answer, source, plan, timings, evidence, notes)

        except ClarificationNeeded as exc:
            return self._done(q, Status.CLARIFY, exc.question, "system", plan, timings)
        except SchemaMismatchError as exc:
            log.info("Schema mismatch: %s", exc.diagnostic or exc.user_message)
            return self._done(q, Status.UNSUPPORTED, exc.user_message, "system", plan, timings)
        except DataMindError as exc:
            log.warning("Request failed [%s]: %s", exc.category.value, exc.diagnostic)
            resp = self._done(q, Status.ERROR, exc.user_message, "system", plan, timings)
            resp.error_category = exc.category.value
            resp.retryable = exc.retryable
            return resp
        except Exception:  # noqa: BLE001 - last-resort guard; never leak a stack trace to the UI
            log.exception("Unexpected failure")
            return DataMindResponse(q, Status.ERROR, "Something unexpected went wrong; nothing was answered. "
                                    "Check the server log for details.", "system", plan,
                                    error_category=ErrorCategory.INTERNAL.value, timings_ms=timings,
                                    gemma_calls=list(self.gemma.calls))

    def rerun(self, evidence: Evidence, plan: QueryPlan | None) -> QueryResult:
        """Execute an answered query's exact SQL again (no Gemma call), so its rows compare like for like.

        The SQL passes the guard again first. A top-N query fetches one extra tie-probe row; it is trimmed
        the same way as the original, using the probe the deterministic compiler produces for that plan.
        """
        snap = self.schema.get()
        guard = sql_guard.validate(evidence.sql, snap, self.settings.max_rows)
        result = self.sf.run_validated(guard.sql)
        if evidence.sql_origin == "compiled_from_plan" and plan is not None:
            try:
                probe = self.compiler.compile(plan, snap, self.settings.max_rows).tie_probe
            except DataMindError:
                probe = None
            apply_tie_probe(result, probe)
        return result

    # ------------------------------------------------------------------ stages
    def _compile_with_repair(self, q, context, history, plan, snap, timings) -> dict:
        """Compile the plan; if it references something invalid, give Gemma one chance to fix it."""
        try:
            compiled = self.compiler.compile(plan, snap, self.settings.max_rows)
        except (SchemaMismatchError, QueryValidationError) as exc:
            log.info("Plan rejected (%s); asking Gemma to repair once", exc.user_message)
            t = time.perf_counter()
            plan = self.gemma.plan(q, context, history, feedback=f"{exc.user_message} Use only listed tables/columns, "
                                   "or return intent 'unsupported' if the data is not available.")
            timings["gemma_replan"] = _ms(t)
            if plan.intent == Intent.CLARIFY:
                raise ClarificationNeeded(plan.clarification_question or exc.user_message) from exc
            if plan.intent == Intent.UNSUPPORTED:
                raise SchemaMismatchError(f"I can't answer this from the available data: {plan.unsupported_reason}") from exc
            if plan.intent == Intent.ADVANCED_SQL:
                return {**self._advanced(q, context, snap), "plan": plan}
            compiled = self.compiler.compile(plan, snap, self.settings.max_rows)
        return {"plan": plan, "sql": compiled.sql, "origin": "compiled_from_plan", "steps": compiled.steps,
                "assumptions": compiled.assumptions, "warnings": compiled.warnings, "columns": compiled.columns,
                "joins": compiled.joins, "filters": compiled.filters, "groupings": compiled.groupings,
                "calculations": compiled.calculations, "tie_probe": compiled.tie_probe}

    def _advanced(self, q, context, snap) -> dict:
        """Gemma writes SQL directly; it must pass the same guard. One repair attempt on rejection."""
        feedback = None
        for attempt in (1, 2):
            draft = self.gemma.draft_sql(q, context, snap.database, snap.schema, self.settings.max_rows, feedback)
            try:
                guard = sql_guard.validate(draft.sql, snap, self.settings.max_rows)
                break
            except QueryValidationError as exc:
                if attempt == 2:
                    raise
                feedback = exc.user_message
        steps = ["This question needed SQL beyond DataMind's structured plan, so Gemma wrote the SQL directly.",
                 "The SQL guard parsed it, confirmed it is a single read-only SELECT over allowlisted tables and known "
                 "columns, and enforced a row limit."]
        if draft.explanation:
            steps.append(f"Gemma's description of the query: {draft.explanation}")
        return {"sql": guard.sql, "origin": "gemma_sql_validated", "steps": steps, "assumptions": draft.assumptions,
                "warnings": ["Model-written SQL: it is validated for safety, not proven correct. Check the SQL tab."],
                "columns": guard.columns}

    def _explain(self, q: str, ev: Evidence) -> tuple[str, str, list[str]]:
        fallback = deterministic_summary(ev)
        try:
            text = self.gemma.explain(q, ev.as_prompt_text())
        except DataMindError as exc:
            return fallback, "deterministic", [f"Gemma explanation unavailable ({exc.user_message}); "
                                               "showing a deterministic summary of the verified result."]
        bad = unverified_numbers(text, ev) + unverified_labels(text, ev)
        if bad:
            ev.unverified_claims = bad
            return fallback, "deterministic", [
                f"Gemma's explanation was withheld because it contained numbers or labels not found in the result: "
                f"{', '.join(bad)}. Showing a deterministic summary instead."]
        notes: list[str] = []
        unit_text = " ".join([self.settings.dataset.description, *self.settings.dataset.column_descriptions.values()])
        if "$" in text and "$" not in unit_text:
            text = text.replace("$", "")
            notes.append("Removed a '$' sign from Gemma's wording: this dataset does not state a currency.")
        return text, "gemma", notes

    def _done(self, q, status, answer, source, plan, timings, evidence=None, notes=None) -> DataMindResponse:
        return DataMindResponse(q, status, answer, source, plan, evidence, timings_ms=timings,
                                gemma_calls=list(self.gemma.calls), notes=notes or [])


_GROUPING_HINT = re.compile(r"\b(by|per|each|every|across|top \d+|bottom \d+|breakdown|trend|over time|monthly|"
                            r"yearly|quarterly|weekly|daily)\b", re.I)
_PER_GROUP_RANK = re.compile(r"\b(for each|in each|within each|for every|in every)\b.*\b(highest|lowest|top|most|least|"
                             r"best|largest|smallest|biggest)\b", re.I)
_SHARE_HINT = re.compile(r"\b(percent|percentage|share|proportion|fraction)\b|%", re.I)


def plan_sanity_problem(question: str, plan: QueryPlan) -> str | None:
    """Deterministic checks for common small-model planning slips. Returns feedback or None."""
    if plan.intent == Intent.AGGREGATE and not plan.dimensions and _GROUPING_HINT.search(question):
        return ("The question asks for a breakdown or ranking ('by', 'per', 'each', 'top N', or a time trend) but the "
                "plan has no dimensions, so it would return one total. Put the grouping column in dimensions "
                "(with time_grain for trends).")
    if plan.intent in (Intent.AGGREGATE, Intent.LIST) and _PER_GROUP_RANK.search(question):
        return ("The question asks for the top item within each group, which the plan format cannot express. "
                "Use intent 'advanced_sql'.")
    if (plan.intent == Intent.AGGREGATE and _SHARE_HINT.search(question)
            and not any(m.share_of_total for m in plan.measures)):
        return ("The question asks for a percentage or share, but no measure has share_of_total=true, so the plan "
                "would return raw totals. Set share_of_total to true on the measure being divided up.")
    return None


def intent_response(intent: IntentClassification) -> tuple[Status, str]:
    """Status and answer for an intent that is answered without running a query."""
    if intent.intent == ActionIntent.MODIFY_DATA:
        alternative = (intent.safe_alternative or "a read-only question about the current values").replace("*", "")
        return Status.UNSUPPORTED, (
            f"DataMind is read-only, so I can't make this change ({intent.requested_change}). No query was generated "
            f"or run, and the stored data is unchanged.\n\nI can answer a read-only question instead, for example: "
            f"*{alternative}*")
    if intent.intent == ActionIntent.AMBIGUOUS:
        return Status.CLARIFY, intent.clarification_question or "Could you clarify what you would like to do?"
    return Status.UNSUPPORTED, f"I can't answer this from the available data: {intent.reason}"


def apply_tie_probe(result, probe: tuple[int, str] | None) -> str | None:
    """Trim the extra probe row; return a disclosure if it ties with the last row shown."""
    if not probe or len(result.rows) <= probe[0]:
        return None
    n, sort_col = probe
    extra, last = result.rows[n], result.rows[n - 1]
    result.rows = result.rows[:n]
    idx = next((i for i, c in enumerate(result.columns) if c.upper() == sort_col.upper()), None)
    if idx is None or extra[idx] != last[idx]:
        return None
    return (f"The top-{n} cut-off splits a tie: at least one more row also has {result.columns[idx]} = {last[idx]} "
            f"but is not shown. Tied rows are ordered by the tie-break columns listed in the steps, not by importance.")


def history_entry(resp: DataMindResponse) -> dict[str, str] | None:
    if resp.plan is None:
        if resp.action_intent is None or resp.status not in (Status.CLARIFY, Status.UNSUPPORTED):
            return None
        # Answered by the intent check: keep the exchange so a reply to the clarification, or a follow-up to a
        # refusal, is interpreted in context.
        return {"question": resp.question, "plan": f"(no query; {resp.action_intent.intent.value}: {resp.answer[:300]})"}
    return {"question": resp.question, "plan": plan_summary(resp.plan)}


def _ms(t: float) -> int:
    return int((time.perf_counter() - t) * 1000)
