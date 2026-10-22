"""Show the propagation happening, live, on one artifact.

Built for demonstrating the core mechanic: fire a real prompt-injection at a
deployed AI system, tag the refusal to ONE control, and watch the count of
covered controls change.

It deliberately uses the real machinery rather than describing it — the same
ProbeCollector the normal run uses, the same mapper, and the same
`propagate_evidence()` SQL function. Nothing here reimplements the behaviour
it is demonstrating, so what you see on screen is what the system does.

    uv run --with 'psycopg[binary]' python scripts/demo_propagation.py

Add --keep to leave the evidence in place afterwards; by default it removes
only the artifact it created, so the demo can be run again from a clean slate.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.base import digest  # noqa: E402
from collector.mapper import map_artifact  # noqa: E402
from collector.probe.collect import ProbeCollector  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///control_graph")
THRESHOLD = float(os.environ.get("PROPAGATION_THRESHOLD", 0.45))
MAX_DEPTH = int(os.environ.get("PROPAGATION_MAX_DEPTH", 3))

DIM, BOLD, GREEN, RED, CYAN, RESET = (
    "\033[2m", "\033[1m", "\033[32m", "\033[31m", "\033[36m", "\033[0m",
)

#: Controls this artifact is expected to touch, shown before and after so the
#: change is visible rather than asserted.
WATCH = [
    ("eu_ai_act", "Art.15"),
    ("iso27001", "A.8.8"),
    ("iso27001", "A.8.24"),
    ("nist_ai_rmf", "MEASURE 2.7"),
]


def coverage(cur) -> dict[tuple[str, str], tuple[str, float | None]]:
    """How each watched control is currently evidenced, if at all."""
    cur.execute(
        """
        SELECT f.key, n.native_ref, l.link_kind::text, l.derived_strength
        FROM control_node n
        JOIN framework f ON f.id = n.framework_id
        LEFT JOIN evidence_control_link l ON l.control_node_id = n.id
        WHERE (f.key, n.native_ref) IN (
            SELECT * FROM unnest(%s::text[], %s::text[])
        )
        """,
        ([w[0] for w in WATCH], [w[1] for w in WATCH]),
    )
    out: dict[tuple[str, str], tuple[str, float | None]] = {}
    for fw, ref, kind, strength in cur.fetchall():
        if kind:
            out[(fw, ref)] = (kind, float(strength) if strength is not None else None)
    return out


def totals(cur) -> tuple[int, int, int]:
    cur.execute("SELECT count(*) FROM evidence")
    artifacts = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM evidence_control_link WHERE link_kind = 'direct'")
    direct = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM evidence_control_link WHERE link_kind = 'propagated'")
    propagated = cur.fetchone()[0]
    return artifacts, direct, propagated


def show(cur, heading: str) -> None:
    print(f"\n{BOLD}{heading}{RESET}")
    cov = coverage(cur)
    for fw, ref in WATCH:
        state = cov.get((fw, ref))
        if state is None:
            mark = f"{DIM}not evidenced{RESET}"
        elif state[0] == "direct":
            mark = f"{GREEN}direct{RESET}"
        else:
            mark = f"{CYAN}propagated  {state[1]:.3f}{RESET}"
        print(f"   {fw:12} {ref:14} {mark}")
    a, d, p = totals(cur)
    print(f"   {DIM}totals: {a} artifacts · {d} direct · {p} propagated{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", default="cv-screener", help="which AI system to probe")
    parser.add_argument("--keep", action="store_true", help="leave the evidence in place")
    args = parser.parse_args()

    import psycopg

    conn = psycopg.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, endpoint_url FROM ai_system WHERE key = %s", (args.system,)
        )
        row = cur.fetchone()
        if not row:
            sys.exit(f"no such AI system: {args.system}")
        system_id, name, endpoint = row
        if not endpoint:
            sys.exit(f"{args.system} has no endpoint_url recorded")

        print(f"{BOLD}Target{RESET}  {name}")
        print(f"        {endpoint}")

        show(cur, "BEFORE")

        collector = ProbeCollector(args.system, endpoint)
        ok, reason = collector.available()
        if not ok:
            sys.exit(f"\nendpoint not reachable: {reason}")

        # the probe reads the system card first, so the route and input field
        # come from the system itself rather than being assumed here
        artifact = None
        for candidate in collector.collect():
            if candidate.artifact_type == "prompt_injection_probe":
                artifact = candidate
                break
        if artifact is None:
            sys.exit("the probe produced no injection artifact")

        verdict = artifact.payload.get("verdict")
        latency = artifact.payload.get("latency_ms")
        colour = GREEN if verdict == "blocked" else RED
        print(f"\n{BOLD}→ fired a real prompt-injection{RESET}")
        print(f"   {DIM}{collector.route}  ·  {latency} ms{RESET}")
        print(f"   verdict: {colour}{verdict}{RESET}")

        # replace any artifact from a previous run of this demo
        cur.execute(
            "DELETE FROM evidence WHERE ai_system_id = %s AND artifact_type = %s",
            (system_id, artifact.artifact_type),
        )
        cur.execute(
            """
            INSERT INTO evidence (ai_system_id, collector, artifact_type, uri,
                                  sha256, payload, summary, valid_until)
            VALUES (%s, 'probe', %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (system_id, artifact.artifact_type, artifact.uri, artifact.sha256,
             __import__("json").dumps(artifact.payload, default=str),
             artifact.summary, artifact.valid_until),
        )
        evidence_id = cur.fetchone()[0]

        mapping = map_artifact(conn, artifact)
        cur.execute(
            """
            SELECT n.id FROM control_node n JOIN framework f ON f.id = n.framework_id
            WHERE f.key = %s AND lower(n.native_ref) = lower(%s)
            """,
            (mapping.framework, mapping.ref),
        )
        node = cur.fetchone()
        if not node:
            sys.exit(f"mapper chose an unknown control: {mapping.framework} {mapping.ref}")

        cur.execute(
            """
            INSERT INTO evidence_control_link (evidence_id, control_node_id, link_kind,
                                               asserted_by, rationale)
            VALUES (%s, %s, 'direct', %s, %s)
            """,
            (evidence_id, node[0], f"{mapping.mode}:{mapping.model}", mapping.rationale),
        )
        print(f"\n{BOLD}→ tagged to exactly one control{RESET}")
        print(f"   {mapping.framework} {mapping.ref}   {DIM}({mapping.mode}, confidence {mapping.confidence:.2f}){RESET}")

        cur.execute(
            "SELECT propagate_evidence(%s::bigint, %s::numeric, %s::integer)",
            (evidence_id, THRESHOLD, MAX_DEPTH),
        )
        spread = cur.fetchone()[0]
        print(f"\n{BOLD}→ propagated{RESET}  {GREEN}+{spread}{RESET} {DIM}controls reached "
              f"(threshold {THRESHOLD}, max depth {MAX_DEPTH}){RESET}")

        show(cur, "AFTER")

        cur.execute(
            """
            SELECT f.key, n.native_ref, l.derived_strength, array_length(l.edge_path, 1)
            FROM evidence_control_link l
            JOIN control_node n ON n.id = l.control_node_id
            JOIN framework f ON f.id = n.framework_id
            WHERE l.evidence_id = %s AND l.link_kind = 'propagated'
            ORDER BY l.derived_strength DESC
            """,
            (evidence_id,),
        )
        reached = cur.fetchall()
        if reached:
            print(f"\n{BOLD}Everything that one tag reached{RESET}")
            for fw, ref, strength, hops in reached:
                print(f"   {fw:12} {ref:14} {float(strength):.3f}  {DIM}{hops} hop(s){RESET}")

        if args.keep:
            conn.commit()
            print(f"\n{DIM}Evidence kept.{RESET}")
        else:
            conn.rollback()
            print(f"\n{DIM}Rolled back — the graph is back where it started, "
                  f"so this can be run again.{RESET}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
