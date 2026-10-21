/* Static-snapshot backend for the published demo.
 *
 * The live site has no database. It reads a snapshot exported from Postgres by
 * scripts/export_snapshot.py, in which every neighbourhood was computed by the
 * `control_neighbourhood()` SQL function. The traversal rules -- including
 * stopping at a divergence rather than expanding through it -- are therefore
 * the database's, not a reimplementation of them in the browser.
 *
 * What IS implemented here is search, which is a filter over the node list and
 * carries no graph semantics.
 *
 * Set VITE_API_BASE at build time to point the app at a live FastAPI instance
 * instead; then none of this is used.
 */

import type {
  FrameworkKey,
  GraphHealth,
  Neighbourhood,
  ProvenanceTier,
  RelatedControl,
  SearchHit,
} from './api';

interface SnapshotNode {
  id: number;
  framework_key: FrameworkKey;
  native_ref: string;
  title: string;
  statement: string;
  concepts: { key: string; label: string; weight: number }[];
  evidence: Partial<Record<'direct' | 'propagated', number>>;
}

/** A neighbour as stored: node text is omitted and resolved via node_id. */
interface SnapshotNeighbour {
  node_id: number;
  depth: number;
  path_strength: number;
  edge_id: number | null;
  edge_type: string | null;
  provenance_tier: ProvenanceTier | null;
  edge_strength: number;
  edge_confidence: number;
  rationale: string | null;
  is_divergence: boolean;
}

interface Snapshot {
  generated_at: string;
  note: string;
  stats: { nodes: number; edges: number; frameworks: number };
  nodes: SnapshotNode[];
  neighbourhoods: Record<string, Record<string, SnapshotNeighbour[]>>;
}

let cached: Promise<Snapshot> | null = null;

function load(): Promise<Snapshot> {
  if (!cached) {
    cached = fetch(`${import.meta.env.BASE_URL}graph-snapshot.json`).then((response) => {
      if (!response.ok) throw new Error(`snapshot unavailable: ${response.status}`);
      return response.json() as Promise<Snapshot>;
    });
  }
  return cached;
}

const byId = (snapshot: Snapshot) => {
  const index = new Map<number, SnapshotNode>();
  for (const node of snapshot.nodes) index.set(node.id, node);
  return index;
};

export const staticApi = {
  async health(): Promise<GraphHealth> {
    const snapshot = await load();
    return { status: 'ok', nodes: snapshot.stats.nodes, edges: snapshot.stats.edges };
  },

  async search(query: string): Promise<SearchHit[]> {
    const snapshot = await load();
    let needle = query.trim().toLowerCase();
    // accept "iso 42001 A.6.2.8" by dropping a leading framework name
    for (const prefix of ['iso/iec ', 'iso ', 'eu ai act ', 'nist ai rmf ', 'nist ', 'en ']) {
      if (needle.startsWith(prefix)) {
        const rest = needle.slice(prefix.length).trim();
        const parts = rest.split(/\s+/);
        if (parts.length === 2 && /^\d+$/.test(parts[0])) needle = parts[1];
        break;
      }
    }
    if (needle.length < 1) return [];

    // same ranking the SQL uses: exact ref, ref prefix, title, concept, text
    const rank = (node: SnapshotNode): number | null => {
      const ref = node.native_ref.toLowerCase();
      if (ref === needle) return 0;
      if (ref.startsWith(needle)) return 1;
      if (node.title.toLowerCase().includes(needle)) return 2;
      if (node.concepts.some((c) => c.label.toLowerCase().includes(needle) || c.key.includes(needle)))
        return 3;
      if (node.statement.toLowerCase().includes(needle)) return 4;
      return null;
    };

    return snapshot.nodes
      .map((node) => ({ node, r: rank(node) }))
      .filter((entry): entry is { node: SnapshotNode; r: number } => entry.r !== null)
      .sort(
        (a, b) =>
          a.r - b.r ||
          a.node.framework_key.localeCompare(b.node.framework_key) ||
          a.node.native_ref.localeCompare(b.node.native_ref),
      )
      .slice(0, 10)
      .map(({ node }) => ({
        id: node.id,
        framework_key: node.framework_key,
        framework_name: node.framework_key,
        native_ref: node.native_ref,
        title: node.title,
        statement: node.statement,
      }));
  },

  async neighbourhood(id: number, depth: number): Promise<Neighbourhood> {
    const snapshot = await load();
    const index = byId(snapshot);
    const root = index.get(id);
    if (!root) throw new Error('no such control');

    const stored = snapshot.neighbourhoods[String(id)]?.[String(depth)] ?? [];

    const hydrate = (n: SnapshotNeighbour): RelatedControl => {
      const node = index.get(n.node_id)!;
      return {
        node_id: n.node_id,
        framework_key: node.framework_key,
        native_ref: node.native_ref,
        title: node.title,
        statement: node.statement,
        depth: n.depth,
        path_strength: n.path_strength,
        edge_id: n.edge_id,
        edge_type: n.edge_type,
        provenance_tier: n.provenance_tier,
        edge_strength: n.edge_strength,
        edge_confidence: n.edge_confidence,
        rationale: n.rationale,
        is_divergence: n.is_divergence,
        evidence: node.evidence,
      };
    };

    const related = stored.map(hydrate);
    const tally = <T extends string>(key: (r: RelatedControl) => T | null) => {
      const out: Partial<Record<T, number>> = {};
      for (const r of related) {
        const value = key(r);
        if (value) out[value] = (out[value] ?? 0) + 1;
      }
      return out;
    };

    return {
      root: {
        node_id: root.id,
        framework_key: root.framework_key,
        native_ref: root.native_ref,
        title: root.title,
        statement: root.statement,
        depth: 0,
        path_strength: 1,
        edge_id: null,
        edge_type: null,
        provenance_tier: null,
        edge_strength: null,
        edge_confidence: null,
        rationale: null,
        is_divergence: false,
        evidence: root.evidence,
      },
      related,
      counts: {
        total: related.length,
        by_framework: tally((r) => r.framework_key),
        by_tier: tally((r) => r.provenance_tier),
        divergences: related.filter((r) => r.is_divergence).length,
      },
    };
  },
};
