# Crosswalk Methodology — how relationships between the frameworks are formed

This document is the evidence base for every edge in the graph. If you want to know why
ISO/IEC 42001 `A.6.2.6` is connected to NIST AI RMF `GOVERN 1.7`, or why ISO 42001 is *not*
treated as equivalent to anything in the EU AI Act, the answer is here.

The central claim of this document: **there is no single mechanism by which these four
frameworks relate to each other.** There are four, they differ in legal weight and in how
much human judgement they require, and treating them as interchangeable is the most common
failure mode in compliance crosswalks. The graph models them as distinct provenance tiers so
that a reviewer can always see which kind of claim they are looking at.

---

## 1. The four frameworks

| Framework | Version | Structure | Legal status | Text licensing |
|---|---|---|---|---|
| ISO/IEC 27001 | 2022 | 93 Annex A controls in 4 themes: A.5.1–5.37 organizational, A.6.1–6.8 people, A.7.1–7.14 physical, A.8.1–8.34 technological | Voluntary, certifiable | Copyrighted |
| ISO/IEC 42001 | 2023 | Clauses 4–10, plus Annex A: 9 control objectives A.2–A.10, ~38 controls | Voluntary, certifiable | Copyrighted |
| EU AI Act | Reg. (EU) 2024/1689 | Articles. High-risk core is Art. 9–15; provider duties Art. 16–17; deployer duties Art. 26; post-market monitoring Art. 72 | **Binding law** | OJ text, freely reusable |
| NIST AI RMF | 1.0 (AI 100-1) | 4 functions / 19 categories / 72 subcategories — GOVERN, MAP, MEASURE, MANAGE | Voluntary guidance | US Gov public domain |

### 1.1 A licensing decision that constrains the data

ISO standards are sold, not published. Reproducing their normative text in a public
repository infringes ISO copyright, regardless of how useful it would be.

So this repository stores, for ISO 27001 and ISO 42001: **the control identifier, the short
control title as commonly cited, and our own paraphrase of intent.** Nothing more. Full
normative text for EU AI Act and NIST AI RMF *is* stored verbatim, because both are freely
reusable.

This is a real constraint with a real consequence: the ontology tagging for ISO controls is
performed against paraphrases rather than normative text, which caps achievable confidence on
ISO-sourced edges. That cap is recorded in the data rather than hidden — see §4.3.

---

## 2. Tier 1 — Official published crosswalks

**Judgement required: none. These are imported and cited.**

NIST maintains a set of crosswalk documents at
<https://airc.nist.gov/airmf-resources/crosswalks/>. Thirteen are published. Two matter here.

### 2.1 NIST AI RMF → ISO/IEC 42001

Source: `NIST_AI_RMF_to_ISO_IEC_42001_Crosswalk.pdf`, 16 pages, exported from Excel,
authored May 2023. Cached under [`docs/sources/`](sources/) with a checksum.

Structure, confirmed by reading the document directly:

| Column | Content |
|---|---|
| 1 | AI RMF subcategory ref — `Govern 1.1`, `Map 2.3`, … |
| 2 | AI RMF outcome text, verbatim |
| 3 | ISO/IEC 42001 clause or Annex A control ref |
| 4 | ISO control/clause title |

It is many-to-many: a single NIST subcategory fans out to between 2 and 9 ISO items. For
example `Govern 1.1` maps to clause `4.1`, clause `6.2`, and Annex A `B.2.2` and `B.2.4`.

**Two important properties of this source:**

1. **It carries no relationship type and no strength.** Every mapping is an undifferentiated
   "these are related". Whether a relationship is equivalence, partial support, or mere
   topical adjacency is not stated. That layer is ours to add, and §4 describes how.

2. **It is numbered against the FDIS draft, not the published standard.** Annex A controls
   appear as `B.x` — `B.6.2.6`, `B.10.3`, `B.4.5`. ISO/IEC 42001:2023 as published renumbers
   these to `A.x`. Importing the crosswalk without normalizing `B.x` → `A.x` produces a graph
   in which **every single Annex A edge points at a control reference that does not exist.**

   The loader performs this normalization explicitly and fails loudly on any reference that
   does not resolve to a published control. There is a test for this
   (`tests/test_normalization.py`) precisely because a silent failure here would be invisible
   and would corrupt the entire crosswalk.

   The document also contains at least one typographical defect in the source itself — `7..2`
   appears where `7.2` is meant. The loader repairs known defects from an explicit, reviewable
   list rather than by guessing.

### 2.2 NIST AI RMF → EU AI Act — and why it is not enough

Source: `crosswalk_AI_RMF_1_0_OECD_EO_AIA_BoR.pdf`, January 2023.

This maps the AI RMF to the OECD AI Principles, the proposed EU AI Act, Executive Order 13960
and the Blueprint for an AI Bill of Rights. Reading it directly establishes a limitation that
shapes this entire project:

**It operates at the level of the seven NIST trustworthiness characteristics, not at the level
of subcategories or articles.** The rows are: Valid and Reliable, Safe, Secure and Resilient,
Accountable and Transparent, Explainable and Interpretable, Privacy-Enhanced, and Fair with
Harmful Bias Managed. The EU AI Act column contains terms like "Robustness; Validation;
Accuracy", not article references.

It is also explicitly a *Draft for Comment*, and it maps the **proposed** Act, which changed
substantially before adoption as Regulation (EU) 2024/1689.

**Consequence: there is no official, article-level mapping between NIST AI RMF and the EU AI
Act in existence.** Anyone presenting one has derived it. This project derives it too — but
derives it transparently, through the ontology layer in §4, and labels it as derived rather
than passing it off as authoritative.

The seven trustworthiness characteristics are not discarded. They are loaded as top-level
nodes in the concept ontology, which gives that ontology an officially-sourced spine rather
than one invented here.

---

## 3. Tier 2 — The regulatory mechanism

**Judgement required: none. This is a matter of legal fact.**

This is the tier most crosswalks omit, and it is the one that most changes how the results
should be read.

### 3.1 Presumption of conformity, Article 40

The EU AI Act does not recognise compliance frameworks as such. It has one formal mechanism
for connecting a standard to a legal requirement: **Article 40, presumption of conformity.**

A high-risk AI system that conforms to a harmonised standard is *presumed* to conform to the
Chapter III Section 2 requirements that standard covers. Three conditions, all necessary:

1. The standard is finalised by a European Standardisation Organisation (CEN, CENELEC, ETSI —
   in practice **CEN-CENELEC JTC 21** for AI).
2. The Commission accepts it against its standardisation request.
3. **The reference is cited in the Official Journal.**

Condition 3 is the operative one. Publication by CEN-CENELEC alone confers nothing. The
presumption also extends *only* to the requirements the standard actually covers, and it is
rebuttable — a market surveillance authority may still show the standard was inadequate in a
given case.

### 3.2 EN 18286:2026 — the first, and currently the only one

**EN 18286:2026** became the first harmonised standard under the EU AI Act, cited in the
Official Journal by **Commission Implementing Decision (EU) 2026/2274**, published
**14 October 2026**. It covers the **Article 17 quality management system** obligation for
providers of high-risk AI systems.

This is recent enough to be worth stating precisely: at the time of writing it is six days
old, and sources published before mid-October 2026 — including several that are otherwise
accurate — still state that no AI Act harmonised standard has been cited. They were correct
when written.

This produces exactly one edge in the graph of type
`grants_presumption_of_conformity`, the only edge type that carries legal weight.

### 3.3 ISO/IEC 42001 is not a harmonised standard

This follows directly from §3.1 and it is the single most consequential modelling decision in
this repository.

ISO/IEC 42001 is an international standard, not a European harmonised one. Its reference is
not cited in the Official Journal. **Certifying to ISO/IEC 42001 confers no presumption of
conformity with the EU AI Act whatsoever.**

CEN-CENELEC's position is that international standards should be used but adapted to AI Act
requirements. The fact that JTC 21 developed a separate QMS standard, rather than adopting
ISO/IEC 42001's management-system clauses, is itself evidence that the alignment was judged
insufficient.

Therefore every ISO 42001 → EU AI Act edge in this graph is typed **`supports`** and never
`equivalent_to`. A tool that renders an ISO 42001 certificate as EU AI Act coverage is telling
its user something legally false, and that is a failure mode worth engineering against
explicitly rather than relying on a disclaimer.

---

## 4. Tier 3, 4 and 5 — structural, derived and curated

### 4.1 Tier 3 — structural (mechanical)

ISO management system standards share the **Annex SL Harmonized Structure**. ISO/IEC 27001
and ISO/IEC 42001 therefore have structurally parallel clauses 4–10:

| Clause | Title |
|---|---|
| 4 | Context of the organization |
| 5 | Leadership |
| 6 | Planning |
| 7 | Support |
| 8 | Operation |
| 9 | Performance evaluation |
| 10 | Improvement |

These edges are generated mechanically with no human judgement, typed `equivalent_to` at the
structural level, and marked as structural so nobody mistakes "clause 7 ≈ clause 7" for a
substantive claim about control content. It is a claim about *management system shape*, which
is genuinely useful — an organisation with a working ISO 27001 management system has the
scaffolding for ISO 42001 — and nothing more.

### 4.2 Tier 4 — ontology-derived (algorithmic, explainable)

This tier exists because of the gap identified in §2.2: most cross-framework pairs have no
official mapping, so they must be derived, and deriving them by hand across four frameworks is
both intractable and unreviewable.

Rather than asserting N² pairwise mappings, every control is tagged against a shared
**concept ontology** — a vocabulary of control intents such as `human-oversight`,
`record-keeping`, `data-governance`, `bias-testing`, `incident-response`, `transparency`,
`risk-assessment`, `third-party-management`.

The ontology is rooted in the seven NIST trustworthiness characteristics from §2.2, so its
top level is officially sourced rather than invented. See [ONTOLOGY.md](ONTOLOGY.md).

Each `(control, concept)` tagging carries a weight in `[0,1]`. Edge strength between two
controls is cosine similarity over their concept vectors, above a cutoff. Every derived edge
stores a human-readable rationale — *"both map to `record-keeping` (0.8) and `traceability`
(0.6)"* — so a reviewer can disagree with a specific edge rather than with the method.

Three properties that matter:

- **Transitive by construction.** A new framework is onboarded by tagging its controls against
  the existing ontology. It does not require authoring mappings to every framework already
  present. This is the actual argument for an ontology over a mapping table.
- **Revisable.** Changing a concept weight updates every affected edge, with the change
  traceable to the tagging rather than buried in hand-edited pairs.
- **Honest about its ceiling.** ISO controls are tagged against paraphrases, not normative
  text (§1.1). Confidence on ISO-sourced derived edges is capped accordingly, and the cap is
  stored on the edge.

### 4.3 Tier 5 — curated (human)

Hand-authored edges for relationships the preceding tiers miss, each requiring a written
rationale and an identified author. Example: EU AI Act Art. 12 (record-keeping / automatic
logging) ↔ ISO 27001 A.8.15 (logging). Obvious to a practitioner; not reliably produced by
concept similarity, because one is a legal obligation on an AI system and the other a
technical security control.

Curated edges are the escape hatch for expert knowledge. They are also the tier most likely to
embed an individual's error, so they carry `created_by` and `reviewed_by` and are the first
thing an auditor should sample.

---

## 5. Divergence — modelling where the frameworks do not reconcile

A crosswalk that only expresses similarity will overstate alignment. Published analysis of
exactly this problem — *Bridging AI Risk Frameworks: Reconciling ISO/IEC 42001, the NIST AI
Risk Management Framework, and the EU AI Act into a Unified Governance Taxonomy*
([arXiv:2608.07515](https://arxiv.org/abs/2608.07515)) — argues that control-level crosswalks
are "both incomplete and, in places, misleading", for four reasons:

1. Legal conformity (EU AI Act) and voluntary certification (ISO/IEC 42001) are **not
   interchangeable** — the §3.3 point, independently reached.
2. List-based risk categories (the Act's Annex III tiers) do not match contextual risk logic
   (the RMF's contextual approach).
3. Enforcement is asymmetric — one carries administrative fines, the others carry none.
4. All three leave a widening gap around general-purpose and agentic AI.

The paper's own taxonomy uses five layers: normative purpose, governance subject, risk logic,
control architecture, and evidence & assurance.

This graph takes two things from that critique:

**An edge type `diverges_from`,** used where two controls address the same subject but cannot
substitute for one another. The clearest case is risk classification: the EU AI Act assigns
risk by use-case list, NIST AI RMF derives it contextually per deployment. They are about the
same thing and are not interchangeable.

**Propagation never crosses a `diverges_from` edge.** This is enforced in the traversal, not
left to UI presentation. Evidence satisfying a NIST contextual risk assessment must not
silently appear as EU AI Act Annex III classification coverage.

---

## 6. How an edge is recorded

Every edge carries its provenance, so any claim can be traced to its basis:

| Field | Purpose |
|---|---|
| `edge_type` | what the relationship means — see below |
| `provenance_tier` | `T1_OFFICIAL` / `T2_REGULATORY` / `T3_STRUCTURAL` / `T4_ONTOLOGY` / `T5_CURATED` |
| `strength` | `[0,1]`, drives propagation decay |
| `confidence` | `[0,1]`, how much we trust the mapping itself — distinct from strength |
| `rationale` | human-readable justification |
| `source_citation` | document, page, or author |
| `created_by` / `reviewed_by` | attribution |

`strength` and `confidence` are deliberately separate. A relationship can be strong but
uncertain (ISO paraphrase-based tagging), or weak but certain (an explicitly narrow official
mapping).

**Edge types:** `grants_presumption_of_conformity` (T2 only, legally operative),
`equivalent_to`, `broader_than`, `narrower_than`, `supports`, `partially_satisfies`,
`informs`, `diverges_from`.

---

## 7. Known limitations

Stated here rather than discovered by a reviewer:

1. **ISO content is paraphrased, not normative** (§1.1). Caps confidence on ISO-derived edges.
2. **The NIST→ISO crosswalk is FDIS-era** (§2.1). Normalized, tested, but it reflects the
   draft's mapping judgement, not a re-derivation against the published standard.
3. **No official NIST→EU article-level mapping exists** (§2.2). Those edges are derived and
   labelled `T4_ONTOLOGY`. They are the least authoritative edges in the graph.
4. **Exactly one `T2_REGULATORY` edge exists today** (§3.2), because exactly one harmonised
   standard has been cited. This tier will grow as JTC 21 deliverables are cited; the loader
   is structured to accept new citations as data, not code changes.
5. **Concept weights are hand-assigned.** Defensible and reviewable, but not learned from a
   corpus. An obvious improvement path is embedding-based tagging with human review.
6. **Framework versions drift.** Every node carries its framework version. Nothing here
   auto-detects an amended standard; re-seeding is a deliberate act.

---

## Sources

- NIST AI RMF crosswalk index — <https://airc.nist.gov/airmf-resources/crosswalks/>
- NIST AI RMF → ISO/IEC 42001 crosswalk — cached in [`docs/sources/`](sources/)
- NIST AI RMF → OECD / EU AI Act / EO 13960 / Blueprint crosswalk — cached in [`docs/sources/`](sources/)
- NIST AI 100-1, AI Risk Management Framework 1.0
- Regulation (EU) 2024/1689 (EU AI Act), Art. 40 and Chapter III Section 2
- Commission Implementing Decision (EU) 2026/2274, OJ 14 October 2026 — EN 18286:2026
- CEN-CENELEC JTC 21 — <https://www.cencenelec.eu/areas-of-work/cen-cenelec-topics/artificial-intelligence/>
- CEN-CENELEC announcement of EN 18286:2026 in the OJEU — <https://www.cencenelec.eu/news-events/news/2026/en-in-the-spotlight/2026-10-15-en-18286-2026-published-in-ojeu/>
- *Bridging AI Risk Frameworks* — <https://arxiv.org/abs/2608.07515>
- ISO/IEC 27001:2022 and ISO/IEC 42001:2023 — purchase from ISO; not reproduced here
