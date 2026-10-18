"""Structured action-intent classification produced by Gemma before any query is planned.

It decides what the user wants done (analyse, compute a what-if, change stored data, ...), not which
columns to query. It is a routing aid only: read-only access is enforced separately by the SQL guard
and the Snowflake role, so a wrong classification can never cause a write.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_TEXT = 300


class ActionIntent(str, Enum):
    READ_ANALYSIS = "READ_ANALYSIS"  # retrieve, compare, filter, aggregate or analyse existing data
    HYPOTHETICAL = "HYPOTHETICAL"    # compute a what-if scenario from existing values; nothing is stored
    MODIFY_DATA = "MODIFY_DATA"      # change stored records, structure or permissions
    AMBIGUOUS = "AMBIGUOUS"          # cannot tell which of the above is meant
    UNSUPPORTED = "UNSUPPORTED"      # not a request DataMind can serve (e.g. forecasting, external actions)


class IntentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: ActionIntent
    # Self-reported by the model and NOT calibrated: recorded for diagnostics, never used to make a decision.
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="", description="One short sentence on why this intent was chosen")
    requested_change: str | None = Field(default=None, description="MODIFY_DATA: what the user wants changed")
    clarification_question: str | None = Field(default=None, description="AMBIGUOUS: question for the user")
    safe_alternative: str | None = Field(default=None, description="MODIFY_DATA: a read-only question that "
                                         "would help instead")

    @field_validator("reason", "requested_change", "clarification_question", "safe_alternative")
    @classmethod
    def _tidy(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())[:_MAX_TEXT]
        return v or None

    @model_validator(mode="after")
    def _coherent(self) -> "IntentClassification":
        if self.intent == ActionIntent.AMBIGUOUS and not self.clarification_question:
            raise ValueError("intent AMBIGUOUS requires clarification_question")
        if self.intent == ActionIntent.MODIFY_DATA and not self.requested_change:
            raise ValueError("intent MODIFY_DATA requires requested_change")
        if self.intent == ActionIntent.UNSUPPORTED and not self.reason:
            raise ValueError("intent UNSUPPORTED requires reason")
        return self
