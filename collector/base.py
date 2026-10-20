"""Evidence collection primitives.

An artifact is something observed about a running system, addressed by the hash
of what was observed. The hash is what makes re-collection useful: if the digest
is unchanged the posture is unchanged, and if it moved something was reconfigured
between runs. That is drift detection for free, and it is why evidence is stored
as a payload plus digest rather than as prose.

Every collector records a ``collector_run`` whether it succeeds or fails. A
collector that silently returns nothing is indistinguishable from a compliant
estate, which is the most dangerous failure mode a tool like this has.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Protocol

#: How long an artifact stays valid. Compliance evidence has a shelf life; a
#: control "covered" by an eight-month-old snapshot is not covered.
DEFAULT_VALIDITY_DAYS = int(os.environ.get("EVIDENCE_MAX_AGE_DAYS", 30))


def digest(payload: Any) -> str:
    """Stable SHA256 over a payload, independent of key order."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Artifact:
    """One observation about a running system."""

    artifact_type: str
    uri: str
    payload: dict[str, Any]
    summary: str
    #: control references this artifact speaks to, as (framework_key, native_ref).
    #: The collector proposes; the mapper decides which single control gets the
    #: direct tag and lets the graph reach the rest.
    suggests: list[tuple[str, str]] = field(default_factory=list)
    validity_days: int = DEFAULT_VALIDITY_DAYS

    @property
    def sha256(self) -> str:
        return digest(self.payload)

    @property
    def valid_until(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(days=self.validity_days)


class Collector(Protocol):
    """Anything that can observe a system and yield artifacts."""

    name: str

    def available(self) -> tuple[bool, str]:
        """Whether this collector can run, and why not if it cannot."""

    def collect(self) -> Iterator[Artifact]:
        ...


class CollectorRun:
    """Records a run so that collection itself is observable.

    Used as a context manager so a crash still closes the run as failed rather
    than leaving it stuck in 'running' forever.
    """

    def __init__(self, conn, collector: str, ai_system_id: int | None = None):
        self.conn = conn
        self.collector = collector
        self.ai_system_id = ai_system_id
        self.run_id: int | None = None
        self.errors: list[dict[str, str]] = []
        self.artifacts_found = 0

    def __enter__(self) -> CollectorRun:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collector_run (collector, ai_system_id, status)
                VALUES (%s, %s, 'running') RETURNING id
                """,
                (self.collector, self.ai_system_id),
            )
            self.run_id = cur.fetchone()[0]
        self.conn.commit()
        return self

    def error(self, where: str, detail: str) -> None:
        """Record a partial failure without aborting the whole run.

        One unreachable service should degrade a run to 'partial', not discard
        the evidence the other services did produce.
        """
        self.errors.append({"where": where, "detail": detail})

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.errors.append({"where": "run", "detail": f"{exc_type.__name__}: {exc}"})
            status = "failed"
        elif self.errors:
            status = "partial"
        else:
            status = "ok"

        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE collector_run
                SET finished_at = now(), status = %s, artifacts_found = %s, errors = %s
                WHERE id = %s
                """,
                (status, self.artifacts_found, json.dumps(self.errors), self.run_id),
            )
        self.conn.commit()
        return False  # never swallow the exception


def store(conn, run: CollectorRun, ai_system_id: int, collector: str,
          artifact: Artifact) -> int:
    """Persist an artifact, replacing any earlier copy of the same observation.

    Keyed on (system, artifact_type, uri) rather than on the digest, so that a
    changed configuration updates the artifact in place and the digest change
    is what signals drift.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO evidence (ai_system_id, run_id, collector, artifact_type,
                                  uri, sha256, payload, summary, valid_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (ai_system_id, run.run_id, collector, artifact.artifact_type,
             artifact.uri, artifact.sha256, json.dumps(artifact.payload, default=str),
             artifact.summary, artifact.valid_until),
        )
        evidence_id = cur.fetchone()[0]
    run.artifacts_found += 1
    return evidence_id
