-- ============================================================================
-- AI Control Graph — schema
--
-- A knowledge graph over four compliance frameworks, in plain Postgres.
-- No extensions. Traversal is recursive CTEs. See docs/DECISIONS.md D1.
--
-- Shape:
--   framework              one row per standard/regulation
--   <framework>_*          native tables, each keeping its own structure
--   control_node           THE CENTRAL HUB — one node per control, any framework
--   concept/control_concept the ontology layer
--   control_edge           typed, weighted, provenance-tagged relationships
--   evidence/*             artifacts collected from live systems, and where they land
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Enumerations
-- ---------------------------------------------------------------------------

-- How a relationship came to exist. This is the distinction that makes the
-- crosswalk reviewable: an edge imported from NIST's published crosswalk is not
-- the same kind of claim as one inferred from concept similarity.
-- See docs/CROSSWALK_METHODOLOGY.md.
CREATE TYPE provenance_tier AS ENUM (
    'T1_OFFICIAL',    -- imported from a published crosswalk, cited
    'T2_REGULATORY',  -- EU AI Act Art. 40 presumption via OJ-cited harmonised standard
    'T3_STRUCTURAL',  -- ISO Annex SL shared management-system structure, mechanical
    'T4_ONTOLOGY',    -- derived from shared concepts, algorithmic and explainable
    'T5_CURATED'      -- hand-authored by a named human, with rationale
);

-- What the relationship actually means.
CREATE TYPE edge_type AS ENUM (
    'grants_presumption_of_conformity', -- legally operative; T2 only
    'equivalent_to',
    'broader_than',
    'narrower_than',
    'supports',
    'partially_satisfies',
    'informs',
    'diverges_from'   -- same subject, NOT interchangeable. Propagation stops here.
);

CREATE TYPE link_kind AS ENUM (
    'direct',      -- an agent or human tagged this evidence to this control
    'propagated'   -- reached by graph traversal from a direct link; a claim, not a fact
);

CREATE TYPE legal_status AS ENUM (
    'binding_law',
    'harmonised_standard',  -- confers presumption of conformity once OJ-cited
    'voluntary_standard',
    'voluntary_guidance'
);

-- ---------------------------------------------------------------------------
-- Frameworks
-- ---------------------------------------------------------------------------

CREATE TABLE framework (
    id            smallserial PRIMARY KEY,
    key           text NOT NULL UNIQUE,     -- iso27001 | iso42001 | eu_ai_act | nist_ai_rmf
    name          text NOT NULL,
    version       text NOT NULL,
    authority     text NOT NULL,            -- ISO/IEC, European Union, NIST
    legal_status  legal_status NOT NULL,
    jurisdiction  text,                     -- NULL = international
    citation_url  text,
    -- TRUE where we may store normative text; FALSE where copyright forbids it
    -- and we store identifiers plus our own paraphrase instead. See D5.
    text_reusable boolean NOT NULL,
    notes         text
);

COMMENT ON COLUMN framework.text_reusable IS
    'FALSE for ISO standards (copyrighted, sold). Controls from these frameworks '
    'carry a paraphrase rather than normative text, which caps confidence on any '
    'ontology-derived edge touching them.';

-- ---------------------------------------------------------------------------
-- Native per-framework tables
--
-- Each framework keeps its own structure rather than being forced into a shared
-- shape. ISO has themes and annexes; the EU AI Act has articles and paragraphs
-- with an obligation holder; NIST has functions and categories. Flattening these
-- would discard the structure that makes each framework navigable on its terms.
-- ---------------------------------------------------------------------------

-- ISO/IEC 27001:2022 — management system clauses 4-10 plus Annex A (93 controls
-- in 4 themes). Clauses are included so the Annex SL structural tier has
-- something to connect to; see docs/CROSSWALK_METHODOLOGY.md section 4.1.
CREATE TABLE iso27001_control (
    id         serial PRIMARY KEY,
    ref        text NOT NULL UNIQUE,        -- '6.1.2' (clause) or 'A.5.1' (annex)
    kind       text NOT NULL,               -- clause | annex_a
    theme      text,                        -- annex only: organizational|people|physical|technological
    title      text NOT NULL,
    paraphrase text NOT NULL,               -- ours, not ISO's. See D5.
    CONSTRAINT iso27001_kind_valid CHECK (kind IN ('clause','annex_a')),
    CONSTRAINT iso27001_theme_valid CHECK (
        (kind = 'annex_a' AND theme IN ('organizational','people','physical','technological'))
        OR (kind = 'clause' AND theme IS NULL)
    )
);

-- ISO/IEC 42001:2023 — management system clauses 4–10 plus Annex A controls
CREATE TABLE iso42001_control (
    id         serial PRIMARY KEY,
    ref        text NOT NULL UNIQUE,        -- '6.1.2' (clause) or 'A.6.2.6' (annex)
    kind       text NOT NULL,               -- clause | annex_a
    objective  text,                        -- A.2 … A.10, for annex controls
    title      text NOT NULL,
    paraphrase text NOT NULL,               -- ours, not ISO's. See D5.
    CONSTRAINT iso42001_kind_valid CHECK (kind IN ('clause','annex_a')),
    -- annex controls belong to an objective; clauses do not
    CONSTRAINT iso42001_objective_present CHECK (
        (kind = 'annex_a' AND objective IS NOT NULL) OR kind = 'clause'
    )
);

-- EU AI Act (Reg. (EU) 2024/1689) — verbatim OJ text, freely reusable
CREATE TABLE eu_ai_act_provision (
    id                serial PRIMARY KEY,
    ref               text NOT NULL UNIQUE, -- 'Art.9' , 'Art.14(4)'
    article           integer NOT NULL,
    paragraph         text,
    title             text NOT NULL,
    text              text NOT NULL,        -- verbatim; OJ text is reusable
    risk_tier         text,                 -- high_risk | gpai | limited | all
    obligation_holder text,                 -- provider | deployer | importer | distributor
    chapter           text
);

-- NIST AI RMF 1.0 — verbatim outcome text, US Gov public domain
CREATE TABLE nist_airmf_subcategory (
    id       serial PRIMARY KEY,
    ref      text NOT NULL UNIQUE,          -- 'GOVERN 1.1'
    function text NOT NULL,                 -- GOVERN | MAP | MEASURE | MANAGE
    category text NOT NULL,                 -- 'GOVERN 1'
    outcome  text NOT NULL,                 -- verbatim; public domain
    CONSTRAINT nist_function_valid CHECK (
        function IN ('GOVERN','MAP','MEASURE','MANAGE')
    )
);

-- ---------------------------------------------------------------------------
-- control_node — THE CENTRAL HUB
--
-- Every control in every framework projects into exactly one node here. All
-- relationships, all evidence and all traversal operate on nodes, so adding a
-- fifth framework means adding a native table and projecting it — nothing
-- downstream changes.
-- ---------------------------------------------------------------------------

CREATE TABLE control_node (
    id           bigserial PRIMARY KEY,
    framework_id smallint NOT NULL REFERENCES framework(id),
    native_ref   text NOT NULL,             -- as cited in its own framework
    native_table text NOT NULL,             -- which table the row lives in
    native_id    integer NOT NULL,          -- its PK there
    title        text NOT NULL,
    -- normative text where licensing allows, our paraphrase where it does not.
    -- This is what the ontology tagger reads.
    statement    text NOT NULL,
    UNIQUE (framework_id, native_ref),
    UNIQUE (native_table, native_id)
);

CREATE INDEX control_node_framework_idx ON control_node(framework_id);
-- case-insensitive lookup so the UI accepts 'iso 42001 a.6.2.6'
CREATE INDEX control_node_ref_lower_idx ON control_node(lower(native_ref));

-- ---------------------------------------------------------------------------
-- Ontology layer
--
-- The reason this exists: there is no official article-level mapping between
-- NIST AI RMF and the EU AI Act, so those edges must be derived. Deriving them
-- through a shared concept vocabulary means onboarding a new framework requires
-- tagging it once, not authoring mappings against every framework already here.
-- The top level is rooted in NIST's seven trustworthiness characteristics so it
-- is officially sourced rather than invented. See docs/ONTOLOGY.md.
-- ---------------------------------------------------------------------------

CREATE TABLE concept (
    id         serial PRIMARY KEY,
    key        text NOT NULL UNIQUE,        -- human-oversight, record-keeping
    label      text NOT NULL,
    definition text NOT NULL,
    parent_id  integer REFERENCES concept(id),
    -- where this concept came from, e.g. 'NIST AI RMF trustworthiness characteristic'
    source     text,
    CONSTRAINT concept_not_own_parent CHECK (parent_id IS DISTINCT FROM id)
);

CREATE TABLE control_concept (
    control_node_id bigint NOT NULL REFERENCES control_node(id) ON DELETE CASCADE,
    concept_id      integer NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    weight          numeric(3,2) NOT NULL,
    assigned_by     text NOT NULL,          -- human:<name> | agent:<model>
    PRIMARY KEY (control_node_id, concept_id),
    CONSTRAINT control_concept_weight_range CHECK (weight > 0 AND weight <= 1)
);

CREATE INDEX control_concept_concept_idx ON control_concept(concept_id);

-- ---------------------------------------------------------------------------
-- control_edge — the crosswalk itself
-- ---------------------------------------------------------------------------

CREATE TABLE control_edge (
    id              bigserial PRIMARY KEY,
    src_node_id     bigint NOT NULL REFERENCES control_node(id) ON DELETE CASCADE,
    dst_node_id     bigint NOT NULL REFERENCES control_node(id) ON DELETE CASCADE,
    edge_type       edge_type NOT NULL,
    provenance_tier provenance_tier NOT NULL,

    -- how strongly the controls relate; drives propagation decay
    strength        numeric(3,2) NOT NULL,
    -- how much we trust the mapping itself. Deliberately separate from strength:
    -- a relationship can be strong but uncertain (ISO paraphrase-based tagging)
    -- or weak but certain (an explicitly narrow official mapping).
    confidence      numeric(3,2) NOT NULL,

    rationale       text NOT NULL,          -- why. Required on every edge.
    source_citation text,                   -- document, page, author
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    reviewed_by     text,
    reviewed_at     timestamptz,

    CONSTRAINT edge_no_self_loop CHECK (src_node_id <> dst_node_id),
    CONSTRAINT edge_strength_range CHECK (strength >= 0 AND strength <= 1),
    CONSTRAINT edge_confidence_range CHECK (confidence >= 0 AND confidence <= 1),

    -- D3, enforced in the schema rather than by convention: only an OJ-cited
    -- harmonised standard can grant presumption of conformity.
    CONSTRAINT edge_presumption_requires_regulatory_tier CHECK (
        edge_type <> 'grants_presumption_of_conformity'
        OR provenance_tier = 'T2_REGULATORY'
    ),

    -- a legally operative edge must cite its source
    CONSTRAINT edge_regulatory_requires_citation CHECK (
        provenance_tier <> 'T2_REGULATORY' OR source_citation IS NOT NULL
    ),

    UNIQUE (src_node_id, dst_node_id, edge_type)
);

CREATE INDEX control_edge_src_idx ON control_edge(src_node_id);
CREATE INDEX control_edge_dst_idx ON control_edge(dst_node_id);
CREATE INDEX control_edge_tier_idx ON control_edge(provenance_tier);

-- Traversal view.
--
-- Edges are stored once, directionally. Crosswalk relationships are navigable
-- both ways, so this view unions each edge with its inverse, flipping asymmetric
-- types (broader_than <-> narrower_than) so direction stays meaningful.
CREATE VIEW control_edge_bidirectional AS
    SELECT id, src_node_id, dst_node_id, edge_type, provenance_tier,
           strength, confidence, rationale, FALSE AS is_inverse
    FROM control_edge
    UNION ALL
    SELECT id, dst_node_id, src_node_id,
           CASE edge_type
               WHEN 'broader_than' THEN 'narrower_than'::edge_type
               WHEN 'narrower_than' THEN 'broader_than'::edge_type
               ELSE edge_type
           END,
           provenance_tier, strength, confidence, rationale, TRUE
    FROM control_edge;

-- ---------------------------------------------------------------------------
-- The governed estate and its evidence
-- ---------------------------------------------------------------------------

CREATE TABLE ai_system (
    id              serial PRIMARY KEY,
    key             text NOT NULL UNIQUE,   -- support-chat, cv-screener, doc-rag
    name            text NOT NULL,
    description     text,
    provider        text NOT NULL,          -- aws | github | vercel
    environment     text NOT NULL,          -- prod | staging | dev
    -- EU AI Act risk tier. cv-screener is Annex III high-risk (employment),
    -- which is why Articles 9-15 genuinely apply to it. See D12.
    risk_tier       text,
    owner           text,
    endpoint_url    text,
    aws_account_ref text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE collector_run (
    id              bigserial PRIMARY KEY,
    collector       text NOT NULL,          -- aws | github | probe
    ai_system_id    integer REFERENCES ai_system(id),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL DEFAULT 'running', -- running | ok | partial | failed
    artifacts_found integer NOT NULL DEFAULT 0,
    errors          jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT collector_run_status_valid CHECK (
        status IN ('running','ok','partial','failed')
    )
);

CREATE INDEX collector_run_collector_idx ON collector_run(collector, started_at DESC);

CREATE TABLE evidence (
    id            bigserial PRIMARY KEY,
    ai_system_id  integer NOT NULL REFERENCES ai_system(id) ON DELETE CASCADE,
    run_id        bigint REFERENCES collector_run(id),
    collector     text NOT NULL,            -- aws | github | probe
    artifact_type text NOT NULL,            -- s3_encryption | bedrock_guardrail | ...
    uri           text NOT NULL,            -- where it came from, e.g. an ARN
    -- tamper-evidence, and lets re-collection detect drift by comparison. D14.
    sha256        char(64) NOT NULL,
    payload       jsonb NOT NULL,
    collected_at  timestamptz NOT NULL DEFAULT now(),
    -- compliance evidence has a shelf life. A control "covered" by a stale
    -- artifact is not covered; the observability panel reports against this.
    valid_until   timestamptz,
    summary       text
);

CREATE INDEX evidence_system_idx ON evidence(ai_system_id);
CREATE INDEX evidence_sha_idx ON evidence(sha256);
CREATE INDEX evidence_collected_idx ON evidence(collected_at DESC);

-- Where evidence lands. One direct link per tagging decision, plus every
-- control the graph says it also speaks to.
CREATE TABLE evidence_control_link (
    id              bigserial PRIMARY KEY,
    evidence_id     bigint NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    control_node_id bigint NOT NULL REFERENCES control_node(id) ON DELETE CASCADE,
    link_kind       link_kind NOT NULL,

    -- propagation provenance: which direct link this descends from, and the
    -- exact edges traversed to get here. Lets the UI answer "why is this
    -- control covered?" with a path rather than an assertion.
    origin_link_id   bigint REFERENCES evidence_control_link(id) ON DELETE CASCADE,
    edge_path        bigint[],
    derived_strength numeric(4,3),

    asserted_by  text NOT NULL,             -- agent:<model> | human:<name> | propagation
    rationale    text,
    -- propagated links are claims needing review, not established coverage. D7.
    needs_review boolean NOT NULL DEFAULT FALSE,
    created_at   timestamptz NOT NULL DEFAULT now(),

    UNIQUE (evidence_id, control_node_id),

    -- a direct link has no path; a propagated one must have both
    CONSTRAINT link_direct_has_no_path CHECK (
        link_kind <> 'direct'
        OR (origin_link_id IS NULL AND edge_path IS NULL)
    ),
    CONSTRAINT link_propagated_has_path CHECK (
        link_kind <> 'propagated'
        OR (origin_link_id IS NOT NULL AND edge_path IS NOT NULL
            AND derived_strength IS NOT NULL)
    )
);

CREATE INDEX evidence_link_control_idx ON evidence_control_link(control_node_id);
CREATE INDEX evidence_link_evidence_idx ON evidence_control_link(evidence_id);
CREATE INDEX evidence_link_origin_idx ON evidence_control_link(origin_link_id);

-- ---------------------------------------------------------------------------
-- AI governance audit log
--
-- Every mapping proposed by the agent is recorded with its prompt version,
-- model, confidence and rationale, so any automated decision is attributable
-- and reversible. This is NIST AI RMF GOVERN 1.2 applied to this tool itself.
-- ---------------------------------------------------------------------------

CREATE TABLE ai_decision (
    id              bigserial PRIMARY KEY,
    decision_type   text NOT NULL,          -- evidence_mapping | concept_tagging
    subject_ref     text NOT NULL,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    input_digest    char(64) NOT NULL,
    output          jsonb NOT NULL,
    confidence      numeric(3,2),
    latency_ms      integer,
    created_at      timestamptz NOT NULL DEFAULT now(),
    human_override  jsonb                   -- set when a human disagreed
);

CREATE INDEX ai_decision_type_idx ON ai_decision(decision_type, created_at DESC);
