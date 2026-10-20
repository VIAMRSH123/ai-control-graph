"""Integrity and behaviour tests for the control graph.

These are the assertions that would catch the failure modes that actually
matter here, rather than restating the schema:

  - the FDIS B.x -> published A.x normalisation resolved completely, because a
    silent failure there corrupts every Annex A edge invisibly
  - evidence propagates across frameworks from a single tag
  - evidence does NOT cross a divergence, which is the one claim that would be
    legally wrong rather than merely imprecise
  - only an OJ-cited harmonised standard can grant presumption of conformity

    make verify
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "db" / "seed"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///control_graph")


@pytest.fixture(scope="module")
def conn():
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(DATABASE_URL)
    yield connection
    connection.close()


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()[0]


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


# --------------------------------------------------------------- seed data

def test_iso27001_has_93_annex_controls_in_four_themes():
    data = list(csv.DictReader((SEED / "iso27001_controls.csv").open()))
    annex = [r for r in data if r["kind"] == "annex_a"]
    assert len(annex) == 93
    counts = {t: sum(1 for r in annex if r["theme"] == t) for t in
              ("organizational", "people", "physical", "technological")}
    assert counts == {"organizational": 37, "people": 8, "physical": 14, "technological": 34}


def test_iso42001_has_38_annex_controls():
    data = list(csv.DictReader((SEED / "iso42001_controls.csv").open()))
    assert sum(1 for r in data if r["kind"] == "annex_a") == 38


def test_nist_has_72_subcategories():
    data = json.loads((SEED / "t1_nist_iso42001_edges.json").read_text())
    assert len(data["subcategories"]) == 72
    counts: dict[str, int] = {}
    for sub in data["subcategories"]:
        function = sub["ref"].split()[0]
        counts[function] = counts.get(function, 0) + 1
    assert counts == {"GOVERN": 19, "MAP": 18, "MEASURE": 22, "MANAGE": 13}


def test_fdis_annex_references_all_resolve_to_published_controls():
    """The normalisation that the whole crosswalk depends on.

    NIST's crosswalk numbers Annex A controls against ISO/IEC FDIS 42001 (B.x).
    The published standard uses A.x. If any reference fails to resolve, the edge
    points at a control that does not exist -- and nothing else would notice.
    """
    published = {r["ref"] for r in csv.DictReader((SEED / "iso42001_controls.csv").open())}
    data = json.loads((SEED / "t1_nist_iso42001_edges.json").read_text())

    unresolved = sorted({
        f"{e['source_ref']} -> {e['iso42001_ref']}"
        for e in data["edges"] if e["iso42001_ref"] not in published
    })
    assert not unresolved, f"unresolved after normalisation: {unresolved}"

    # and the normalisation was actually exercised
    assert any(e["source_ref"].startswith("B.") for e in data["edges"])


def test_source_defects_are_recorded_not_silently_corrected():
    """Three references in NIST's document carry another control's title.

    They are loaded, flagged and explained rather than quietly repaired, so a
    reviewer can decide. Losing that record would be worse than the defect.
    """
    data = json.loads((SEED / "t1_nist_iso42001_edges.json").read_text())
    anomalies = data["source_anomalies"]
    assert anomalies, "expected the parser to detect title/reference disagreements"
    for anomaly in anomalies:
        assert anomaly["title_in_source"] != anomaly["canonical_title_for_ref"]
    assert any(e.get("disputed") for e in data["edges"])


# ---------------------------------------------------------------- the graph

def test_every_framework_is_loaded(conn):
    keys = {r[0] for r in rows(conn, "SELECT key FROM framework")}
    assert keys == {"iso27001", "iso42001", "eu_ai_act", "nist_ai_rmf", "en18286"}


def test_no_orphan_nodes_and_no_self_edges(conn):
    assert scalar(conn, """
        SELECT count(*) FROM control_node n
        LEFT JOIN framework f ON f.id = n.framework_id WHERE f.id IS NULL
    """) == 0
    assert scalar(conn, "SELECT count(*) FROM control_edge WHERE src_node_id = dst_node_id") == 0


def test_every_edge_carries_a_rationale(conn):
    """An edge nobody can explain is an edge nobody can audit."""
    assert scalar(conn, """
        SELECT count(*) FROM control_edge
        WHERE rationale IS NULL OR btrim(rationale) = ''
    """) == 0


def test_all_five_provenance_tiers_are_populated(conn):
    tiers = {r[0] for r in rows(conn, "SELECT DISTINCT provenance_tier::text FROM control_edge")}
    assert tiers == {"T1_OFFICIAL", "T2_REGULATORY", "T3_STRUCTURAL", "T4_ONTOLOGY", "T5_CURATED"}


def test_only_a_harmonised_standard_grants_presumption_of_conformity(conn):
    """D3, the claim that would be legally wrong rather than merely imprecise.

    ISO/IEC 42001 is not a harmonised standard. Nothing but an OJ-cited one may
    carry this edge type, and the schema enforces it.
    """
    offenders = rows(conn, """
        SELECT f.key, n.native_ref FROM control_edge e
        JOIN control_node n ON n.id = e.src_node_id
        JOIN framework f ON f.id = n.framework_id
        WHERE e.edge_type = 'grants_presumption_of_conformity'
          AND f.legal_status <> 'harmonised_standard'
    """)
    assert not offenders, f"non-harmonised standard granting presumption: {offenders}"


def test_schema_rejects_presumption_from_a_non_regulatory_tier(conn):
    """The constraint holds against a direct INSERT, not just by convention."""
    psycopg = pytest.importorskip("psycopg")
    src, dst = rows(conn, "SELECT id FROM control_node ORDER BY id LIMIT 2")
    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO control_edge (src_node_id, dst_node_id, edge_type,
                    provenance_tier, strength, confidence, rationale, created_by)
                VALUES (%s, %s, 'grants_presumption_of_conformity', 'T4_ONTOLOGY',
                        1.0, 1.0, 'should be rejected', 'test')
                """,
                (src[0], dst[0]),
            )
    conn.rollback()


def test_divergences_exist_and_are_curated(conn):
    divergences = rows(conn, """
        SELECT e.provenance_tier::text, e.rationale FROM control_edge e
        WHERE e.edge_type = 'diverges_from'
    """)
    assert len(divergences) >= 3
    for tier, rationale in divergences:
        assert tier == "T5_CURATED", "a divergence is a human judgement, never derived"
        assert len(rationale) > 80, "a divergence must be argued, not asserted"


# ----------------------------------------------------------- propagation

@pytest.fixture
def tagged_evidence(conn):
    """Tag one artifact to ISO 42001 A.6.2.8 and propagate from it."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ai_system (key, name, provider, environment)
            VALUES ('pytest-system', 'Test System', 'test', 'test')
            ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name RETURNING id
        """)
        system_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO evidence (ai_system_id, collector, artifact_type, uri, sha256, payload, summary)
            VALUES (%s, 'test', 'event_logging', 'test://logging', repeat('c', 64),
                    '{"enabled": true}'::jsonb, 'Event logging enabled')
            RETURNING id
        """, (system_id,))
        evidence_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO evidence_control_link (evidence_id, control_node_id, link_kind, asserted_by)
            SELECT %s, n.id, 'direct', 'test'
            FROM control_node n JOIN framework f ON f.id = n.framework_id
            WHERE f.key = 'iso42001' AND n.native_ref = 'A.6.2.8'
        """, (evidence_id,))
        cur.execute("SELECT propagate_evidence(%s::bigint, 0.45::numeric, 3::integer)", (evidence_id,))
        spread = cur.fetchone()[0]
    conn.commit()
    yield evidence_id, spread
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ai_system WHERE key = 'pytest-system'")
    conn.commit()


def test_one_tag_reaches_every_other_framework(conn, tagged_evidence):
    """The whole point of the crosswalk, asserted.

    Logging evidence tagged to a single ISO 42001 control must surface the
    equivalent obligation in ISO 27001, the EU AI Act and NIST AI RMF.
    """
    evidence_id, spread = tagged_evidence
    assert spread > 0

    reached = {r[0] for r in rows(conn, """
        SELECT f.key FROM evidence_control_link l
        JOIN control_node n ON n.id = l.control_node_id
        JOIN framework f ON f.id = n.framework_id
        WHERE l.evidence_id = %s
    """, (evidence_id,))}
    assert {"iso42001", "iso27001", "eu_ai_act", "nist_ai_rmf"} <= reached

    refs = {r[0] for r in rows(conn, """
        SELECT n.native_ref FROM evidence_control_link l
        JOIN control_node n ON n.id = l.control_node_id
        WHERE l.evidence_id = %s
    """, (evidence_id,))}
    # the specific obligations a logging artifact must reach
    assert "A.8.15" in refs, "ISO 27001 Logging not reached"
    assert "Art.12" in refs, "EU AI Act record-keeping not reached"


def test_propagated_links_carry_their_path_and_need_review(conn, tagged_evidence):
    """A propagated link is a claim that can be explained, not an assertion."""
    evidence_id, _ = tagged_evidence
    propagated = rows(conn, """
        SELECT edge_path, derived_strength, needs_review, origin_link_id
        FROM evidence_control_link
        WHERE evidence_id = %s AND link_kind = 'propagated'
    """, (evidence_id,))
    assert propagated
    for path, strength, needs_review, origin in propagated:
        assert path, "propagated link without an edge path cannot be explained"
        assert 0 < float(strength) <= 1
        assert needs_review is True
        assert origin is not None


def test_strength_decays_with_distance(conn, tagged_evidence):
    """A two-hop claim must be weaker than a one-hop claim."""
    evidence_id, _ = tagged_evidence
    by_hops: dict[int, list[float]] = {}
    for path, strength in rows(conn, """
        SELECT edge_path, derived_strength FROM evidence_control_link
        WHERE evidence_id = %s AND link_kind = 'propagated'
    """, (evidence_id,)):
        by_hops.setdefault(len(path), []).append(float(strength))
    if len(by_hops) > 1:
        depths = sorted(by_hops)
        assert max(by_hops[depths[-1]]) <= max(by_hops[depths[0]])


def test_evidence_never_crosses_a_divergence(conn):
    """The guard that stops the one legally false claim this tool could make.

    ISO 42001 conformity confers no presumption under EU AI Act Art.40. Evidence
    tagged to the AI management system clause must never surface as Art.40
    coverage, and the block belongs in traversal, not presentation.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ai_system (key, name, provider, environment)
            VALUES ('pytest-divergence', 'Divergence Test', 'test', 'test')
            ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name RETURNING id
        """)
        system_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO evidence (ai_system_id, collector, artifact_type, uri, sha256, payload, summary)
            VALUES (%s, 'test', 'ams_policy', 'test://ams', repeat('d', 64),
                    '{"documented": true}'::jsonb, 'AI management system documented')
            RETURNING id
        """, (system_id,))
        evidence_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO evidence_control_link (evidence_id, control_node_id, link_kind, asserted_by)
            SELECT %s, n.id, 'direct', 'test'
            FROM control_node n JOIN framework f ON f.id = n.framework_id
            WHERE f.key = 'iso42001' AND n.native_ref = '4.4'
        """, (evidence_id,))
        cur.execute("SELECT propagate_evidence(%s::bigint, 0.45::numeric, 3::integer)", (evidence_id,))

        cur.execute("""
            SELECT n.native_ref FROM evidence_control_link l
            JOIN control_node n ON n.id = l.control_node_id
            JOIN framework f ON f.id = n.framework_id
            WHERE l.evidence_id = %s AND f.key = 'eu_ai_act' AND n.native_ref = 'Art.40'
        """, (evidence_id,))
        leaked = cur.fetchall()

        cur.execute("DELETE FROM ai_system WHERE key = 'pytest-divergence'")
    conn.commit()

    assert not leaked, (
        "ISO 42001 evidence reached EU AI Act Art.40 presumption of conformity. "
        "ISO 42001 is not a harmonised standard; this claim is legally false."
    )


def test_divergence_is_still_visible_when_queried(conn):
    """Blocked for propagation, shown for review. Those are different things."""
    node_id = scalar(conn, """
        SELECT n.id FROM control_node n JOIN framework f ON f.id = n.framework_id
        WHERE f.key = 'iso42001' AND n.native_ref = '4.4'
    """)
    divergences = rows(conn, """
        SELECT native_ref, is_divergence FROM control_neighbourhood(%s::bigint, 1::integer, 0.0::numeric)
        WHERE is_divergence
    """, (node_id,))
    assert any(ref == "Art.40" for ref, _ in divergences)
