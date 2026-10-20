"""Read API over the control graph.

Deliberately thin. Every interesting operation -- neighbourhood traversal,
evidence propagation, coverage -- lives in SQL functions in
db/migrations/002_graph_functions.sql, so the rules that matter (never cross a
divergence, decay strength along the path) hold for anything touching the
database, not just for callers who go through this service.

    uv run --with 'psycopg[binary]' --with fastapi --with uvicorn \
        uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///control_graph")
DEFAULT_THRESHOLD = float(os.environ.get("PROPAGATION_THRESHOLD", 0.45))
DEFAULT_MAX_DEPTH = int(os.environ.get("PROPAGATION_MAX_DEPTH", 3))

app = FastAPI(title="AI Control Graph", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)

_pool: Any = None


def _connect():
    global _pool
    import psycopg
    from psycopg.rows import dict_row

    if _pool is None:
        _pool = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    elif _pool.closed:
        _pool = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    return _pool


@contextmanager
def cursor():
    with _connect().cursor() as cur:
        yield cur


@app.get("/api/health")
def health() -> dict:
    with cursor() as cur:
        cur.execute("SELECT count(*) AS nodes FROM control_node")
        nodes = cur.fetchone()["nodes"]
        cur.execute("SELECT count(*) AS edges FROM control_edge")
        edges = cur.fetchone()["edges"]
    return {"status": "ok", "nodes": nodes, "edges": edges}


@app.get("/api/frameworks")
def frameworks() -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT f.key, f.name, f.version, f.authority, f.legal_status,
                   f.jurisdiction, f.citation_url, f.text_reusable, f.notes,
                   count(n.id) AS control_count
            FROM framework f LEFT JOIN control_node n ON n.framework_id = f.id
            GROUP BY f.id ORDER BY f.key
            """
        )
        return cur.fetchall()


@app.get("/api/search")
def search(q: str = Query(min_length=1), limit: int = 12) -> list[dict]:
    """Resolve what someone typed to candidate controls.

    Accepts a native reference ('A.6.2.8', 'iso 42001 A.6.2.8', 'Art.12'),
    a concept ('human oversight'), or free text from a control's statement.
    """
    needle = q.strip()
    # strip a leading framework name so 'iso 42001 A.6.2.8' resolves
    compact = needle.lower()
    for prefix in ("iso/iec ", "iso ", "eu ai act ", "nist ai rmf ", "nist ", "en "):
        if compact.startswith(prefix):
            rest = needle[len(prefix):].strip()
            if rest and rest[0].isdigit():
                parts = rest.split(None, 1)
                if len(parts) == 2 and parts[0].rstrip(":").isdigit():
                    needle = parts[1]
            break

    with cursor() as cur:
        cur.execute(
            """
            SELECT n.id, f.key AS framework_key, f.name AS framework_name,
                   n.native_ref, n.title, left(n.statement, 400) AS statement,
                   CASE
                     WHEN lower(n.native_ref) = lower(%(q)s) THEN 0
                     WHEN lower(n.native_ref) LIKE lower(%(q)s) || '%%' THEN 1
                     WHEN lower(n.title) LIKE '%%' || lower(%(q)s) || '%%' THEN 2
                     WHEN EXISTS (
                        SELECT 1 FROM control_concept cc JOIN concept c ON c.id = cc.concept_id
                        WHERE cc.control_node_id = n.id
                          AND (lower(c.label) LIKE '%%' || lower(%(q)s) || '%%'
                               OR lower(c.key) LIKE '%%' || lower(%(q)s) || '%%')
                     ) THEN 3
                     ELSE 4
                   END AS rank
            FROM control_node n JOIN framework f ON f.id = n.framework_id
            WHERE lower(n.native_ref) LIKE lower(%(q)s) || '%%'
               OR lower(n.title) LIKE '%%' || lower(%(q)s) || '%%'
               OR lower(n.statement) LIKE '%%' || lower(%(q)s) || '%%'
               OR EXISTS (
                    SELECT 1 FROM control_concept cc JOIN concept c ON c.id = cc.concept_id
                    WHERE cc.control_node_id = n.id
                      AND (lower(c.label) LIKE '%%' || lower(%(q)s) || '%%'
                           OR lower(c.key) LIKE '%%' || lower(%(q)s) || '%%')
               )
            ORDER BY rank, f.key, n.native_ref
            LIMIT %(limit)s
            """,
            {"q": needle, "limit": limit},
        )
        return cur.fetchall()


@app.get("/api/control/{node_id}")
def control(node_id: int) -> dict:
    with cursor() as cur:
        cur.execute(
            """
            SELECT n.id, f.key AS framework_key, f.name AS framework_name,
                   f.legal_status, n.native_ref, n.title, n.statement
            FROM control_node n JOIN framework f ON f.id = n.framework_id
            WHERE n.id = %s
            """,
            (node_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "no such control")

        cur.execute(
            """
            SELECT c.key, c.label, cc.weight
            FROM control_concept cc JOIN concept c ON c.id = cc.concept_id
            WHERE cc.control_node_id = %s ORDER BY cc.weight DESC
            """,
            (node_id,),
        )
        row["concepts"] = cur.fetchall()
    return row


@app.get("/api/control/{node_id}/neighbourhood")
def neighbourhood(
    node_id: int,
    depth: int = Query(1, ge=1, le=3),
    threshold: float = Query(0.0, ge=0.0, le=1.0),
) -> dict:
    """The queried control and the controls it relates to -- never full tables.

    Divergences come back flagged rather than hidden: showing a reviewer where
    two frameworks deliberately fail to reconcile is the point. It is
    propagation that must not cross them, not display.
    """
    with cursor() as cur:
        cur.execute("SELECT 1 FROM control_node WHERE id = %s", (node_id,))
        if not cur.fetchone():
            raise HTTPException(404, "no such control")

        cur.execute(
            "SELECT * FROM control_neighbourhood(%s::bigint, %s::integer, %s::numeric)",
            (node_id, depth, threshold),
        )
        rows = cur.fetchall()

        # which controls already carry evidence, so the UI can show coverage
        cur.execute(
            """
            SELECT l.control_node_id, l.link_kind, count(*) AS n
            FROM evidence_control_link l
            WHERE l.control_node_id = ANY(%s)
            GROUP BY l.control_node_id, l.link_kind
            """,
            ([r["node_id"] for r in rows],),
        )
        coverage: dict[int, dict[str, int]] = {}
        for item in cur.fetchall():
            coverage.setdefault(item["control_node_id"], {})[item["link_kind"]] = item["n"]

    root = next((r for r in rows if r["depth"] == 0), None)
    related = [r for r in rows if r["depth"] > 0]
    for r in rows:
        r["evidence"] = coverage.get(r["node_id"], {})

    return {
        "root": root,
        "related": related,
        "counts": {
            "total": len(related),
            "by_framework": _count(related, "framework_key"),
            "by_tier": _count(related, "provenance_tier"),
            "divergences": sum(1 for r in related if r["is_divergence"]),
        },
    }


def _count(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if value:
            out[value] = out.get(value, 0) + 1
    return out


@app.get("/api/systems")
def systems() -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT s.*, count(DISTINCT e.id) AS evidence_count,
                   max(e.collected_at) AS last_evidence_at
            FROM ai_system s LEFT JOIN evidence e ON e.ai_system_id = s.id
            GROUP BY s.id ORDER BY s.key
            """
        )
        return cur.fetchall()


@app.get("/api/systems/{key}/coverage")
def coverage(key: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT framework_key, framework_name, total_controls,
                   direct_covered, propagated_covered, stale_covered
            FROM control_coverage WHERE ai_system_key = %s
            ORDER BY framework_key
            """,
            (key,),
        )
        return cur.fetchall()


@app.get("/api/systems/{key}/evidence")
def system_evidence(key: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.collector, e.artifact_type, e.uri, e.sha256,
                   e.collected_at, e.valid_until, e.summary,
                   (e.valid_until IS NOT NULL AND e.valid_until < now()) AS stale,
                   count(l.id) FILTER (WHERE l.link_kind = 'direct') AS direct_links,
                   count(l.id) FILTER (WHERE l.link_kind = 'propagated') AS propagated_links
            FROM evidence e
            JOIN ai_system s ON s.id = e.ai_system_id
            LEFT JOIN evidence_control_link l ON l.evidence_id = e.id
            WHERE s.key = %s
            GROUP BY e.id ORDER BY e.collected_at DESC
            """,
            (key,),
        )
        return cur.fetchall()


@app.get("/api/evidence/{evidence_id}/propagation")
def propagation(evidence_id: int) -> dict:
    """Where one artifact landed, and the exact path it took to get there.

    This is what the UI animates: the direct tag first, then each control the
    graph says the same artifact also speaks to, in hop order.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.artifact_type, e.uri, e.sha256, e.summary,
                   e.collected_at, e.collector, s.key AS ai_system_key
            FROM evidence e JOIN ai_system s ON s.id = e.ai_system_id
            WHERE e.id = %s
            """,
            (evidence_id,),
        )
        artifact = cur.fetchone()
        if not artifact:
            raise HTTPException(404, "no such evidence")

        cur.execute(
            """
            SELECT l.id, l.link_kind, l.derived_strength, l.edge_path,
                   l.needs_review, l.rationale, l.asserted_by,
                   coalesce(array_length(l.edge_path, 1), 0) AS hops,
                   n.id AS node_id, n.native_ref, n.title,
                   f.key AS framework_key, f.name AS framework_name
            FROM evidence_control_link l
            JOIN control_node n ON n.id = l.control_node_id
            JOIN framework f ON f.id = n.framework_id
            WHERE l.evidence_id = %s
            ORDER BY (l.link_kind = 'direct') DESC, hops, l.derived_strength DESC
            """,
            (evidence_id,),
        )
        links = cur.fetchall()

        # resolve the edges each propagated link travelled, so the UI can draw
        # the actual route rather than a straight line to the target
        edge_ids = sorted({eid for l in links for eid in (l["edge_path"] or [])})
        edges: list[dict] = []
        if edge_ids:
            cur.execute(
                """
                SELECT e.id, e.src_node_id, e.dst_node_id, e.edge_type,
                       e.provenance_tier, e.strength, e.confidence, e.rationale,
                       e.source_citation
                FROM control_edge e WHERE e.id = ANY(%s)
                """,
                (edge_ids,),
            )
            edges = cur.fetchall()

    return {
        "evidence": artifact,
        "links": links,
        "edges": edges,
        "counts": {
            "direct": sum(1 for l in links if l["link_kind"] == "direct"),
            "propagated": sum(1 for l in links if l["link_kind"] == "propagated"),
            "frameworks_reached": len({l["framework_key"] for l in links}),
        },
    }


@app.get("/api/observability")
def observability() -> dict:
    """Collector health and evidence freshness.

    Evidence has a shelf life; a control 'covered' by an expired artifact is
    not covered. This is what makes that visible rather than a footnote.
    """
    max_age_days = int(os.environ.get("EVIDENCE_MAX_AGE_DAYS", 30))
    with cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (collector)
                   collector, status, started_at, finished_at, artifacts_found,
                   jsonb_array_length(errors) AS error_count,
                   extract(epoch FROM (finished_at - started_at)) AS duration_s
            FROM collector_run ORDER BY collector, started_at DESC
            """
        )
        runs = cur.fetchall()

        cur.execute(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE valid_until IS NOT NULL AND valid_until < now()) AS expired,
                   count(*) FILTER (WHERE collected_at < now() - make_interval(days => %s)) AS beyond_slo,
                   min(collected_at) AS oldest,
                   max(collected_at) AS newest
            FROM evidence
            """,
            (max_age_days,),
        )
        freshness = cur.fetchone()

        cur.execute(
            """
            SELECT decision_type, count(*) AS n, round(avg(confidence), 2) AS avg_confidence,
                   count(*) FILTER (WHERE human_override IS NOT NULL) AS overridden
            FROM ai_decision GROUP BY decision_type
            """
        )
        decisions = cur.fetchall()

    return {
        "collector_runs": runs,
        "evidence_freshness": {**freshness, "slo_days": max_age_days},
        "ai_decisions": decisions,
    }


@app.get("/api/graph/stats")
def stats() -> dict:
    with cursor() as cur:
        cur.execute(
            """
            SELECT provenance_tier::text AS tier, edge_type::text AS type, count(*) AS n,
                   round(avg(strength), 2) AS avg_strength,
                   round(avg(confidence), 2) AS avg_confidence
            FROM control_edge GROUP BY 1, 2 ORDER BY 1, 3 DESC
            """
        )
        return {"edges": cur.fetchall()}
