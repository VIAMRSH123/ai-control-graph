"""Concept tagging and ontology-derived edge inference.

This is the Tier 4 machinery described in docs/CROSSWALK_METHODOLOGY.md section
4.2. It exists because most cross-framework pairs have no official mapping --
notably there is no article-level NIST AI RMF to EU AI Act crosswalk anywhere --
so those edges must be derived, and deriving them pairwise across four
frameworks is neither tractable nor reviewable.

Instead every control is tagged against a shared concept vocabulary, and edges
fall out of concept overlap. Onboarding a fifth framework then means tagging it
once, not authoring mappings against four existing ones.

Tagging is deterministic regex matching, not an LLM call. That means the graph
builds with no API key, the same input always produces the same tags, and a
reviewer can see exactly why a control carries a concept. An LLM pass can refine
the result afterwards; it is not needed to produce a graph.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

#: Weight a parent concept inherits from its strongest matched child. Keeping
#: this well below 1 means a shared parent alone never implies a strong link --
#: two controls both touching "management-system" is not a crosswalk.
PARENT_INHERITANCE = 0.45

#: Below this cosine similarity, two controls are not considered related.
DEFAULT_SIMILARITY_THRESHOLD = 0.55

#: At or above this, the relationship is called equivalence rather than support.
EQUIVALENCE_THRESHOLD = 0.90

#: Most similar targets to keep per source node per target framework. Without a
#: cap, dense concept overlap produces a hairball that is technically correct
#: and useless to read. See docs/DECISIONS.md D16.
TOP_K_PER_FRAMEWORK = 5

#: Two controls must share at least this many DIRECTLY matched concepts before
#: an edge is derived. Without it, two controls that each match exactly one
#: concept have collinear vectors and score a perfect 1.0 -- which is how
#: "Security of assets off-premises" ends up looking identical to "Determining
#: the scope of the management system". Inherited parent concepts do not count.
MIN_SHARED_DIRECT_CONCEPTS = 2

#: Confidence multiplier for edges touching a framework whose text we may not
#: reproduce, and which is therefore tagged from our paraphrase rather than from
#: normative wording. See docs/DECISIONS.md D5.
PARAPHRASE_CONFIDENCE_PENALTY = 0.85


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    definition: str
    parent: str | None
    source: str | None = None
    patterns: tuple[tuple[re.Pattern[str], float], ...] = ()


@dataclass
class TaggedNode:
    """A control node, its concept vector, and which concepts matched directly."""

    node_id: int
    framework_key: str
    native_ref: str
    title: str
    vector: dict[str, float] = field(default_factory=dict)
    #: concepts matched by their own patterns, excluding inherited parents
    direct: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class InferredEdge:
    src_node_id: int
    dst_node_id: int
    edge_type: str
    strength: float
    confidence: float
    rationale: str


def load_concepts(path: Path) -> list[Concept]:
    raw = json.loads(path.read_text())
    concepts: list[Concept] = []
    for item in raw["concepts"]:
        patterns = tuple(
            (re.compile(pattern, re.IGNORECASE), float(weight))
            for pattern, weight in item.get("patterns", [])
        )
        concepts.append(
            Concept(
                key=item["key"],
                label=item["label"],
                definition=item["definition"],
                parent=item.get("parent"),
                source=item.get("source"),
                patterns=patterns,
            )
        )
    return concepts


def tag(text: str, concepts: list[Concept]) -> tuple[dict[str, float], set[str]]:
    """Assign concept weights to one control's text.

    Returns the full weighted vector and the set of concepts that matched their
    own patterns. The distinction matters: inherited parent weights make the
    hierarchy contribute to similarity, but they are too coarse to count as
    evidence that two controls are about the same thing.
    """
    by_key = {c.key: c for c in concepts}
    vector: dict[str, float] = {}

    for concept in concepts:
        best = 0.0
        for pattern, weight in concept.patterns:
            if weight > best and pattern.search(text):
                best = weight
        if best:
            vector[concept.key] = best

    direct = set(vector)

    # roll weights up the hierarchy
    inherited: dict[str, float] = defaultdict(float)
    for key, weight in vector.items():
        parent = by_key[key].parent
        while parent:
            inherited[parent] = max(inherited[parent], weight * PARENT_INHERITANCE)
            parent = by_key[parent].parent
    for key, weight in inherited.items():
        vector[key] = max(vector.get(key, 0.0), weight)

    return vector, direct


def inverse_document_frequency(nodes: list[TaggedNode]) -> dict[str, float]:
    """How much signal each concept carries.

    A concept attached to half the corpus -- 'management-system', say -- is
    nearly worthless as evidence that two controls are related. One attached to
    a handful is strong evidence. Weighting by IDF is what stops the generic
    governance vocabulary from linking everything to everything.
    """
    total = len(nodes) or 1
    frequency: dict[str, int] = defaultdict(int)
    for node in nodes:
        for key in node.vector:
            frequency[key] += 1
    return {key: math.log(1 + total / count) for key, count in frequency.items()}


def cosine(
    a: dict[str, float],
    b: dict[str, float],
    idf: dict[str, float] | None = None,
) -> float:
    shared = a.keys() & b.keys()
    if not shared:
        return 0.0
    weight = (lambda k: idf.get(k, 1.0)) if idf else (lambda k: 1.0)
    dot = sum(a[k] * b[k] * weight(k) ** 2 for k in shared)
    norm_a = math.sqrt(sum((v * weight(k)) ** 2 for k, v in a.items()))
    norm_b = math.sqrt(sum((v * weight(k)) ** 2 for k, v in b.items()))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _explain(
    a: TaggedNode, b: TaggedNode, labels: dict[str, str], idf: dict[str, float]
) -> str:
    """Why these two controls were linked, in words a reviewer can argue with."""
    shared = sorted(
        a.direct & b.direct,
        key=lambda k: min(a.vector[k], b.vector[k]) * idf.get(k, 1.0),
        reverse=True,
    )[:3]
    parts = [f"{labels.get(k, k)} ({min(a.vector[k], b.vector[k]):.2f})" for k in shared]
    return "Derived from shared concepts: " + ", ".join(parts) + "."


def infer_edges(
    nodes: list[TaggedNode],
    concepts: list[Concept],
    paraphrase_frameworks: set[str],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    top_k: int = TOP_K_PER_FRAMEWORK,
) -> list[InferredEdge]:
    """Derive cross-framework edges from concept overlap.

    Only cross-framework pairs are considered: relating a framework's controls
    to each other is a different problem and is not what a crosswalk is for.
    Each node keeps its ``top_k`` strongest matches per target framework, and
    the pair is emitted once in a stable direction so the edge is not duplicated.

    Two guards keep the result honest. Similarity is computed over IDF-weighted
    vectors, so shared generic vocabulary counts for little. And a pair must
    share at least ``MIN_SHARED_DIRECT_CONCEPTS`` directly matched concepts,
    which rules out the degenerate case of two single-concept vectors scoring a
    perfect 1.0 purely because they are collinear.
    """
    labels = {c.key: c.label for c in concepts}
    by_framework: dict[str, list[TaggedNode]] = defaultdict(list)
    for node in nodes:
        if node.vector:
            by_framework[node.framework_key].append(node)

    idf = inverse_document_frequency([n for n in nodes if n.vector])
    kept: dict[tuple[int, int], InferredEdge] = {}

    for src_framework, sources in by_framework.items():
        for dst_framework, targets in by_framework.items():
            if src_framework == dst_framework:
                continue
            for source in sources:
                scored = [
                    (cosine(source.vector, target.vector, idf), target)
                    for target in targets
                    if len(source.direct & target.direct) >= MIN_SHARED_DIRECT_CONCEPTS
                ]
                scored = [(s, t) for s, t in scored if s >= threshold]
                scored.sort(key=lambda pair: pair[0], reverse=True)

                for score, target in scored[:top_k]:
                    # stable ordering so A->B and B->A collapse to one edge
                    low, high = sorted((source.node_id, target.node_id))
                    existing = kept.get((low, high))
                    if existing and existing.strength >= score:
                        continue

                    confidence = score
                    if {src_framework, dst_framework} & paraphrase_frameworks:
                        confidence *= PARAPHRASE_CONFIDENCE_PENALTY

                    first, second = (
                        (source, target)
                        if source.node_id == low
                        else (target, source)
                    )
                    kept[(low, high)] = InferredEdge(
                        src_node_id=first.node_id,
                        dst_node_id=second.node_id,
                        edge_type=(
                            "equivalent_to" if score >= EQUIVALENCE_THRESHOLD else "supports"
                        ),
                        strength=round(score, 2),
                        confidence=round(confidence, 2),
                        rationale=_explain(source, target, labels, idf),
                    )

    return list(kept.values())
