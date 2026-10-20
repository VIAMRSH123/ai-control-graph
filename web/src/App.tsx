import { useEffect, useState } from 'react';
import {
  api,
  FRAMEWORK_LABEL,
  frameworkColour,
  type FrameworkKey,
  type GraphHealth,
  type Neighbourhood,
  type RelatedControl,
  type SearchHit,
} from './api';
import { CommandBar } from './components/CommandBar';
import { ControlGraph } from './components/ControlGraph';
import { DetailRail } from './components/DetailRail';

const FRAMEWORKS: FrameworkKey[] = [
  'eu_ai_act',
  'iso42001',
  'iso27001',
  'nist_ai_rmf',
  'en18286',
];

export function App() {
  const [health, setHealth] = useState<GraphHealth | null>(null);
  const [data, setData] = useState<Neighbourhood | null>(null);
  const [selected, setSelected] = useState<RelatedControl | null>(null);
  const [depth, setDepth] = useState(1);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  const load = (nodeId: number, nextDepth = depth) => {
    setLoading(true);
    api
      .neighbourhood(nodeId, nextDepth, 0.4)
      .then((result) => {
        setData(result);
        setSelected(null);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  };

  const pick = (hit: SearchHit) => load(hit.id);

  const changeDepth = (next: number) => {
    setDepth(next);
    if (data) load(data.root.node_id, next);
  };

  return (
    <>
      <header className="masthead">
        <div className="wordmark">
          <strong>AI Control Graph</strong>
          <span>Crosswalk</span>
        </div>
        <CommandBar onPick={pick} />
        <div className="masthead-stats">
          {health && (
            <>
              <span><b>{health.nodes}</b> controls</span>
              <span><b>{health.edges}</b> relationships</span>
              <span><b>5</b> frameworks</span>
            </>
          )}
        </div>
      </header>

      <div className="workspace">
        <main className="stage">
          {data ? (
            <ControlGraph
              data={data}
              selectedEdgeId={selected?.edge_id ?? null}
              onSelect={setSelected}
            />
          ) : (
            <div className="stage-hint">
              <div style={{ fontSize: 14, color: 'var(--text-dim)' }}>
                Ask for a control.
              </div>
              <div>
                Try <kbd>A.6.2.8</kbd> <kbd>Art.14</kbd> <kbd>human oversight</kbd>
              </div>
              <div style={{ marginTop: 6, fontSize: 11 }}>
                Press <kbd>⌘K</kbd> to focus the search
              </div>
            </div>
          )}
          {loading && (
            <div className="stage-hint" style={{ fontSize: 11 }}>
              resolving…
            </div>
          )}
        </main>

        <DetailRail data={data} selected={selected} />
      </div>

      <footer className="legend">
        {FRAMEWORKS.map((key) => (
          <span className="legend-item" key={key}>
            <span
              className="legend-swatch"
              style={{ background: frameworkColour(key) }}
            />
            {FRAMEWORK_LABEL[key]}
          </span>
        ))}

        <span className="legend-sep" />

        <span className="legend-item" title="Imported from a published crosswalk">
          <svg width="26" height="6"><line x1="0" y1="3" x2="26" y2="3" stroke="var(--text-faint)" strokeWidth="1.6" /></svg>
          official / curated
        </span>
        <span className="legend-item" title="ISO Annex SL shared clause structure">
          <svg width="26" height="6"><line x1="0" y1="3" x2="26" y2="3" stroke="var(--text-faint)" strokeWidth="1.6" strokeDasharray="7 4" /></svg>
          structural
        </span>
        <span className="legend-item" title="Derived from shared concepts">
          <svg width="26" height="6"><line x1="0" y1="3" x2="26" y2="3" stroke="var(--text-faint)" strokeWidth="1.6" strokeDasharray="1.5 4" /></svg>
          derived
        </span>
        <span className="legend-item" style={{ color: 'var(--divergence)' }} title="Cannot substitute for one another">
          <svg width="12" height="12"><rect x="1" y="3" width="10" height="7" rx="2" fill="none" stroke="var(--divergence)" strokeDasharray="3 2" /></svg>
          divergence
        </span>

        <span className="legend-sep" />
        <span className="legend-item">line weight = strength</span>

        <span style={{ marginLeft: 'auto' }} className="legend-item">
          <span className="mode-toggle">
            {[1, 2].map((d) => (
              <button
                key={d}
                aria-pressed={depth === d}
                onClick={() => changeDepth(d)}
              >
                {d} hop{d > 1 ? 's' : ''}
              </button>
            ))}
          </span>
        </span>
      </footer>
    </>
  );
}
