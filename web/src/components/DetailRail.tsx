import {
  FRAMEWORK_LABEL,
  TIER_LABEL,
  frameworkColour,
  type Neighbourhood,
  type RelatedControl,
} from '../api';

/* Why two controls are connected, in enough detail to disagree with.
 *
 * Every edge carries its provenance tier, strength, confidence, a written
 * rationale and a citation. A reviewer who thinks an edge is wrong should be
 * able to say which edge and why, and that requires showing all of it.
 */

interface Props {
  data: Neighbourhood | null;
  selected: RelatedControl | null;
}

export function DetailRail({ data, selected }: Props) {
  if (!data) {
    return (
      <aside className="rail">
        <p className="empty-rail">
          Search for a control to see what it maps to across the other
          frameworks.
        </p>
      </aside>
    );
  }

  const byTier = Object.entries(data.counts.by_tier) as [string, number][];

  return (
    <aside className="rail">
      <section className="panel">
        <div className="panel-label">Queried control</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 6 }}>
          <code
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 13,
              color: frameworkColour(data.root.framework_key),
            }}
          >
            {data.root.native_ref}
          </code>
          <span style={{ fontSize: 10, letterSpacing: '0.08em', color: 'var(--text-faint)', textTransform: 'uppercase' }}>
            {FRAMEWORK_LABEL[data.root.framework_key]}
          </span>
        </div>
        <div style={{ fontSize: 12.5, marginBottom: 8 }}>{data.root.title}</div>
        <p className="rationale">{data.root.statement}</p>
      </section>

      <section className="panel">
        <div className="panel-label">
          {data.counts.total} related control{data.counts.total === 1 ? '' : 's'}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          {Object.entries(data.counts.by_framework).map(([key, n]) => (
            <span
              key={key}
              className="pill"
              style={{ borderColor: frameworkColour(key as never), color: frameworkColour(key as never) }}
            >
              {FRAMEWORK_LABEL[key as never]} {n}
            </span>
          ))}
        </div>
        <dl className="kv">
          {byTier.map(([tier, n]) => (
            <div key={tier} style={{ display: 'contents' }}>
              <dt style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{tier.replace('T', 'T').split('_')[0]}</dt>
              <dd>
                {n} · {TIER_LABEL[tier as never]}
              </dd>
            </div>
          ))}
        </dl>
        {data.counts.divergences > 0 && (
          <div style={{ marginTop: 10 }}>
            <span className="pill divergence">
              {data.counts.divergences} divergence{data.counts.divergences === 1 ? '' : 's'} — evidence will not cross
            </span>
          </div>
        )}
      </section>

      {selected ? (
        <section className="panel" style={{ borderColor: selected.is_divergence ? 'var(--divergence)' : 'var(--border-strong)' }}>
          <div className="panel-label">Relationship</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 6 }}>
            <code
              style={{
                fontFamily: 'var(--mono)',
                fontSize: 13,
                color: selected.is_divergence ? 'var(--divergence)' : frameworkColour(selected.framework_key),
              }}
            >
              {selected.native_ref}
            </code>
            <span style={{ fontSize: 10, letterSpacing: '0.08em', color: 'var(--text-faint)', textTransform: 'uppercase' }}>
              {FRAMEWORK_LABEL[selected.framework_key]}
            </span>
          </div>
          <div style={{ fontSize: 12.5, marginBottom: 10 }}>{selected.title}</div>

          <dl className="kv" style={{ marginBottom: 10 }}>
            <dt>Type</dt>
            <dd>
              <span className={selected.is_divergence ? 'pill divergence' : selected.provenance_tier === 'T2_REGULATORY' ? 'pill regulatory' : 'pill'}>
                {selected.edge_type?.replaceAll('_', ' ')}
              </span>
            </dd>
            <dt>Provenance</dt>
            <dd>{selected.provenance_tier ? TIER_LABEL[selected.provenance_tier] : '—'}</dd>
            <dt>Strength</dt>
            <dd style={{ fontFamily: 'var(--mono)' }}>{selected.edge_strength ?? '—'}</dd>
            <dt>Confidence</dt>
            <dd style={{ fontFamily: 'var(--mono)' }}>{selected.edge_confidence ?? '—'}</dd>
            <dt>Distance</dt>
            <dd>{selected.depth} hop{selected.depth === 1 ? '' : 's'}</dd>
          </dl>

          {selected.rationale && <p className="rationale">{selected.rationale}</p>}

          {selected.is_divergence && (
            <p className="rationale" style={{ color: 'var(--divergence)', marginTop: 10 }}>
              These address the same subject and cannot substitute for one
              another. Evidence is blocked from propagating across this edge.
            </p>
          )}
        </section>
      ) : (
        <p className="empty-rail">Select an edge or a control to see why they are connected.</p>
      )}
    </aside>
  );
}
