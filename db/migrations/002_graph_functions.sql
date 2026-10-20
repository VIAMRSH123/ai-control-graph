-- ============================================================================
-- Graph traversal and evidence propagation
--
-- Two functions do the real work:
--   propagate_evidence()    tag once, light up everywhere
--   control_neighbourhood() what the UI renders for a queried control
--
-- Both traverse control_edge_bidirectional with recursive CTEs. Neither will
-- cross a 'diverges_from' edge — that rule is enforced here, in the traversal,
-- not in presentation, because anything consuming the API directly would
-- otherwise lose it. See docs/DECISIONS.md D6.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- propagate_evidence
--
-- Given evidence already tagged to one or more controls, walk the graph and
-- record every other control that evidence also speaks to.
--
-- Strength decays multiplicatively along the path, so a two-hop link through
-- edges of 0.8 and 0.7 lands at 0.56. Traversal stops when cumulative strength
-- falls below the threshold, so weak chains die out rather than reaching the
-- whole graph.
--
-- Returns the number of propagated links written.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION propagate_evidence(
    p_evidence_id bigint,
    p_threshold   numeric DEFAULT 0.45,
    p_max_depth   integer DEFAULT 3
) RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted integer;
BEGIN
    WITH RECURSIVE
    -- seeds: every control a human or agent tagged this evidence to directly
    direct AS (
        SELECT l.id AS origin_link_id, l.control_node_id AS node_id
        FROM evidence_control_link l
        WHERE l.evidence_id = p_evidence_id
          AND l.link_kind = 'direct'
    ),
    walk AS (
        SELECT d.origin_link_id,
               d.node_id,
               1.0::numeric       AS strength,
               ARRAY[]::bigint[]  AS edge_path,
               ARRAY[d.node_id]   AS visited,
               0                  AS depth
        FROM direct d

        UNION ALL

        SELECT w.origin_link_id,
               e.dst_node_id,
               (w.strength * e.strength)::numeric,
               w.edge_path || e.id,
               w.visited || e.dst_node_id,
               w.depth + 1
        FROM walk w
        JOIN control_edge_bidirectional e ON e.src_node_id = w.node_id
        WHERE w.depth < p_max_depth
          -- D6: evidence must never cross a deliberate divergence. The EU AI
          -- Act assigns risk by use-case list; NIST derives it contextually.
          -- Same subject, not interchangeable.
          AND e.edge_type <> 'diverges_from'
          AND e.strength >= p_threshold
          AND (w.strength * e.strength) >= p_threshold
          -- cycle guard: crosswalks are densely interlinked
          AND NOT (e.dst_node_id = ANY(w.visited))
    ),
    -- a control can be reachable by several paths; keep the strongest, and
    -- break ties toward the shorter path since it is easier to justify
    best AS (
        SELECT origin_link_id, node_id, strength, edge_path, depth,
               ROW_NUMBER() OVER (
                   PARTITION BY node_id
                   ORDER BY strength DESC, depth ASC
               ) AS rn
        FROM walk
        WHERE depth > 0
    ),
    ins AS (
        INSERT INTO evidence_control_link (
            evidence_id, control_node_id, link_kind, origin_link_id,
            edge_path, derived_strength, asserted_by, rationale, needs_review
        )
        SELECT p_evidence_id,
               b.node_id,
               'propagated',
               b.origin_link_id,
               b.edge_path,
               ROUND(b.strength, 3),
               'propagation',
               format('Reached in %s hop(s) from a direct tag; strength %s after decay.',
                      b.depth, ROUND(b.strength, 3)),
               -- D7: propagated coverage is a claim awaiting review, never an
               -- established fact. Weaker links are flagged harder.
               TRUE
        FROM best b
        WHERE b.rn = 1
          AND NOT EXISTS (
              SELECT 1 FROM evidence_control_link x
              WHERE x.evidence_id = p_evidence_id
                AND x.control_node_id = b.node_id
          )
        RETURNING 1
    )
    SELECT count(*)::integer INTO v_inserted FROM ins;

    RETURN v_inserted;
END;
$$;

COMMENT ON FUNCTION propagate_evidence IS
    'Walks the crosswalk from directly-tagged controls, recording every control '
    'the evidence also speaks to. Never crosses diverges_from. Writes edge_path '
    'so any propagated link can be explained rather than asserted.';


-- ---------------------------------------------------------------------------
-- control_neighbourhood
--
-- What the UI renders when someone asks about a control: that control and the
-- controls it relates to — never the full tables.
--
-- Unlike propagation this DOES return divergences, flagged, because showing a
-- reviewer where frameworks deliberately fail to reconcile is the point. It is
-- propagation that must not cross them, not display.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION control_neighbourhood(
    p_node_id   bigint,
    p_max_depth integer DEFAULT 1,
    p_threshold numeric DEFAULT 0.0
) RETURNS TABLE (
    node_id         bigint,
    framework_key   text,
    native_ref      text,
    title           text,
    statement       text,
    depth           integer,
    path_strength   numeric,
    edge_id         bigint,
    edge_type       edge_type,
    provenance_tier provenance_tier,
    edge_strength   numeric,
    edge_confidence numeric,
    rationale       text,
    is_divergence   boolean
)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE walk AS (
        SELECT p_node_id        AS node_id,
               0                AS depth,
               1.0::numeric     AS path_strength,
               NULL::bigint     AS edge_id,
               NULL::edge_type  AS edge_type,
               NULL::provenance_tier AS provenance_tier,
               NULL::numeric    AS edge_strength,
               NULL::numeric    AS edge_confidence,
               NULL::text       AS rationale,
               ARRAY[p_node_id] AS visited

        UNION ALL

        SELECT e.dst_node_id,
               w.depth + 1,
               (w.path_strength * e.strength)::numeric,
               e.id,
               e.edge_type,
               e.provenance_tier,
               e.strength,
               e.confidence,
               e.rationale,
               w.visited || e.dst_node_id
        FROM walk w
        JOIN control_edge_bidirectional e ON e.src_node_id = w.node_id
        WHERE w.depth < p_max_depth
          AND e.strength >= p_threshold
          -- stop expanding THROUGH a divergence, but still return it as a leaf
          AND (w.edge_type IS NULL OR w.edge_type <> 'diverges_from')
          AND NOT (e.dst_node_id = ANY(w.visited))
    ),
    -- one row per reached control: the strongest path to it
    deduped AS (
        SELECT DISTINCT ON (node_id) *
        FROM walk
        ORDER BY node_id, path_strength DESC, depth ASC
    )
    SELECT d.node_id,
           f.key,
           n.native_ref,
           n.title,
           n.statement,
           d.depth,
           ROUND(d.path_strength, 3),
           d.edge_id,
           d.edge_type,
           d.provenance_tier,
           d.edge_strength,
           d.edge_confidence,
           d.rationale,
           COALESCE(d.edge_type = 'diverges_from', FALSE)
    FROM deduped d
    JOIN control_node n ON n.id = d.node_id
    JOIN framework f ON f.id = n.framework_id
    ORDER BY d.depth, f.key, n.native_ref;
$$;

COMMENT ON FUNCTION control_neighbourhood IS
    'Returns a queried control plus the controls it relates to, with full edge '
    'provenance. Divergences are returned as flagged leaves — shown, not traversed.';


-- ---------------------------------------------------------------------------
-- Coverage view
--
-- Per AI system and framework: how much is directly evidenced, how much is
-- only inherited through the crosswalk, and how much is a genuine gap.
--
-- Direct and propagated are deliberately never summed into one number. A
-- framework that looks covered only because evidence propagated into it is in
-- a materially different position from one with its own artifacts.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW control_coverage AS
SELECT s.id                AS ai_system_id,
       s.key               AS ai_system_key,
       f.key               AS framework_key,
       f.name              AS framework_name,
       count(DISTINCT n.id) AS total_controls,
       count(DISTINCT n.id) FILTER (WHERE l.link_kind = 'direct')     AS direct_covered,
       count(DISTINCT n.id) FILTER (WHERE l.link_kind = 'propagated') AS propagated_covered,
       count(DISTINCT n.id) FILTER (WHERE l.id IS NULL)               AS uncovered,
       -- staleness is a coverage property, not a footnote: a control evidenced
       -- only by an expired artifact is not covered. See D14.
       count(DISTINCT n.id) FILTER (
           WHERE l.id IS NOT NULL AND e.valid_until IS NOT NULL AND e.valid_until < now()
       ) AS stale_covered
FROM ai_system s
CROSS JOIN framework f
JOIN control_node n ON n.framework_id = f.id
LEFT JOIN evidence_control_link l ON l.control_node_id = n.id
LEFT JOIN evidence e ON e.id = l.evidence_id AND e.ai_system_id = s.id
GROUP BY s.id, s.key, f.key, f.name;
