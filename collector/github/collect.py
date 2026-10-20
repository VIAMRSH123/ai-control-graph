"""Collect real engineering-governance evidence from the GitHub API.

Branch protection, required review, code ownership, dependency alerting and CI
history are genuine controls. They are also the evidence most compliance tools
ignore, because they live in the development process rather than in the cloud
account — which is exactly why an AI system's change management, secure
development and third-party controls are so often unevidenced.

Falls back to the ``gh`` CLI's token when ``GITHUB_TOKEN`` is unset, so a
developer already logged in needs no extra setup.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, Iterator

from ..base import Artifact

API = "https://api.github.com"


def _token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


class GithubCollector:
    name = "github"

    def __init__(self, system_key: str, repo: str | None = None):
        self.system_key = system_key
        self.repo = repo or os.environ.get("GITHUB_REPO", "")
        self.token = _token()

    def available(self) -> tuple[bool, str]:
        if not self.repo:
            return False, "GITHUB_REPO not set"
        if not self.token:
            return False, "no GITHUB_TOKEN and `gh auth token` unavailable"
        try:
            self._get(f"/repos/{self.repo}")
        except Exception as exc:  # noqa: BLE001
            return False, f"cannot read {self.repo}: {exc}"
        return True, "ok"

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{API}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "ai-control-graph",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    def collect(self) -> Iterator[Artifact]:
        repo = self._get(f"/repos/{self.repo}")
        default_branch = repo["default_branch"]

        # branch protection: 404 when unprotected, which is the finding
        protection: dict[str, Any] | None
        try:
            protection = self._get(
                f"/repos/{self.repo}/branches/{default_branch}/protection"
            )
        except urllib.error.HTTPError:
            protection = None

        reviews = (protection or {}).get("required_pull_request_reviews") or {}
        payload = {
            "repository": self.repo,
            "default_branch": default_branch,
            "protected": protection is not None,
            "required_approving_reviews": reviews.get("required_approving_review_count"),
            "dismiss_stale_reviews": reviews.get("dismiss_stale_reviews"),
            "require_code_owner_reviews": reviews.get("require_code_owner_reviews"),
            "enforce_admins": ((protection or {}).get("enforce_admins") or {}).get("enabled"),
            "required_status_checks": bool((protection or {}).get("required_status_checks")),
            "private": repo.get("private"),
        }
        yield Artifact(
            artifact_type="repo_change_control",
            uri=repo["html_url"],
            payload=payload,
            summary=(
                f"{self.repo}@{default_branch}: "
                + ("protected, " if protection else "UNPROTECTED, ")
                + f"{reviews.get('required_approving_review_count', 0)} required reviews"
            ),
            suggests=[("iso27001", "A.8.32")],
        )

        # recent workflow runs: evidence that checks actually execute
        try:
            runs = self._get(f"/repos/{self.repo}/actions/runs?per_page=20")
            items = runs.get("workflow_runs", [])
            if items:
                conclusions: dict[str, int] = {}
                for run in items:
                    key = run.get("conclusion") or "running"
                    conclusions[key] = conclusions.get(key, 0) + 1
                yield Artifact(
                    artifact_type="ci_verification_runs",
                    uri=f"{repo['html_url']}/actions",
                    payload={
                        "sampled": len(items),
                        "conclusions": conclusions,
                        "latest": items[0].get("name"),
                        "latest_conclusion": items[0].get("conclusion"),
                    },
                    summary=f"{len(items)} recent CI runs: {conclusions}",
                    suggests=[("iso42001", "A.6.2.4")],
                )
        except urllib.error.HTTPError:
            pass

        # dependency vulnerability alerting
        try:
            alerts = self._get(f"/repos/{self.repo}/dependabot/alerts?state=open&per_page=50")
            severities: dict[str, int] = {}
            for alert in alerts:
                sev = (alert.get("security_advisory") or {}).get("severity", "unknown")
                severities[sev] = severities.get(sev, 0) + 1
            yield Artifact(
                artifact_type="dependency_vulnerability_alerts",
                uri=f"{repo['html_url']}/security/dependabot",
                payload={"open_alerts": len(alerts), "by_severity": severities},
                summary=f"{len(alerts)} open Dependabot alerts {severities or ''}".strip(),
                suggests=[("iso27001", "A.8.8")],
            )
        except urllib.error.HTTPError:
            # alerting disabled is itself worth recording
            yield Artifact(
                artifact_type="dependency_vulnerability_alerts",
                uri=f"{repo['html_url']}/security/dependabot",
                payload={"enabled": False},
                summary="Dependabot alerting not enabled or not readable",
                suggests=[("iso27001", "A.8.8")],
            )
