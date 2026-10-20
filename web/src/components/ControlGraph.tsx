import { useMemo } from 'react';
import {
  FRAMEWORK_LABEL,
  frameworkColour,
  type FrameworkKey,
  type Neighbourhood,
  type RelatedControl,
} from '../api';

/* Radial layout.
 *
 * The queried control sits at the centre and the controls it relates to are
 * placed around it, clustered into one angular sector per framework so that
 * "which standards does this touch" is readable before any label is. Radius
 * carries hop distance; a two-hop control sits further out because it is a
 * weaker claim.
 *
 * Only the queried control and its matches are ever drawn. There is no view
 * in this application that renders a whole framework as a table.
 */

const SECTOR_GAP = 0.16; // radians kept clear between framework clusters
const RING = [0, 232, 352]; // radius by depth

interface Placed extends RelatedControl {
  x: number;
  y: number;
  angle: number;
}

function layout(related: RelatedControl[], width: number, height: number) {
  const cx = width / 2;
  const cy = height / 2;

  const order: FrameworkKey[] = [
    'eu_ai_act',
    'iso42001',
    'iso27001',
    'nist_ai_rmf',
    'en18286',
  ];
  const groups = order
    .map((key) => ({ key, items: related.filter((r) => r.framework_key === key) }))
    .filter((g) => g.items.length > 0);

  const total = related.length || 1;
  const placed: Placed[] = [];

  // Sector size is proportional to how many controls a framework contributes,
  // but floored: without a minimum, a framework with one match next to one with
  // fifteen gets an arc too narrow to hold its chip, and the two collide.
  const FULL = Math.PI * 2;
  const minSpan = Math.min(FULL / groups.length, 0.52);
  const raw = groups.map((g) => Math.max((g.items.length / total) * FULL, minSpan));
  const scale = FULL / raw.reduce((sum, value) => sum + value, 0);

  // start at the top and sweep clockwise
  let cursor = -Math.PI / 2;

  for (const [groupIndex, group] of groups.entries()) {
    const span = raw[groupIndex] * scale;
    const usable = Math.max(span - SECTOR_GAP, 0.08);
    const start = cursor + SECTOR_GAP / 2;

    group.items.forEach((item, index) => {
      // spread within the sector; a lone item sits at its centre
      const t = group.items.length === 1 ? 0.5 : index / (group.items.length - 1);
      const angle = start + usable * t;
      // Stagger radius for crowded sectors. A framework contributing a dozen
      // controls would otherwise pack them onto one arc and the chips collide;
      // alternating two radii gives each label room without widening the dial.
      const crowded = group.items.length > 5;
      const stagger = crowded ? (index % 2) * 62 : 0;
      const radius = RING[Math.min(item.depth, RING.length - 1)] + stagger;
      placed.push({
        ...item,
        angle,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius * 0.78, // flatten: screens are wide
      });
    });

    cursor += span;
  }

  return { cx, cy, placed, groups };
}

function edgePath(cx: number, cy: number, node: Placed) {
  // bow each edge away from centre so parallel edges stay distinguishable
  const mx = (cx + node.x) / 2;
  const my = (cy + node.y) / 2;
  const bow = 0.14;
  const qx = mx + (node.y - cy) * bow;
  const qy = my - (node.x - cx) * bow;
  return `M ${cx} ${cy} Q ${qx} ${qy} ${node.x} ${node.y}`;
}

interface Props {
  data: Neighbourhood;
  selectedEdgeId: number | null;
  onSelect: (control: RelatedControl | null) => void;
  width?: number;
  height?: number;
}

export function ControlGraph({
  data,
  selectedEdgeId,
  onSelect,
  width = 980,
  height = 660,
}: Props) {
  const { cx, cy, placed, groups } = useMemo(
    () => layout(data.related, width, height),
    [data.related, width, height],
  );

  const rootColour = frameworkColour(data.root.framework_key);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`${data.root.native_ref} and ${data.related.length} related controls across ${
        Object.keys(data.counts.by_framework).length
      } frameworks`}
    >
      <defs>
        <radialGradient id="hub-glow">
          <stop offset="0%" stopColor={rootColour} stopOpacity="0.20" />
          <stop offset="100%" stopColor={rootColour} stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* framework labels, anchored just beyond each cluster rather than at the
          geometric sector midpoint, which drifts away from the nodes when a
          framework contributes only one or two controls */}
      {groups.map((group) => {
        const items = placed.filter((p) => p.framework_key === group.key);
        const mid = items.reduce((sum, i) => sum + i.angle, 0) / items.length;
        const outer = Math.max(...items.map((i) => RING[Math.min(i.depth, RING.length - 1)]));
        const r = outer + 78;
        const lx = Math.min(width - 56, Math.max(56, cx + Math.cos(mid) * r));
        const ly = Math.min(height - 26, Math.max(22, cy + Math.sin(mid) * r * 0.78));
        return (
          <text
            key={group.key}
            x={lx}
            y={ly}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={10}
            letterSpacing="0.09em"
            fill={frameworkColour(group.key)}
            opacity={0.55}
            style={{ textTransform: 'uppercase' }}
          >
            {FRAMEWORK_LABEL[group.key]}
          </text>
        );
      })}

      <circle cx={cx} cy={cy} r={150} fill="url(#hub-glow)" />

      {/* edges first so nodes paint over them */}
      {placed.map((node, index) => {
        const colour = node.is_divergence
          ? 'var(--divergence)'
          : frameworkColour(node.framework_key);
        const strength = node.edge_strength ?? 0.5;
        const selected = selectedEdgeId === node.edge_id;
        return (
          <g
            key={`e-${node.node_id}`}
            className="edge-group"
            onClick={() => onSelect(node)}
          >
            <path
              d={edgePath(cx, cy, node)}
              className={`edge-line tier-${node.provenance_tier ?? 'T4_ONTOLOGY'}`}
              stroke={colour}
              strokeWidth={selected ? 2.5 : 0.8 + strength * 1.7}
              strokeOpacity={selected ? 1 : 0.34 + strength * 0.3}
              style={{
                strokeDasharray:
                  node.provenance_tier === 'T3_STRUCTURAL'
                    ? '7 4'
                    : node.provenance_tier === 'T4_ONTOLOGY'
                      ? '1.5 4'
                      : undefined,
                animation:
                  node.provenance_tier === 'T1_OFFICIAL' ||
                  node.provenance_tier === 'T2_REGULATORY' ||
                  node.provenance_tier === 'T5_CURATED'
                    ? `edge-draw 420ms var(--ease) ${index * 26}ms backwards`
                    : undefined,
                strokeDashoffset:
                  node.provenance_tier === 'T1_OFFICIAL' ||
                  node.provenance_tier === 'T2_REGULATORY' ||
                  node.provenance_tier === 'T5_CURATED'
                    ? 0
                    : undefined,
              }}
            />
            {/* invisible fat path so thin edges are still clickable */}
            <path
              d={edgePath(cx, cy, node)}
              fill="none"
              stroke="transparent"
              strokeWidth={14}
            />
          </g>
        );
      })}

      {/* the queried control */}
      <g className="animate-hub">
        <circle
          cx={cx}
          cy={cy}
          r={54}
          fill="none"
          stroke={rootColour}
          strokeOpacity={0.4}
          className="pulse-ring"
        />
        <circle
          cx={cx}
          cy={cy}
          r={46}
          fill="var(--surface-raised)"
          stroke={rootColour}
          strokeWidth={1.5}
        />
        <text x={cx} y={cy - 4} textAnchor="middle" className="hub-ref">
          {data.root.native_ref}
        </text>
        <text x={cx} y={cy + 11} textAnchor="middle" fontSize={9} fill="var(--text-faint)">
          {FRAMEWORK_LABEL[data.root.framework_key]}
        </text>
      </g>
      <text
        x={cx}
        y={cy + 86}
        textAnchor="middle"
        className="hub-title"
        style={{ maxWidth: 200 }}
      >
        {data.root.title.length > 52
          ? `${data.root.title.slice(0, 52)}…`
          : data.root.title}
      </text>

      {/* related controls */}
      {placed.map((node, index) => {
        const colour = node.is_divergence
          ? 'var(--divergence)'
          : frameworkColour(node.framework_key);
        const label =
          node.title.length > 22 ? `${node.title.slice(0, 22)}…` : node.title;
        const w = Math.max(78, node.native_ref.length * 7.4 + 22);
        const hasEvidence = (node.evidence.direct ?? 0) + (node.evidence.propagated ?? 0) > 0;
        return (
          <g
            key={`n-${node.node_id}`}
            className="node-group animate-node"
            style={{ animationDelay: `${120 + index * 26}ms` }}
            onClick={() => onSelect(node)}
            tabIndex={0}
            role="button"
            aria-label={`${node.native_ref} ${node.title}`}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelect(node);
              }
            }}
          >
            <rect
              className="node-chip"
              x={node.x - w / 2}
              y={node.y - 13}
              width={w}
              height={26}
              rx={6}
              fill="var(--surface-raised)"
              stroke={colour}
              strokeWidth={node.is_divergence ? 1.4 : 1}
              strokeDasharray={node.is_divergence ? '3 2' : undefined}
            />
            <text
              x={node.x}
              y={node.y + 1}
              textAnchor="middle"
              dominantBaseline="middle"
              className="node-ref"
              fill={colour}
            >
              {node.native_ref}
            </text>
            <text
              x={node.x}
              y={node.y + 25}
              textAnchor="middle"
              className="node-title"
            >
              {label}
            </text>
            {hasEvidence && (
              <circle
                cx={node.x + w / 2 - 4}
                cy={node.y - 11}
                r={3.2}
                fill={node.evidence.direct ? 'var(--ok)' : 'var(--warn)'}
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}
