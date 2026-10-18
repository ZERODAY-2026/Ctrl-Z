"""Typed configuration loaded from environment variables, `.env`, or Streamlit secrets.

Secrets are never logged or rendered; `describe()` returns a redacted summary.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from src.utils.errors import ConfigurationError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    try:  # Streamlit secrets are optional; accessing them outside Streamlit raises.
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


@dataclass(frozen=True)
class SnowflakeSettings:
    account: str | None
    user: str | None
    password: str | None = field(repr=False)
    private_key_file: str | None
    private_key_passphrase: str | None = field(repr=False)
    authenticator: str | None
    warehouse: str | None
    role: str | None
    statement_timeout_s: int
    login_timeout_s: int

    def missing(self) -> list[str]:
        missing = [n for n, v in (("SNOWFLAKE_ACCOUNT", self.account), ("SNOWFLAKE_USER", self.user),
                                  ("SNOWFLAKE_WAREHOUSE", self.warehouse)) if not v]
        if not (self.password or self.private_key_file or self.authenticator):
            missing.append("SNOWFLAKE_PASSWORD (or SNOWFLAKE_PRIVATE_KEY_FILE / SNOWFLAKE_AUTHENTICATOR)")
        return missing


@dataclass(frozen=True)
class GemmaSettings:
    provider: str  # "ollama" (verified) or "google_ai_studio" (optional, unverified)
    model: str
    ollama_host: str
    api_key: str | None = field(repr=False)
    timeout_s: int
    num_ctx: int


@dataclass(frozen=True)
class DatasetConfig:
    """Everything dataset-specific lives here so a new dataset needs config, not code."""

    name: str
    database: str
    schema: str
    description: str
    license: str
    tables: list[str]
    table_descriptions: dict[str, str]
    column_descriptions: dict[str, str]
    relationships: list[tuple[str, str]]  # (many_side "T.C", one_side "T.C")
    metrics: dict[str, dict[str, str]]
    categorical_columns: list[str]
    column_labels: dict[str, str]  # result column -> display name for charts, e.g. C_NAME -> Customer
    label_keys: dict[str, str]  # display label column -> stable key column, e.g. CUSTOMER.C_NAME -> CUSTOMER.C_CUSTKEY
    example_questions: list[str]
    path: str

    @staticmethod
    def load(path: str | Path) -> "DatasetConfig":
        p = Path(path)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            raise ConfigurationError(f"Dataset config not found: {p.name}", str(p))
        with p.open("rb") as fh:
            raw = tomllib.load(fh)
        try:
            return DatasetConfig(
                name=raw["name"],
                database=raw["database"].upper(),
                schema=raw["schema"].upper(),
                description=raw.get("description", ""),
                license=raw.get("license", ""),
                tables=[t.upper() for t in raw.get("tables", [])],
                table_descriptions={k.upper(): v for k, v in raw.get("table_descriptions", {}).items()},
                column_descriptions={k.upper(): v for k, v in raw.get("column_descriptions", {}).items()},
                relationships=[(r["many"].upper(), r["one"].upper()) for r in raw.get("relationships", [])],
                metrics={k.lower(): v for k, v in raw.get("metrics", {}).items()},
                categorical_columns=[c.upper() for c in raw.get("categorical_columns", [])],
                label_keys={k.upper(): v.upper() for k, v in raw.get("label_keys", {}).items()},
                column_labels={k.upper(): v for k, v in raw.get("column_labels", {}).items()},
                example_questions=raw.get("example_questions", []),
                path=str(p),
            )
        except KeyError as exc:
            raise ConfigurationError(f"Dataset config {p.name} is missing key {exc}", str(exc)) from exc


@dataclass(frozen=True)
class Settings:
    snowflake: SnowflakeSettings
    gemma: GemmaSettings
    dataset: DatasetConfig
    max_rows: int
    preview_rows: int
    schema_cache_ttl_s: int

    def describe(self) -> dict[str, str]:
        """Redacted diagnostics safe to show in the UI."""
        sf = self.snowflake
        return {
            "Snowflake account": sf.account or "(not set)",
            "Snowflake user": sf.user or "(not set)",
            "Auth method": "password" if sf.password else ("key pair" if sf.private_key_file else (sf.authenticator or "(none)")),
            "Warehouse": sf.warehouse or "(not set)",
            "Role": sf.role or "(user default)",
            "Dataset": f"{self.dataset.database}.{self.dataset.schema}",
            "Gemma provider": self.gemma.provider,
            "Gemma model": self.gemma.model,
        }


def load_settings() -> Settings:
    snowflake = SnowflakeSettings(
        account=_get("SNOWFLAKE_ACCOUNT"),
        user=_get("SNOWFLAKE_USER"),
        password=_get("SNOWFLAKE_PASSWORD"),
        private_key_file=_get("SNOWFLAKE_PRIVATE_KEY_FILE"),
        private_key_passphrase=_get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        authenticator=_get("SNOWFLAKE_AUTHENTICATOR"),
        warehouse=_get("SNOWFLAKE_WAREHOUSE"),
        role=_get("SNOWFLAKE_ROLE"),
        statement_timeout_s=int(_get("DATAMIND_STATEMENT_TIMEOUT_S", "60")),
        login_timeout_s=int(_get("DATAMIND_LOGIN_TIMEOUT_S", "30")),
    )
    gemma = GemmaSettings(
        provider=(_get("GEMMA_PROVIDER", "ollama") or "ollama").lower(),
        model=_get("GEMMA_MODEL", "gemma2:9b") or "gemma2:9b",
        ollama_host=(_get("OLLAMA_HOST", "http://localhost:11434") or "").rstrip("/"),
        api_key=_get("GEMMA_API_KEY"),
        timeout_s=int(_get("GEMMA_TIMEOUT_S", "180")),
        num_ctx=int(_get("GEMMA_NUM_CTX", "6144")),
    )
    if gemma.provider not in {"ollama", "google_ai_studio"}:
        raise ConfigurationError(f"Unsupported GEMMA_PROVIDER '{gemma.provider}'. Use 'ollama' or 'google_ai_studio'.")
    if not gemma.model.lower().startswith("gemma"):
        # Hackathon rule: never silently swap Gemma for another model family.
        raise ConfigurationError(f"GEMMA_MODEL must be a Gemma model (got '{gemma.model}').")
    dataset = DatasetConfig.load(_get("DATAMIND_DATASET_CONFIG", "datasets/tpch_sf1.toml"))
    # Same semantic config, different location (e.g. the writable demo copy made by scripts/snowflake_setup.sql).
    db_override, schema_override = _get("DATAMIND_DATABASE"), _get("DATAMIND_SCHEMA")
    if db_override or schema_override:
        from dataclasses import replace
        dataset = replace(dataset, database=(db_override or dataset.database).upper(),
                          schema=(schema_override or dataset.schema).upper())
    return Settings(
        snowflake=snowflake,
        gemma=gemma,
        dataset=dataset,
        max_rows=int(_get("DATAMIND_MAX_ROWS", "500")),
        preview_rows=int(_get("DATAMIND_PREVIEW_ROWS", "50")),
        schema_cache_ttl_s=int(_get("DATAMIND_SCHEMA_CACHE_TTL_S", "900")),
    )
