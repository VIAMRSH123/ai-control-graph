# Ontology

The concept vocabulary that cross-framework relationships are derived from.

This is the layer that makes the crosswalk something other than a wall of
hand-authored pairs, and it exists for a concrete reason rather than an
architectural preference: **there is no official article-level mapping between
NIST AI RMF and the EU AI Act.** Those edges have to be derived from something.
Deriving them from a shared vocabulary makes the derivation reviewable, and
makes onboarding a fifth framework a matter of tagging it once rather than
authoring mappings against four frameworks already present.

Definition lives in [`db/seed/ontology.json`](../db/seed/ontology.json);
mechanics in [`graph/ontology.py`](../graph/ontology.py).

---

## Shape

Two levels. The top level is **NIST AI RMF's seven trustworthiness
characteristics**, taken from NIST's own published crosswalk, so the spine of
the ontology is officially sourced rather than invented here:

| Concept | |
|---|---|
| `valid-reliable` | performs as intended, demonstrably |
| `safe` | does not lead to a state endangering life, health, property or environment |
| `secure-resilient` | withstands adverse events and unauthorised interference |
| `accountable-transparent` | information is available and responsibility is traceable |
| `explainable-interpretable` | mechanism and meaning of output can be described |
| `privacy-enhanced` | personal data safeguarded |
| `fair-bias-managed` | harmful bias identified, measured and managed |

Plus one addition of ours, marked as such in the data:

| `management-system` | the organisational machinery — policy, roles, competence, audit, review, corrective action |

That addition is necessary because the trustworthiness characteristics describe
properties of an *AI system*, while the bulk of both ISO standards describes the
*organisation that governs it*. Without it, every management-system clause in
ISO 27001 and ISO 42001 would be unclassifiable.

Beneath those sit **42 operational concepts** — `human-oversight`,
`record-keeping`, `data-quality`, `bias-testing`, `adversarial-robustness`,
`third-party-management` and so on. These are what controls actually tag to.

---

## How a control gets tagged

Deterministic regex matching against the control's title and statement. Each
concept carries patterns with weights; a concept's weight on a control is the
highest weight among its patterns that match.

```json
{ "key": "human-oversight",
  "patterns": [["human oversight", 0.95], ["overseen by", 0.9],
               ["automation bias", 0.85], ["disregard, override", 0.9]] }
```

**Why rules and not an LLM.** The graph builds with no API key, the same input
always produces the same tags, and a reviewer can see exactly why a control
carries a concept. An LLM pass can refine the result afterwards and the
`control_concept.assigned_by` column records which did the work. It is not
required to produce a graph, and a crosswalk whose contents change between runs
would be impossible to audit.

Parents inherit **0.45 ×** their strongest matched child. Set well below 1
deliberately: two controls both touching `management-system` is not a crosswalk,
and without the discount the generic governance vocabulary links everything to
everything.

---

## How an edge is derived

Cosine similarity between two controls' concept vectors, with three guards. Each
exists because its absence produced a specific wrong answer during development.

**IDF weighting.** A concept attached to half the corpus carries almost no
signal; one attached to a handful carries a lot. Weighting by inverse document
frequency is what stops shared generic vocabulary from relating everything.

**A minimum of two shared *directly matched* concepts.** Inherited parents do
not count. Without this, two controls that each match exactly one concept have
collinear vectors and score a perfect 1.0 — which is how *Security of assets
off-premises* came out identical to *Determining the scope of the management
system*.

**Top 5 per source per target framework.** Dense overlap otherwise produces a
result that is technically correct and unreadable.

Threshold is 0.50 by default (`ONTOLOGY_THRESHOLD`). At that setting the derived
tier is 99 edges.

Every derived edge stores its reasoning:

> Derived from shared concepts: Logging (0.95), Record keeping (0.70).

so a reviewer can reject one edge rather than the whole method.

---

## Where its confidence is capped

ISO 27001 and ISO 42001 text is copyrighted and not reproduced here, so their
controls are tagged against **our paraphrases** rather than normative wording
(see [DECISIONS.md D5](DECISIONS.md)). Any derived edge touching an ISO
framework therefore has its confidence multiplied by 0.85.

The cap is stored on the edge rather than mentioned in a footnote, because the
limitation is a property of the data and should travel with it.

---

## A failure worth keeping

During development `data-quality` carried the pattern `representative`, intended
for *sufficiently representative* training data under EU AI Act Art.10(1).

It matched **"authorised representative"** — a legal role appearing throughout
the Act — and produced a confident 1.00 edge between *Acquisition of data* and
*Art.49 Registration*. Two controls with nothing to do with each other, linked
at maximum strength, with a rationale that read perfectly plausibly.

The pattern now carries a negative lookbehind. The incident is recorded here
because it is the characteristic failure mode of this entire approach: a
derivation that is wrong is not obviously wrong, it is confidently wrong with a
rationale attached. That is the argument for keeping derived edges in their own
provenance tier, visibly separate from imported and curated ones, rather than
flattening every relationship into one undifferentiated table.

---

## Extending it

To add a framework: add a native table, project it into `control_node`, and tag
its controls against this vocabulary. Cross-framework edges then appear without
authoring a single pair.

To add a concept: add it to `ontology.json` with a parent and patterns, then
re-run `make graph`. Edges are rebuilt from the seed every time, so the seed
stays the source of truth and the database stays derived.

To correct a specific edge rather than the method, author a `T5_CURATED` edge in
[`curated_edges.json`](../db/seed/curated_edges.json). Curated edges load before
derived ones and win on conflict.
