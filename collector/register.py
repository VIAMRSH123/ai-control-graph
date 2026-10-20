"""Register the AI systems under governance from db/seed/ai_systems.json.

Idempotent: re-running updates names, risk tiers and endpoints rather than
creating duplicates, so an endpoint can be filled in after deployment without
losing the evidence already collected against that system.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "db" / "seed" / "ai_systems.json"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///control_graph")


def main() -> int:
    import psycopg

    systems = json.loads(SEED.read_text())["systems"]
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        for system in systems:
            cur.execute(
                """
                INSERT INTO ai_system (key, name, description, provider, environment,
                                       risk_tier, owner, endpoint_url)
                VALUES (%(key)s, %(name)s, %(description)s, %(provider)s,
                        %(environment)s, %(risk_tier)s, %(owner)s,
                        NULLIF(%(endpoint_url)s, ''))
                ON CONFLICT (key) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    risk_tier = EXCLUDED.risk_tier,
                    endpoint_url = COALESCE(EXCLUDED.endpoint_url, ai_system.endpoint_url)
                """,
                system,
            )
            print(f"  {system['key']:14} {system['risk_tier']:10} {system['name']}")
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
