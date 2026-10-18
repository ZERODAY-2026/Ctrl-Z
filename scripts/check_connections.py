"""Feasibility gates: prove Snowflake and Gemma both work for real, without starting the UI.

    python scripts/check_connections.py

Prints PASS/FAIL per gate with evidence (Snowflake query IDs, Gemma latency). No secrets are printed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings  # noqa: E402
from src.services.gemma_service import GemmaService  # noqa: E402
from src.services.schema_service import SchemaService  # noqa: E402
from src.services.snowflake_service import SnowflakeService  # noqa: E402
from src.utils.errors import DataMindError  # noqa: E402


def main() -> int:
    s = load_settings()
    print("Configuration:", s.describe())
    ok = True

    print("\n[1] Snowflake authentication + live query")
    sf = SnowflakeService(s.snowflake, s.max_rows)
    try:
        h = sf.health_check()
        print(f"  PASS  account={h['account']} user={h['user']} role={h['role']} warehouse={h['warehouse']} "
              f"version={h['version']} query_id={h['query_id']}")
        print("\n[2] Schema discovery")
        snap = SchemaService(sf, s.dataset).get()
        print(f"  PASS  {len(snap.tables)} tables in {snap.database}.{snap.schema}: {', '.join(snap.tables)}")
        print(f"        categorical columns sampled: {len(snap.categorical_values)}; query ids: {snap.query_ids[:3]}...")
        print("\n[3] Analytical query")
        first = next(iter(snap.tables))
        r = sf.run_metadata(f'SELECT COUNT(*) FROM {snap.fqn(first)}')
        print(f"  PASS  COUNT(*) FROM {first} = {r.rows[0][0]:,}  (query_id={r.query_id})")
    except DataMindError as exc:
        ok = False
        print(f"  FAIL  {exc.user_message}")
    finally:
        sf.close()

    print("\n[4] Gemma inference")
    g = GemmaService(s.gemma).health_check()
    if g.get("verified"):
        print(f"  PASS  provider={g['provider']} model={g['model']} latency={g['latency_ms']} ms reply={g['sample']!r}")
    else:
        ok = False
        print(f"  FAIL  {g.get('error', g)}")
    print("\nALL GATES PASSED" if ok else "\nSOME GATES FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
