# Decisions

Every non-obvious decision taken in this project, with the reasoning. Reverse any of these
knowingly, not by accident.

Format: decision, why, and what it costs — because every one of these has a cost.

---

## D1 — The graph is plain Postgres, not a graph database

**Decision.** The knowledge graph is node and edge tables in vanilla Postgres, traversed with
recursive CTEs. No Neo4j, no Apache AGE, no extensions at all.

**Why.** It was the explicit ask. It is also correct at this scale: four frameworks produce
roughly 250 nodes and low thousands of edges, with traversals bounded at depth 3. Published
benchmarks put the comfortable ceiling for recursive-CTE traversal at around 100k nodes and
depth ≤ 4; we are two orders of magnitude inside that. Requiring no extensions means the
schema runs on any managed Postgres, Neon included.

**Cost.** Recursive CTEs evaluate all candidate edges rather than seeking a single node's
neighbourhood, so this degrades at a scale we will not reach. If the graph ever grew past
~100k nodes the answer would be a materialised n-th-order adjacency table, not a migration to
Neo4j.

---

## D2 — Four provenance tiers instead of one mapping table

**Decision.** Edges carry a `provenance_tier`: `T1_OFFICIAL`, `T2_REGULATORY`,
`T3_STRUCTURAL`, `T4_ONTOLOGY`, `T5_CURATED`.

**Why.** These frameworks relate to each other through genuinely different mechanisms with
genuinely different authority. An edge imported from NIST's published crosswalk and an edge
inferred from concept similarity are not the same kind of claim, and flattening them into one
table destroys the information a reviewer most needs. Full reasoning in
[CROSSWALK_METHODOLOGY.md](CROSSWALK_METHODOLOGY.md).

**Cost.** More schema, and every loader must declare its tier. Worth it.

---

## D3 — ISO 42001 → EU AI Act is `supports`, never `equivalent_to`

**Decision.** No ISO edge may claim equivalence to an EU AI Act provision. The edge type
`grants_presumption_of_conformity` is reserved for standards whose reference is cited in the
Official Journal, which today means exactly one: EN 18286:2026, via Commission Implementing
Decision (EU) 2026/2274, 14 October 2026.

**Why.** EU AI Act Article 40 is the only mechanism that connects a standard to legal
conformity, and it requires OJ citation. ISO/IEC 42001 is not a harmonised standard and
confers no presumption. A tool that renders an ISO 42001 certificate as EU AI Act coverage is
telling its user something legally false.

**Cost.** The crosswalk looks less impressive — fewer strong cross-framework claims. That is
the correct outcome.

---

## D4 — An ontology layer rather than pairwise mappings

**Decision.** Controls are tagged against a shared concept vocabulary; most cross-framework
edges are derived from concept overlap rather than authored pair by pair.

**Why.** There is no official article-level mapping between NIST AI RMF and the EU AI Act, so
those edges must be derived regardless. Deriving them through concepts means onboarding a
fifth framework requires tagging it once against the ontology, not authoring mappings to four
existing frameworks. It also makes every derived edge explainable — each stores the concepts
and weights that produced it.

**Cost.** Concept weights are hand-assigned, so the ontology encodes our judgement. Mitigated
by storing rationale on every edge, so a reviewer can disagree with one edge rather than the
whole method.

---

## D5 — ISO text is paraphrased, never reproduced

**Decision.** For ISO 27001 and ISO 42001 the repository stores the control identifier, the
commonly-cited short title, and our own paraphrase. Full normative text is stored only for the
EU AI Act and NIST AI RMF.

**Why.** ISO standards are copyrighted and sold. Reproducing normative text in a public
repository infringes that copyright. EU AI Act text is OJ-published and freely reusable; NIST
AI RMF is US Government public domain.

**Cost.** Real, and recorded in the data: ontology tagging for ISO controls runs against
paraphrases rather than normative text, which caps achievable confidence on ISO-derived edges.
The cap is stored on the edge rather than hidden.

---

## D6 — `diverges_from`, and propagation that refuses to cross it

**Decision.** The schema can express that two controls address the same subject and cannot
substitute for one another. Propagation traversal never crosses such an edge.

**Why.** A crosswalk that only expresses similarity overstates alignment. The clearest case:
the EU AI Act assigns risk by use-case list (Annex III), NIST AI RMF derives it contextually
per deployment. Same subject, not interchangeable. Evidence satisfying one must not silently
appear as coverage of the other.

**Cost.** Divergences must be authored deliberately; the system cannot infer them.

**Enforcement is in the traversal, not the UI.** A presentational-only warning would be lost
the moment anything consumed the API directly.

---

## D7 — Propagated links are claims, not assertions

**Decision.** Evidence tagged to one control propagates across the graph as
`link_kind='propagated'` rows carrying `edge_path`, a decayed strength, and `needs_review`.
Direct and propagated links are visually and semantically distinct.

**Why.** "Tag once, lights up everywhere" is the requested behaviour and it is genuinely
useful, but auto-propagated coverage presented as established fact is how compliance tooling
produces confident, wrong answers. Storing the path means any propagated link can be
explained: *this* evidence reached *this* control along *these* edges.

**Cost.** More rows and a UI that must distinguish three states instead of two.

---

## D8 — Postgres on Neon; AWS is the audited estate, not the platform

**Decision.** The control graph lives in Neon Postgres. The AI applications and their evidence
live in AWS.

**Why.** The control graph is the governance platform; AWS is the estate under governance.
Keeping them separate is the architecturally honest arrangement, and it avoids RDS
provisioning time inside a two-day window. The applications log to CloudWatch and S3, so AWS
remains the genuine evidence source.

**Cost.** Two providers to configure instead of one. Documented in
[../PREREQUISITES.md](../PREREQUISITES.md).

---

## D9 — The collector is read-only, with separate credentials

**Decision.** Two IAM identities. The collector gets `SecurityAudit` and nothing more. A
separate deployer credential is used for Terraform.

**Why.** A collector that can modify the estate it audits is not producing evidence. The
separation is itself an auditable control and is cited as evidence in the graph — the system
demonstrates on itself the property it checks for.

**Cost.** Two credential sets to manage.

---

## D10 — Terraform for the AWS estate

**Decision.** The three applications and their supporting resources are defined in Terraform.

**Why.** Reproducibility — the reviewer can stand the estate up themselves. And the IaC is
itself evidence: declarative infrastructure under version control, with reviewed changes, is
exactly what a change-management control asks for.

**Cost.** Terraform must be installed, and state has to live somewhere. Local state is
acceptable for a demo and is explicitly called out as unacceptable for production.

---

## D11 — Bedrock primary, Anthropic API fallback

**Decision.** The applications call Amazon Bedrock, falling back to the Anthropic API when
Bedrock model access is unavailable.

**Why.** Bedrock keeps inference inside AWS, which is what makes Bedrock Guardrails and model
invocation logging available as evidence — among the strongest artifacts in the whole system,
mapping cleanly to EU AI Act Art. 15 and Art. 12. But Bedrock model access requires approval
that can take hours, and a demo that cannot run is worth nothing.

**Cost.** Two code paths. The fallback is loudly logged, never silent, because which path ran
determines which evidence exists.

---

## D12 — A CV screener as one of the three applications

**Decision.** The three applications are a support chatbot, a CV screener, and a document RAG
assistant.

**Why.** The chatbot was named in the brief. The CV screener is chosen deliberately:
employment screening is EU AI Act **Annex III high-risk**, which makes Articles 9–15 genuinely
apply rather than hypothetically. Without a high-risk system in the estate, most of the EU AI
Act half of the graph would have nothing real attached to it. The RAG assistant exercises data
governance and retrieval logging, which the other two do not.

**Cost.** Three applications is more surface than two. The payoff is that different controls
light up for each, which is the whole point of a per-system coverage view.

---

## D13 — Live probes count as evidence

**Decision.** The collector performs real HTTP probes against deployed endpoints — model card
retrieval, rate-limit header checks, and an actual prompt-injection attempt recording the
system's refusal.

**Why.** Configuration says what a system is *meant* to do. A probe establishes what it
actually does. For AI-specific controls — guardrails, refusal behaviour, oversight — the
behavioural check is the stronger artifact, and it cannot be produced by reading config.

**Cost.** Probes mutate nothing but they do generate real traffic and real inference cost, and
a red-team probe appears in the application's own logs. That is correct: the audit trail
should show it.

---

## D14 — Hash-addressed evidence with a validity window

**Decision.** Every artifact stores a `sha256` of its payload, `collected_at`, and
`valid_until`.

**Why.** The hash makes evidence tamper-evident and lets re-collection detect drift by
comparison rather than by diffing blobs. The validity window makes staleness a first-class
property — compliance evidence has a shelf life, and a control "covered" by an eight-month-old
artifact is not covered. This is what the observability panel reports against.

**Cost.** Storage grows with every run. Retention policy is a real question deferred, and
named as deferred in [OBSERVABILITY.md](OBSERVABILITY.md).

---

## D15 — Public repository

**Decision.** Public, no secrets, `.env` gitignored, PDFs cached with checksums.

**Why.** The reviewer opens a link and it works — no invite, no account coordination.

**Cost.** Requires discipline about credentials. `make doctor` checks for accidentally
committed secrets before any push.
