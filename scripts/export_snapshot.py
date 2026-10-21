"""Export the graph to a static snapshot the web app can serve without a database.

Why this exists: the control graph is deterministic reference data rebuilt from
the seed files, so for a public demo there is nothing for a database to do per
request. Exporting a snapshot gives a live, free, always-up URL with no
credentials to provision.

What matters is that the snapshot is produced BY the real SQL -- node and edge
rows straight out of Postgres, and neighbourhoods computed by
`control_neighbourhood()` itself. The static site therefore shows exactly what
the database produced rather than a reimplementation of it.

Evidence coverage is included as a frozen result of the last collector run, and
is labelled as such in the UI.

    DATABASE_URL=... uv run --with 'psycopg[binary]' python scripts/export_snapshot.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "web" / "public" / "graph-snapshot.json"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///control_graph")

#: Depths the UI offers. Precomputed so the browser never has to traverse.
DEPTHS = (1, 2)
THRESHOLD = 0.4


def main() -> int:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, name, version, authority, legal_status::text, jurisdiction,
                   citation_url, text_reusable, notes
            FROM framework ORDER BY key
            """
        )
        frameworks = cur.fetchall()

        cur.execute(
            """
            SELECT n.id, f.key AS framework_key, n.native_ref, n.title, n.statement
            FROM control_node n JOIN framework f ON f.id = n.framework_id
            ORDER BY f.key, n.native_ref
            """
        )
        nodes = cur.fetchall()

        # concepts per node, for the "why" panel and concept search
        cur.execute(
            """
            SELECT cc.control_node_id AS node_id, c.key, c.label, cc.weight::float
            FROM control_concept cc JOIN concept c ON c.id = cc.concept_id
            ORDER BY cc.control_node_id, cc.weight DESC
            """
        )
        concepts: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            concepts.setdefault(row.pop("node_id"), []).append(row)

        # evidence coverage, frozen from the last collector run
        cur.execute(
            """
            SELECT l.control_node_id AS node_id, l.link_kind::text, count(*) AS n
            FROM evidence_control_link l GROUP BY 1, 2
            """
        )
        evidence: dict[int, dict[str, int]] = {}
        for row in cur.fetchall():
            evidence.setdefault(row["node_id"], {})[row["link_kind"]] = row["n"]

        # neighbourhoods, computed by the real SQL function
        neighbourhoods: dict[str, dict[str, list]] = {}
        for node in nodes:
            per_depth: dict[str, list] = {}
            for depth in DEPTHS:
                cur.execute(
                    "SELECT * FROM control_neighbourhood(%s::bigint, %s::integer, %s::numeric)",
                    (node["id"], depth, THRESHOLD),
                )
                related = []
                for row in cur.fetchall():
                    if row["depth"] == 0:
                        continue
                    # title/statement/framework are omitted: they are looked up
                    # from `nodes` by node_id, which keeps the payload small
                    # instead of repeating every control's text per neighbour
                    related.append(
                        {
                            "node_id": row["node_id"],
                            "depth": row["depth"],
                            "path_strength": float(row["path_strength"] or 0),
                            "edge_id": row["edge_id"],
                            "edge_type": row["edge_type"],
                            "provenance_tier": row["provenance_tier"],
                            "edge_strength": float(row["edge_strength"] or 0),
                            "edge_confidence": float(row["edge_confidence"] or 0),
                            "rationale": row["rationale"],
                            "is_divergence": row["is_divergence"],
                        }
                    )
                per_depth[str(depth)] = related
            neighbourhoods[str(node["id"])] = per_depth

        cur.execute("SELECT count(*) AS n FROM control_edge")
        edge_count = cur.fetchone()["n"]

        cur.execute(
            """
            SELECT provenance_tier::text AS tier, count(*) AS n
            FROM control_edge GROUP BY 1 ORDER BY 1
            """
        )
        by_tier = {r["tier"]: r["n"] for r in cur.fetchall()}

    for node in nodes:
        node["concepts"] = concepts.get(node["id"], [])
        node["evidence"] = evidence.get(node["id"], {})

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "Static snapshot of the control graph. Nodes, edges and every "
            "neighbourhood were produced by the Postgres schema and the "
            "control_neighbourhood() SQL function, not reimplemented in the "
            "browser. Evidence coverage is frozen from the last collector run."
        ),
        "stats": {
            "nodes": len(nodes),
            "edges": edge_count,
            "frameworks": len(frameworks),
            "by_tier": by_tier,
        },
        "frameworks": frameworks,
        "nodes": nodes,
        "neighbourhoods": neighbourhoods,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":"), default=str))
    size_kb = OUT.stat().st_size / 1024
    print(f"nodes         : {len(nodes)}")
    print(f"edges         : {edge_count}  {by_tier}")
    print(f"neighbourhoods: {len(neighbourhoods)} nodes x depths {DEPTHS}")
    print(f"written       : {OUT.relative_to(REPO)}  ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
