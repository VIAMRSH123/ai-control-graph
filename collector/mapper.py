"""Decide which single control an artifact is direct evidence for.

The brief is "tag the evidence to one control, one central hub" and let the
graph do the rest. So this deliberately picks exactly ONE control per artifact.
Resisting the urge to tag an artifact to everything it plausibly touches is the
whole point: if the crosswalk is right, one tag is enough, and if it is wrong,
tagging everything hides that.

Two modes, and which one ran is recorded on every decision:

  agent   Claude is given the artifact payload and a shortlist of candidate
          controls, and returns a choice with a confidence and a rationale.
  rules   The collector's own ``suggests`` hint is used. Deterministic, needs
          no API key, and keeps the whole pipeline runnable offline.

Either way the decision lands in ``ai_decision`` with the model, prompt version,
input digest, confidence and output. An automated judgement that cannot be
audited or reversed has no business in a compliance tool.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .base import Artifact, digest

PROMPT_VERSION = "evidence-mapping/v1"

SYSTEM_PROMPT = """You map compliance evidence to controls.

You are given one piece of evidence collected from a live system, and a \
shortlist of candidate controls drawn from ISO/IEC 27001, ISO/IEC 42001, the \
EU AI Act and the NIST AI Risk Management Framework.

Choose the SINGLE control for which this evidence is the most direct proof. \
Not the broadest match and not the most impressive one: the control a competent \
auditor would file this exact artifact against. A crosswalk propagates the \
evidence to related controls afterwards, so you do not need to cover them.

Return strict JSON only:
{"framework": "<framework key>", "ref": "<native ref>", "confidence": <0..1>, \
"rationale": "<one or two sentences>"}

If no candidate is a genuine fit, return {"framework": null, "ref": null, \
"confidence": 0, "rationale": "<why>"}. Declining is better than a weak tag."""


@dataclass
class Mapping:
    framework: str | None
    ref: str | None
    confidence: float
    rationale: str
    mode: str
    model: str


def candidates(conn, artifact: Artifact, limit: int = 12) -> list[dict]:
    """Shortlist controls worth considering for this artifact.

    The collector's hints go first, then concept-matched controls, so the agent
    sees both the obvious answer and enough alternatives to reject it.
    """
    hinted = artifact.suggests
    rows: list[dict] = []
    with conn.cursor() as cur:
        if hinted:
            # unnest two parallel arrays rather than binding a list of tuples:
            # psycopg cannot send an anonymous composite type
            cur.execute(
                """
                SELECT f.key AS framework, n.native_ref AS ref, n.title, n.statement
                FROM unnest(%s::text[], %s::text[]) AS hint(framework, ref)
                JOIN framework f ON f.key = hint.framework
                JOIN control_node n
                  ON n.framework_id = f.id AND lower(n.native_ref) = lower(hint.ref)
                """,
                ([h[0] for h in hinted], [h[1] for h in hinted]),
            )
            rows = [dict(zip(("framework", "ref", "title", "statement"), r)) for r in cur.fetchall()]

        # widen with concept-similar controls so the shortlist is not just the hint
        terms = f"{artifact.artifact_type} {artifact.summary}".lower().replace("_", " ")
        cur.execute(
            """
            SELECT f.key, n.native_ref, n.title, n.statement
            FROM control_node n JOIN framework f ON f.id = n.framework_id
            WHERE EXISTS (
                SELECT 1 FROM control_concept cc JOIN concept c ON c.id = cc.concept_id
                WHERE cc.control_node_id = n.id
                  AND cc.weight >= 0.7
                  AND %s LIKE '%%' || replace(c.key, '-', ' ') || '%%'
            )
            LIMIT %s
            """,
            (terms, limit),
        )
        seen = {(r["framework"], r["ref"]) for r in rows}
        for framework, ref, title, statement in cur.fetchall():
            if (framework, ref) not in seen:
                rows.append(
                    {"framework": framework, "ref": ref, "title": title, "statement": statement}
                )
    return rows[:limit]


def _by_rules(artifact: Artifact) -> Mapping:
    if not artifact.suggests:
        return Mapping(None, None, 0.0, "No rule hint for this artifact type.", "rules", "none")
    framework, ref = artifact.suggests[0]
    return Mapping(
        framework,
        ref,
        0.70,
        f"Rule mapping for artifact type {artifact.artifact_type!r}. "
        "Deterministic fallback used because no model credential was available.",
        "rules",
        "none",
    )


def _by_agent(artifact: Artifact, options: list[dict]) -> Mapping:
    import anthropic

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    listing = "\n".join(
        f"- {o['framework']} {o['ref']}: {o['title']} — {o['statement'][:260]}"
        for o in options
    )
    user = (
        f"EVIDENCE\n"
        f"type: {artifact.artifact_type}\n"
        f"uri: {artifact.uri}\n"
        f"summary: {artifact.summary}\n"
        f"payload: {json.dumps(artifact.payload, default=str)[:1800]}\n\n"
        f"CANDIDATE CONTROLS\n{listing}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    parsed = json.loads(text)
    return Mapping(
        parsed.get("framework"),
        parsed.get("ref"),
        float(parsed.get("confidence") or 0),
        parsed.get("rationale") or "",
        "agent",
        model,
    )


def map_artifact(conn, artifact: Artifact) -> Mapping:
    """Choose one control, recording the decision either way."""
    options = candidates(conn, artifact)

    if os.environ.get("ANTHROPIC_API_KEY") and options:
        try:
            mapping = _by_agent(artifact, options)
        except Exception as exc:  # noqa: BLE001 - never let the agent stop collection
            mapping = _by_rules(artifact)
            mapping.rationale += f" (agent unavailable: {type(exc).__name__})"
    else:
        mapping = _by_rules(artifact)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_decision (decision_type, subject_ref, model, prompt_version,
                                     input_digest, output, confidence)
            VALUES ('evidence_mapping', %s, %s, %s, %s, %s, %s)
            """,
            (
                f"{artifact.artifact_type}@{artifact.uri}",
                mapping.model,
                PROMPT_VERSION if mapping.mode == "agent" else "rules/v1",
                digest(artifact.payload),
                json.dumps(
                    {
                        "framework": mapping.framework,
                        "ref": mapping.ref,
                        "rationale": mapping.rationale,
                        "mode": mapping.mode,
                        "candidates_offered": [f"{o['framework']} {o['ref']}" for o in options],
                    }
                ),
                round(mapping.confidence, 2),
            ),
        )
    return mapping
