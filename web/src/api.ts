export type FrameworkKey =
  | 'eu_ai_act'
  | 'iso27001'
  | 'iso42001'
  | 'nist_ai_rmf'
  | 'en18286';

export type ProvenanceTier =
  | 'T1_OFFICIAL'
  | 'T2_REGULATORY'
  | 'T3_STRUCTURAL'
  | 'T4_ONTOLOGY'
  | 'T5_CURATED';

export interface SearchHit {
  id: number;
  framework_key: FrameworkKey;
  framework_name: string;
  native_ref: string;
  title: string;
  statement: string;
}

export interface RelatedControl {
  node_id: number;
  framework_key: FrameworkKey;
  native_ref: string;
  title: string;
  statement: string;
  depth: number;
  path_strength: number | null;
  edge_id: number | null;
  edge_type: string | null;
  provenance_tier: ProvenanceTier | null;
  edge_strength: number | null;
  edge_confidence: number | null;
  rationale: string | null;
  is_divergence: boolean;
  evidence: Partial<Record<'direct' | 'propagated', number>>;
}

export interface Neighbourhood {
  root: RelatedControl;
  related: RelatedControl[];
  counts: {
    total: number;
    by_framework: Partial<Record<FrameworkKey, number>>;
    by_tier: Partial<Record<ProvenanceTier, number>>;
    divergences: number;
  };
}

export interface GraphHealth {
  status: string;
  nodes: number;
  edges: number;
}

export interface PropagationLink {
  id: number;
  link_kind: 'direct' | 'propagated';
  derived_strength: number | null;
  hops: number;
  needs_review: boolean;
  rationale: string | null;
  asserted_by: string;
  node_id: number;
  native_ref: string;
  title: string;
  framework_key: FrameworkKey;
}

export interface Propagation {
  evidence: {
    id: number;
    artifact_type: string;
    uri: string;
    sha256: string;
    summary: string | null;
    collector: string;
    ai_system_key: string;
  };
  links: PropagationLink[];
  counts: { direct: number; propagated: number; frameworks_reached: number };
}

export interface EvidenceItem {
  id: number;
  collector: string;
  artifact_type: string;
  uri: string;
  summary: string | null;
  collected_at: string;
  stale: boolean;
  direct_links: number;
  propagated_links: number;
}

export interface AiSystem {
  id: number;
  key: string;
  name: string;
  provider: string;
  environment: string;
  risk_tier: string | null;
  evidence_count: number;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response.json() as Promise<T>;
}

export const api = {
  health: () => get<GraphHealth>('/api/health'),
  search: (q: string) => get<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}&limit=10`),
  neighbourhood: (id: number, depth: number, threshold: number) =>
    get<Neighbourhood>(`/api/control/${id}/neighbourhood?depth=${depth}&threshold=${threshold}`),
  systems: () => get<AiSystem[]>('/api/systems'),
  evidence: (key: string) => get<EvidenceItem[]>(`/api/systems/${key}/evidence`),
  propagation: (id: number) => get<Propagation>(`/api/evidence/${id}/propagation`),
};

export const FRAMEWORK_LABEL: Record<FrameworkKey, string> = {
  eu_ai_act: 'EU AI Act',
  iso27001: 'ISO 27001',
  iso42001: 'ISO 42001',
  nist_ai_rmf: 'NIST AI RMF',
  en18286: 'EN 18286',
};

export const TIER_LABEL: Record<ProvenanceTier, string> = {
  T1_OFFICIAL: 'Official crosswalk',
  T2_REGULATORY: 'Regulatory — presumption of conformity',
  T3_STRUCTURAL: 'Structural — Annex SL',
  T4_ONTOLOGY: 'Derived from shared concepts',
  T5_CURATED: 'Curated by a human',
};

export const frameworkColour = (key: FrameworkKey) => `var(--fw-${key})`;
