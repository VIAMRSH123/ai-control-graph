# Observability

> "You will also probably just go through how does observability, monitoring and
> all this work, because those impurities are traps of the entire system."

The trap is specific and worth naming: **a compliance tool fails silently in the
direction of looking compliant.** A collector that cannot reach AWS returns no
findings. No findings looks exactly like no problems. The dashboard stays green
and the green is meaningless.

Everything here exists to make that failure mode visible.

---

## 1. Collection is itself observed

Every collector run writes a `collector_run` row before it does anything, and
closes it on the way out — including when it crashes, because the run is a
context manager rather than a pair of statements.

```
collector | status  | artifacts | errors
----------+---------+-----------+-------
probe     | ok      |         3 |      0
github    | ok      |         2 |      0
github    | partial |         2 |      2
```

Three statuses, three different things:

| | meaning |
|---|---|
| `ok` | ran, found what it found |
| `partial` | ran, but something failed — the errors are in the row |
| `failed` | did not complete |

`partial` is the important one. One unreachable service should degrade a run,
not discard the evidence the other services produced — and not pass silently
either. The `partial` row above is real: it is the run where a psycopg binding
bug broke the evidence mapper. It stayed in the history rather than being
cleaned up, because a monitoring surface that only shows successful runs is
decorative.

A collector that cannot run at all reports **why**, and is skipped:

```
aws      skipped — no AWS credentials in environment
```

It does not fall back to fixtures. There is no fixture mode. A smaller evidence
set with a stated reason is an honest output; a complete-looking one built from
sample data is not.

---

## 2. Evidence expires

Every artifact carries `collected_at`, `valid_until` and a `sha256` of what was
observed.

Compliance evidence has a shelf life. A control "covered" by an eight-month-old
snapshot is not covered — the configuration may have changed the day after it
was taken. So `control_coverage` reports `stale_covered` as its own number
rather than folding it into coverage, and `/api/observability` reports evidence
age against an SLO (`EVIDENCE_MAX_AGE_DAYS`, default 30).

Validity varies by what is being observed, deliberately:

| Artifact | Valid for | Why |
|---|---|---|
| S3 encryption, IAM posture, CloudTrail | 30 days | configuration changes rarely and visibly |
| Prompt-injection probe | **7 days** | behaviour changes whenever the model, system prompt or guardrail changes — none of which is a config event the collector would otherwise see |

---

## 3. Drift detection comes from the digest

Artifacts are keyed on `(system, artifact_type, uri)` and addressed by the hash
of their payload. Re-collection compares digests:

- unchanged digest → unchanged posture
- moved digest → something was reconfigured between runs

That is drift detection without a diffing engine, and it is why evidence is
stored as a structured payload plus digest rather than as prose. A summary
string cannot be compared; a canonical JSON hash can.

---

## 4. Automated decisions are auditable

Every evidence-to-control mapping lands in `ai_decision` with the model, prompt
version, input digest, confidence, the candidate controls that were offered, and
whether the agent or the deterministic rules made the call.

```sql
SELECT decision_type, count(*), round(avg(confidence), 2), 
       count(*) FILTER (WHERE human_override IS NOT NULL) AS overridden
FROM ai_decision GROUP BY decision_type;
```

This is NIST AI RMF **GOVERN 1.2** and **MANAGE 4.1** applied to this tool
itself. A system that assigns compliance evidence by model inference and keeps
no record of its own inferences would fail the standard it is checking against.

`human_override` is the feedback channel: when a human disagrees with a mapping,
the disagreement is recorded against the original decision rather than
overwriting it, so prompt changes can be evaluated against real corrections.

---

## 5. The applications emit structured audit events

One JSON line per event, to stdout — which on Lambda is CloudWatch Logs, which
is where the collector reads retention from. The loop closes: the apps produce
the logs, the collector reads the log configuration, and the graph records
whether the configuration satisfies EU AI Act Art.19.

```json
{"event":"inference","request_id":"c34ee305-…","ai_system":"support-chat",
 "model":"anthropic.claude-3-5-sonnet-20241022-v2:0","route":"refused",
 "input_sha256":"9f2b…","pii_detected":["EMAIL"],"guardrail_triggered":true,
 "latency_ms":3,"human_review_required":false}
```

Note what is **not** there: the prompt. Only its digest. An app that logged raw
prompts would satisfy the logging control and breach the privacy one
simultaneously. Redacting before writing is what makes both true at once.

Other events: `human_escalation`, `rate_limited`, `retrieval`,
`protected_attributes_stripped`, `bedrock_unavailable`.

`bedrock_unavailable` is deliberately loud. A silent fallback from Bedrock to
the Anthropic API would make the evidence record wrong — Bedrock guardrail and
invocation-logging evidence only exists for calls that actually went through
Bedrock.

---

## 6. What the UI surfaces

- per-framework coverage split into **direct**, **propagated** and **gap**,
  never summed — a framework that looks covered only because evidence
  propagated into it is in a materially different position from one with its
  own artifacts
- propagated links marked `needs_review`, with the exact edge path that reached
  them, so "why is this covered?" has an answer rather than an assertion
- last collector run per source, with status and error count
- evidence age against the freshness SLO

---

## What is deliberately not here

Stated rather than discovered:

- **No metrics backend.** Counts are computed from Postgres on request. At this
  scale that is correct; at fleet scale the `collector_run` and `ai_decision`
  tables should feed a time-series store.
- **No alerting.** The data to alert on exists — run status, evidence age, drift
  — but nothing dispatches. In production, staleness crossing the SLO is the
  first alert worth having.
- **No evidence retention policy.** Artifacts accumulate on every run. Keeping
  history is what makes drift visible, so this is a real question deferred
  rather than solved: the right answer is probably keeping the digest series
  and collapsing old payloads.
- **Rate limiting is per-process.** Each Lambda execution environment holds its
  own window, so it bounds one environment rather than the fleet. The system
  card says `rate_limit_per_minute` without that caveat, which is the kind of
  small overclaim worth catching in review.
