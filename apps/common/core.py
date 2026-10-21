"""Shared runtime for the three governed AI applications.

These are real services, not demonstrations of services. Each one runs the
controls that the graph later looks for evidence of, which is the point: the
collector should find a posture because the posture exists, not because a
fixture says so.

What every app gets:

  model routing     Bedrock first, Anthropic API as fallback. Which path ran is
                    recorded on every inference, because it determines whether
                    Bedrock-side evidence (guardrails, invocation logging)
                    exists at all for that call.
  guardrail         an input/output check that refuses rather than complies
  PII redaction     before anything is logged
  audit log         one structured record per inference, hash-addressed input
  rate limiting     per-caller, with standard headers so a probe can see it
  system card       machine-readable at /.well-known/ai-system-card.json
  human escalation  a route that hands a conversation to a person

An app that logged raw prompts would fail the privacy control while passing the
logging control. Redacting before writing is what makes both true at once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", 20))

#: Patterns redacted before anything reaches a log. Deliberately conservative:
#: over-redacting a log costs nothing, under-redacting one is a breach.
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("PHONE", re.compile(r"\+?\d[\d\s().-]{8,}\d")),
    ("CARD", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
]

#: Instructions that try to override the system prompt. Matching here is a
#: refusal, not a filter: the request is declined and the attempt is logged.
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"(reveal|print|show|repeat)\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"(environment variables|env vars|aws_secret|api[_ ]?key)", re.I),
    re.compile(r"disregard\s+(your\s+)?(guidelines|rules|instructions)", re.I),
]

REFUSAL = (
    "I can't help with that. That request asks me to disregard my operating "
    "instructions or disclose configuration, which I'm not able to do. "
    "I can still help with questions about the service itself."
)


def redact(text: str) -> tuple[str, list[str]]:
    """Replace personal data with typed placeholders. Returns text and kinds found."""
    found: list[str] = []
    for label, pattern in PII_PATTERNS:
        if pattern.search(text):
            found.append(label)
            text = pattern.sub(f"[{label}_REDACTED]", text)
    return text, found


def guardrail(text: str) -> str | None:
    """Return a refusal if the input attacks the system, else None."""
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return REFUSAL
    return None


@dataclass
class RateLimiter:
    """Fixed-window limiter, surfaced through standard headers.

    In-process on purpose: each Lambda holds its own window, so this bounds a
    single execution environment rather than the fleet. Documented as a known
    limitation rather than presented as a global guarantee.
    """

    per_minute: int = RATE_LIMIT_PER_MINUTE
    _hits: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def check(self, caller: str) -> tuple[bool, dict[str, str]]:
        now = time.time()
        window = self._hits[caller]
        while window and now - window[0] > 60:
            window.popleft()
        remaining = max(0, self.per_minute - len(window))
        headers = {
            "X-RateLimit-Limit": str(self.per_minute),
            "X-RateLimit-Remaining": str(max(0, remaining - 1)),
            "X-RateLimit-Reset": str(int(now + 60)),
        }
        if len(window) >= self.per_minute:
            headers["Retry-After"] = "60"
            return False, headers
        window.append(now)
        return True, headers


@dataclass
class Inference:
    """One model call, as it will be recorded."""

    request_id: str
    system_key: str
    model: str
    route: str  # bedrock | anthropic | refused
    input_sha256: str
    pii_detected: list[str]
    guardrail_triggered: bool
    latency_ms: int
    output_chars: int
    human_review_required: bool

    def as_log(self) -> dict[str, Any]:
        return {
            "event": "inference",
            "request_id": self.request_id,
            "ai_system": self.system_key,
            "model": self.model,
            "route": self.route,
            "input_sha256": self.input_sha256,
            "pii_detected": self.pii_detected,
            "guardrail_triggered": self.guardrail_triggered,
            "latency_ms": self.latency_ms,
            "output_chars": self.output_chars,
            "human_review_required": self.human_review_required,
            "ts": time.time(),
        }


def audit(record: dict[str, Any]) -> None:
    """Emit one structured line.

    On Lambda, stdout is CloudWatch Logs, which is what the collector reads
    retention from. Keeping this a single JSON line per event is what makes the
    log queryable rather than merely present.
    """
    print(json.dumps(record, separators=(",", ":"), default=str), flush=True)


class ModelClient:
    """Bedrock first, Anthropic API as fallback.

    The fallback is loud, never silent: which route served a request determines
    whether Bedrock guardrail and invocation-logging evidence exists for it, so
    a quiet downgrade would make the evidence record wrong.
    """

    def __init__(self) -> None:
        self.route = "unavailable"

    def complete(self, system: str, prompt: str, max_tokens: int = 700) -> tuple[str, str]:
        if os.environ.get("USE_BEDROCK", "1") == "1":
            try:
                return self._bedrock(system, prompt, max_tokens), "bedrock"
            except Exception as exc:  # noqa: BLE001
                audit({"event": "bedrock_unavailable", "detail": f"{type(exc).__name__}: {exc}"})
        if os.environ.get("ANTHROPIC_API_KEY"):
            return self._anthropic(system, prompt, max_tokens), "anthropic"
        raise RuntimeError("no model route available: set AWS credentials or ANTHROPIC_API_KEY")

    def _bedrock(self, system: str, prompt: str, max_tokens: int) -> str:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        kwargs: dict[str, Any] = {
            "modelId": BEDROCK_MODEL,
            "body": json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        }
        if GUARDRAIL_ID:
            kwargs["guardrailIdentifier"] = GUARDRAIL_ID
            kwargs["guardrailVersion"] = os.environ.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT")
        response = client.invoke_model(**kwargs)
        body = json.loads(response["body"].read())
        return "".join(part.get("text", "") for part in body.get("content", []))

    def _anthropic(self, system: str, prompt: str, max_tokens: int) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        message = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in message.content if block.type == "text")


def run_inference(
    system_key: str,
    system_prompt: str,
    user_text: str,
    client: ModelClient,
    human_review_required: bool = False,
    max_tokens: int = 700,
) -> tuple[str, Inference]:
    """The path every app's inference takes, so every app gets the same controls."""
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    input_sha = hashlib.sha256(user_text.encode()).hexdigest()

    safe_text, pii = redact(user_text)
    refusal = guardrail(user_text)

    if refusal is not None:
        record = Inference(
            request_id=request_id, system_key=system_key, model="none", route="refused",
            input_sha256=input_sha, pii_detected=pii, guardrail_triggered=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            output_chars=len(refusal), human_review_required=False,
        )
        audit(record.as_log())
        return refusal, record

    # the model sees redacted text: personal data never needs to leave the app
    answer, route = client.complete(system_prompt, safe_text, max_tokens)
    record = Inference(
        request_id=request_id, system_key=system_key,
        model=BEDROCK_MODEL if route == "bedrock" else MODEL, route=route,
        input_sha256=input_sha, pii_detected=pii, guardrail_triggered=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        output_chars=len(answer), human_review_required=human_review_required,
    )
    audit(record.as_log())
    return answer, record


def system_card(
    key: str, name: str, purpose: str, risk_tier: str,
    oversight: str, limitations: list[str],
    route: str = "/chat", input_field: str = "message",
) -> dict[str, Any]:
    """What the system publishes about itself, machine-readably.

    Serving this is how an AI system makes its purpose, limits and oversight
    arrangements legible to a deployer without a human writing a document.
    """
    return {
        "name": name,
        "key": key,
        "purpose": purpose,
        "risk_tier": risk_tier,
        # How to exercise this system. Declared here so a behavioural probe can
        # adapt to each application instead of assuming every AI system answers
        # on /chat -- which is exactly the assumption that made the collector
        # silently useless against the screener and the retrieval assistant.
        "interaction": {"route": route, "input_field": input_field},
        "provider": os.environ.get("PROVIDER_NAME", "AI Control Graph demo estate"),
        "model": BEDROCK_MODEL if os.environ.get("USE_BEDROCK", "1") == "1" else MODEL,
        "model_pinned": True,
        "human_oversight": oversight,
        "limitations": limitations,
        "logging": {
            "inference_logged": True,
            "input_stored": "sha256 digest only",
            "pii_redacted_before_logging": True,
            "retention_days": int(os.environ.get("LOG_RETENTION_DAYS", 180)),
        },
        "guardrails": {
            "prompt_injection_refusal": True,
            "bedrock_guardrail": bool(GUARDRAIL_ID),
        },
        "rate_limit_per_minute": RATE_LIMIT_PER_MINUTE,
        "contact": os.environ.get("PROVIDER_CONTACT", "ai-governance@example.invalid"),
    }
