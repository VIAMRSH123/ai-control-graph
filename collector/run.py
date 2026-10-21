"""Run the collectors: observe live systems, tag evidence, propagate it.

    uv run --with 'psycopg[binary]' --with boto3 --with anthropic \
        python -m collector.run --system cv-screener

For each AI system in the registry it runs every collector that reports itself
available, stores what was observed, asks the mapper to pick ONE control per
artifact, and then lets the graph propagate from there.

Collectors that cannot run say so and are skipped. Nothing is simulated to fill
the gap: a missing AWS credential produces a smaller evidence set and a visible
reason, never a plausible-looking fake one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.aws.collect import AwsCollector  # noqa: E402
from collector.base import CollectorRun, store  # noqa: E402
from collector.github.collect import GithubCollector  # noqa: E402
from collector.mapper import map_artifact  # noqa: E402
from collector.probe.collect import ProbeCollector  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///control_graph")
THRESHOLD = float(os.environ.get("PROPAGATION_THRESHOLD", 0.45))
MAX_DEPTH = int(os.environ.get("PROPAGATION_MAX_DEPTH", 3))


def connect():
    import psycopg

    return psycopg.connect(DATABASE_URL, autocommit=False)


def systems(conn, only: str | None) -> list[dict]:
    with conn.cursor() as cur:
        if only:
            cur.execute(
                "SELECT id, key, name, endpoint_url FROM ai_system WHERE key = %s", (only,)
            )
        else:
            cur.execute("SELECT id, key, name, endpoint_url FROM ai_system ORDER BY key")
        return [
            dict(zip(("id", "key", "name", "endpoint_url"), row)) for row in cur.fetchall()
        ]


def collectors_for(system: dict):
    yield AwsCollector(system["key"])
    yield GithubCollector(system["key"])
    if system.get("endpoint_url"):
        yield ProbeCollector(system["key"], system["endpoint_url"])


def resolve(conn, framework: str, ref: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.id FROM control_node n JOIN framework f ON f.id = n.framework_id
            WHERE f.key = %s AND lower(n.native_ref) = lower(%s)
            """,
            (framework, ref),
        )
        row = cur.fetchone()
        return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect evidence and propagate it.")
    parser.add_argument("--system", help="only this ai_system key")
    parser.add_argument("--dry-run", action="store_true", help="report availability only")
    args = parser.parse_args()

    conn = connect()
    registry = systems(conn, args.system)
    if not registry:
        print("no AI systems registered. run: python -m collector.register", file=sys.stderr)
        return 1

    total_artifacts = total_direct = total_propagated = 0

    for system in registry:
        print(f"\n{system['key']}  ({system['name']})")

        for collector in collectors_for(system):
            ok, reason = collector.available()
            if not ok:
                print(f"  {collector.name:8} skipped — {reason}")
                continue
            if args.dry_run:
                print(f"  {collector.name:8} available")
                continue

            with CollectorRun(conn, collector.name, system["id"]) as run:
                # An exception raised while *producing* artifacts -- a network
                # timeout mid-iteration, say -- must not escape and abandon the
                # remaining collectors and systems. It degrades this run only.
                try:
                    artifacts = list(collector.collect())
                except Exception as exc:  # noqa: BLE001
                    run.error(collector.name, f"{type(exc).__name__}: {exc}")
                    print(f"  {collector.name:8} aborted — {type(exc).__name__}: {exc}")
                    artifacts = []

                for artifact in artifacts:
                    try:
                        evidence_id = store(conn, run, system["id"], collector.name, artifact)
                        mapping = map_artifact(conn, artifact)
                        total_artifacts += 1

                        if not mapping.framework or not mapping.ref:
                            print(f"  {collector.name:8} {artifact.artifact_type}: unmapped — {mapping.rationale[:60]}")
                            conn.commit()
                            continue

                        node_id = resolve(conn, mapping.framework, mapping.ref)
                        if node_id is None:
                            run.error(artifact.artifact_type,
                                      f"mapper chose unknown control {mapping.framework} {mapping.ref}")
                            conn.commit()
                            continue

                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO evidence_control_link
                                    (evidence_id, control_node_id, link_kind, asserted_by, rationale)
                                VALUES (%s, %s, 'direct', %s, %s)
                                ON CONFLICT (evidence_id, control_node_id) DO NOTHING
                                """,
                                (
                                    evidence_id,
                                    node_id,
                                    f"{mapping.mode}:{mapping.model}",
                                    mapping.rationale,
                                ),
                            )
                            cur.execute(
                                "SELECT propagate_evidence(%s::bigint, %s::numeric, %s::integer)",
                                (evidence_id, THRESHOLD, MAX_DEPTH),
                            )
                            spread = cur.fetchone()[0]

                        total_direct += 1
                        total_propagated += spread
                        conn.commit()
                        print(
                            f"  {collector.name:8} {artifact.artifact_type:34} "
                            f"-> {mapping.framework} {mapping.ref}  "
                            f"(+{spread} propagated, conf {mapping.confidence:.2f}, {mapping.mode})"
                        )
                    except Exception as exc:  # noqa: BLE001 - one bad artifact must not end the run
                        conn.rollback()
                        run.error(artifact.artifact_type, f"{type(exc).__name__}: {exc}")
                        print(f"  {collector.name:8} {artifact.artifact_type}: ERROR {exc}")

    if not args.dry_run:
        print(
            f"\n{total_artifacts} artifacts · {total_direct} direct tags · "
            f"{total_propagated} propagated links"
        )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
