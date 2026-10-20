"""Build the control graph: frameworks, controls, concepts and every edge tier.

Idempotent. Running it again rebuilds the graph from the seed files, so the
seeds are the source of truth and the database is derived.

Order matters:
  1. frameworks
  2. native per-framework tables
  3. projection into control_node (the central hub)
  4. concepts, and concept tagging of every control
  5. T1 edges  -- imported from NIST's published crosswalk
  6. T3 edges  -- generated from the shared Annex SL clause numbering
  7. T2 and T5 -- regulatory and curated, from curated_edges.json
  8. T4 edges  -- derived from concept overlap, last so curated edges win

Usage:
    uv run --with 'psycopg[binary]' python graph/load.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontology import (  # noqa: E402
    DEFAULT_SIMILARITY_THRESHOLD,
    TaggedNode,
    infer_edges,
    load_concepts,
    tag,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED = REPO_ROOT / "db" / "seed"

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql:///control_graph"
)

#: Strength assigned to a NIST-published mapping. The crosswalk asserts that two
#: items are related but says nothing about how strongly, so this is our value,
#: not NIST's -- recorded explicitly rather than implied.
T1_STRENGTH = 0.75
T1_CONFIDENCE = 0.95
#: Confidence for a mapping whose source title disagrees with its own reference
#: (see graph/parse_crosswalk.py). Loaded, but visibly distrusted.
T1_DISPUTED_CONFIDENCE = 0.45

#: Annex SL clause parallels are mechanical, hence full confidence, but they are
#: a claim about management-system shape and not about control content, so the
#: strength is deliberately moderate.
T3_STRENGTH = 0.70
T3_CONFIDENCE = 1.00


def connect():
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        sys.exit("psycopg is required: uv run --with 'psycopg[binary]' python graph/load.py")
    return psycopg.connect(DATABASE_URL, autocommit=False)


def reset(cur) -> None:
    """Clear derived state so a reload is a rebuild, not an append."""
    cur.execute(
        """
        TRUNCATE control_edge, control_concept, concept, control_node,
                 iso27001_control, iso42001_control, eu_ai_act_provision,
                 nist_airmf_subcategory, framework
        RESTART IDENTITY CASCADE
        """
    )


# ---------------------------------------------------------------------------
# frameworks and native tables
# ---------------------------------------------------------------------------

FRAMEWORKS = [
    dict(
        key="iso27001",
        name="ISO/IEC 27001 Information security management systems",
        version="2022",
        authority="ISO/IEC",
        legal_status="voluntary_standard",
        jurisdiction=None,
        citation_url="https://www.iso.org/standard/27001",
        text_reusable=False,
        notes="Copyrighted. Identifiers and short titles only; statements are our paraphrase.",
    ),
    dict(
        key="iso42001",
        name="ISO/IEC 42001 Artificial intelligence management system",
        version="2023",
        authority="ISO/IEC",
        legal_status="voluntary_standard",
        jurisdiction=None,
        citation_url="https://www.iso.org/standard/42001",
        text_reusable=False,
        notes="Copyrighted. Identifiers and short titles only; statements are our paraphrase. "
        "Not a harmonised standard: confers no presumption of conformity under EU AI Act Art. 40.",
    ),
    dict(
        key="eu_ai_act",
        name="EU Artificial Intelligence Act",
        version="Regulation (EU) 2024/1689",
        authority="European Union",
        legal_status="binding_law",
        jurisdiction="EU",
        citation_url="https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
        text_reusable=True,
        notes="Operative statements with per-article citation to the Official Journal text.",
    ),
    dict(
        key="nist_ai_rmf",
        name="NIST AI Risk Management Framework",
        version="1.0 (AI 100-1)",
        authority="NIST",
        legal_status="voluntary_guidance",
        jurisdiction="US",
        citation_url="https://www.nist.gov/itl/ai-risk-management-framework",
        text_reusable=True,
        notes="US Government public domain. Outcome text extracted mechanically from NIST's own crosswalk PDF.",
    ),
]


def load_frameworks(cur, extra: list[dict]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for fw in FRAMEWORKS + [
        {k: v for k, v in f.items() if k != "nodes"} for f in extra
    ]:
        cur.execute(
            """
            INSERT INTO framework (key, name, version, authority, legal_status,
                                   jurisdiction, citation_url, text_reusable, notes)
            VALUES (%(key)s, %(name)s, %(version)s, %(authority)s, %(legal_status)s,
                    %(jurisdiction)s, %(citation_url)s, %(text_reusable)s, %(notes)s)
            RETURNING id
            """,
            {**fw, "jurisdiction": fw.get("jurisdiction"), "notes": fw.get("notes")},
        )
        ids[fw["key"]] = cur.fetchone()[0]
    return ids


def load_iso27001(cur, framework_id: int) -> None:
    with (SEED / "iso27001_controls.csv").open() as fh:
        for row in csv.DictReader(fh):
            cur.execute(
                """
                INSERT INTO iso27001_control (ref, kind, theme, title, paraphrase)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (row["ref"], row["kind"], row["theme"] or None, row["title"], row["paraphrase"]),
            )
            native_id = cur.fetchone()[0]
            _project(cur, framework_id, row["ref"], "iso27001_control", native_id,
                     row["title"], row["paraphrase"])


def load_iso42001(cur, framework_id: int) -> None:
    with (SEED / "iso42001_controls.csv").open() as fh:
        for row in csv.DictReader(fh):
            cur.execute(
                """
                INSERT INTO iso42001_control (ref, kind, objective, title, paraphrase)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (row["ref"], row["kind"], row["objective"] or None, row["title"],
                 row["paraphrase"]),
            )
            native_id = cur.fetchone()[0]
            _project(cur, framework_id, row["ref"], "iso42001_control", native_id,
                     row["title"], row["paraphrase"])


def load_eu_ai_act(cur, framework_id: int) -> None:
    data = json.loads((SEED / "eu_ai_act_provisions.json").read_text())
    for row in data["provisions"]:
        cur.execute(
            """
            INSERT INTO eu_ai_act_provision
                (ref, article, paragraph, title, text, risk_tier, obligation_holder, chapter)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (row["ref"], row["article"], row.get("paragraph"), row["title"], row["text"],
             row.get("risk_tier"), row.get("obligation_holder"), row.get("chapter")),
        )
        native_id = cur.fetchone()[0]
        _project(cur, framework_id, row["ref"], "eu_ai_act_provision", native_id,
                 row["title"], row["text"])


def load_nist(cur, framework_id: int) -> None:
    """NIST subcategories, with outcome text taken verbatim from NIST's crosswalk."""
    data = json.loads((SEED / "t1_nist_iso42001_edges.json").read_text())
    for row in data["subcategories"]:
        function, number = row["ref"].split()
        cur.execute(
            """
            INSERT INTO nist_airmf_subcategory (ref, function, category, outcome)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (row["ref"], function, f"{function} {number.split('.')[0]}", row["outcome"]),
        )
        native_id = cur.fetchone()[0]
        _project(cur, framework_id, row["ref"], "nist_airmf_subcategory", native_id,
                 row["outcome"][:200], row["outcome"])


def _project(cur, framework_id, ref, table, native_id, title, statement) -> None:
    """Project a native row into the central hub."""
    cur.execute(
        """
        INSERT INTO control_node (framework_id, native_ref, native_table, native_id,
                                  title, statement)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (framework_id, ref, table, native_id, title, statement),
    )


# ---------------------------------------------------------------------------
# edges
# ---------------------------------------------------------------------------

def node_index(cur) -> dict[tuple[str, str], int]:
    cur.execute(
        "SELECT f.key, n.native_ref, n.id FROM control_node n JOIN framework f ON f.id = n.framework_id"
    )
    return {(fw, ref): nid for fw, ref, nid in cur.fetchall()}


def insert_edge(cur, src, dst, edge_type, tier, strength, confidence,
                rationale, citation, created_by) -> bool:
    """Insert an edge, ignoring duplicates. Earlier tiers win by load order."""
    cur.execute(
        """
        INSERT INTO control_edge (src_node_id, dst_node_id, edge_type, provenance_tier,
                                  strength, confidence, rationale, source_citation, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (src_node_id, dst_node_id, edge_type) DO NOTHING
        RETURNING id
        """,
        (src, dst, edge_type, tier, strength, confidence, rationale, citation, created_by),
    )
    return cur.fetchone() is not None


def load_t1(cur, idx) -> tuple[int, int]:
    """NIST's published AI RMF to ISO/IEC 42001 crosswalk."""
    data = json.loads((SEED / "t1_nist_iso42001_edges.json").read_text())
    citation = data["source"]["document"]
    loaded = disputed = 0
    for edge in data["edges"]:
        src = idx.get(("nist_ai_rmf", edge["nist_ref"]))
        dst = idx.get(("iso42001", edge["iso42001_ref"]))
        if not src or not dst:
            continue
        is_disputed = edge.get("disputed", False)
        rationale = (
            f"Published by NIST as related. The crosswalk asserts relatedness only; "
            f"the relationship type and strength are assigned at load time."
        )
        if is_disputed:
            rationale += (
                f" DISPUTED: the source titles {edge['source_ref']} as "
                f"{edge['iso42001_title']!r}, which belongs to "
                f"{edge.get('suspected_ref', 'another reference')}. Loaded with reduced confidence."
            )
            disputed += 1
        if insert_edge(cur, src, dst, "supports", "T1_OFFICIAL", T1_STRENGTH,
                       T1_DISPUTED_CONFIDENCE if is_disputed else T1_CONFIDENCE,
                       rationale, citation, "import:nist_crosswalk"):
            loaded += 1
    return loaded, disputed


def load_t3(cur, idx) -> int:
    """Annex SL structural parallels between the two ISO management systems."""
    cur.execute("SELECT ref FROM iso27001_control WHERE kind = 'clause'")
    iso27_clauses = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT ref FROM iso42001_control WHERE kind = 'clause'")
    iso42_clauses = {r[0] for r in cur.fetchall()}

    loaded = 0
    for ref in sorted(iso27_clauses & iso42_clauses):
        src, dst = idx[("iso27001", ref)], idx[("iso42001", ref)]
        if insert_edge(
            cur, src, dst, "equivalent_to", "T3_STRUCTURAL", T3_STRENGTH, T3_CONFIDENCE,
            f"Clause {ref} occupies the same position in both management system standards "
            "under the ISO Annex SL Harmonized Structure. This is a claim about "
            "management-system shape, not about control content.",
            "ISO Annex SL Harmonized Structure", "generated:annex_sl",
        ):
            loaded += 1
    return loaded


def load_curated(cur, idx) -> dict[str, int]:
    data = json.loads((SEED / "curated_edges.json").read_text())
    counts = {"regulatory": 0, "curated": 0, "divergence": 0}

    for group, tier, label in (
        ("regulatory_edges", "T2_REGULATORY", "regulatory"),
        ("curated_edges", "T5_CURATED", "curated"),
        ("divergences", "T5_CURATED", "divergence"),
    ):
        for edge in data.get(group, []):
            src = idx.get((edge["src"]["framework"], edge["src"]["ref"]))
            dst = idx.get((edge["dst"]["framework"], edge["dst"]["ref"]))
            if not src or not dst:
                print(f"  ! unresolved {group}: {edge['src']} -> {edge['dst']}")
                continue
            if insert_edge(cur, src, dst, edge["edge_type"], tier, edge["strength"],
                           edge["confidence"], edge["rationale"],
                           edge.get("source_citation"), edge["created_by"]):
                counts[label] += 1
    return counts


def load_extra_framework_nodes(cur, framework_ids) -> None:
    data = json.loads((SEED / "curated_edges.json").read_text())
    for fw in data.get("extra_frameworks", []):
        for node in fw["nodes"]:
            cur.execute(
                """
                INSERT INTO control_node (framework_id, native_ref, native_table,
                                          native_id, title, statement)
                VALUES (%s, %s, 'framework', %s, %s, %s)
                """,
                (framework_ids[fw["key"]], node["ref"], framework_ids[fw["key"]],
                 node["title"], node["statement"]),
            )


def load_concepts_and_tag(cur) -> tuple[int, int, list[TaggedNode]]:
    concepts = load_concepts(SEED / "ontology.json")
    keys: dict[str, int] = {}
    # parents first so the FK resolves
    for concept in sorted(concepts, key=lambda c: c.parent is not None):
        cur.execute(
            """
            INSERT INTO concept (key, label, definition, parent_id, source)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (concept.key, concept.label, concept.definition,
             keys.get(concept.parent) if concept.parent else None, concept.source),
        )
        keys[concept.key] = cur.fetchone()[0]

    cur.execute(
        """
        SELECT n.id, f.key, n.native_ref, n.title, n.statement
        FROM control_node n JOIN framework f ON f.id = n.framework_id
        """
    )
    tagged: list[TaggedNode] = []
    taggings = 0
    for node_id, framework_key, ref, title, statement in cur.fetchall():
        vector, direct = tag(f"{title}\n{statement}", concepts)
        for concept_key, weight in vector.items():
            cur.execute(
                """
                INSERT INTO control_concept (control_node_id, concept_id, weight, assigned_by)
                VALUES (%s, %s, %s, 'rules:ontology.json')
                ON CONFLICT DO NOTHING
                """,
                (node_id, keys[concept_key], round(weight, 2)),
            )
            taggings += 1
        tagged.append(TaggedNode(node_id, framework_key, ref, title, vector, direct))

    return len(concepts), taggings, tagged


def main() -> int:
    threshold = float(os.environ.get("ONTOLOGY_THRESHOLD", DEFAULT_SIMILARITY_THRESHOLD))
    extra = json.loads((SEED / "curated_edges.json").read_text()).get("extra_frameworks", [])

    with connect() as conn, conn.cursor() as cur:
        reset(cur)

        framework_ids = load_frameworks(cur, extra)
        load_iso27001(cur, framework_ids["iso27001"])
        load_iso42001(cur, framework_ids["iso42001"])
        load_eu_ai_act(cur, framework_ids["eu_ai_act"])
        load_nist(cur, framework_ids["nist_ai_rmf"])
        load_extra_framework_nodes(cur, framework_ids)

        cur.execute("SELECT f.key, count(*) FROM control_node n JOIN framework f ON f.id=n.framework_id GROUP BY f.key ORDER BY f.key")
        print("nodes")
        for key, count in cur.fetchall():
            print(f"  {key:14} {count:4}")

        n_concepts, n_taggings, tagged = load_concepts_and_tag(cur)
        untagged = [t for t in tagged if not t.vector]
        print(f"\nontology\n  concepts {n_concepts}  taggings {n_taggings}  untagged nodes {len(untagged)}")
        for node in untagged[:10]:
            print(f"    ! no concepts: {node.framework_key} {node.native_ref}")

        idx = node_index(cur)

        t1, disputed = load_t1(cur, idx)
        t3 = load_t3(cur, idx)
        curated = load_curated(cur, idx)

        cur.execute("SELECT key FROM framework WHERE NOT text_reusable")
        paraphrase_frameworks = {r[0] for r in cur.fetchall()}
        inferred = infer_edges(tagged, load_concepts(SEED / "ontology.json"),
                               paraphrase_frameworks, threshold=threshold)
        t4 = 0
        for edge in inferred:
            if insert_edge(cur, edge.src_node_id, edge.dst_node_id, edge.edge_type,
                           "T4_ONTOLOGY", edge.strength, edge.confidence,
                           edge.rationale, f"ontology.json @ threshold {threshold}",
                           "derived:ontology"):
                t4 += 1

        print(f"\nedges")
        print(f"  T1_OFFICIAL    {t1:5}   ({disputed} disputed, loaded with reduced confidence)")
        print(f"  T2_REGULATORY  {curated['regulatory']:5}")
        print(f"  T3_STRUCTURAL  {t3:5}")
        print(f"  T4_ONTOLOGY    {t4:5}   (threshold {threshold})")
        print(f"  T5_CURATED     {curated['curated'] + curated['divergence']:5}"
              f"   ({curated['divergence']} divergences)")

        conn.commit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
