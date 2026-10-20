"""Report what works, what is degraded, and what is missing.

The distinction this draws matters more than the checks themselves. A
compliance tool that silently runs with half its evidence sources unavailable
produces a coverage picture that looks like an answer and is not one. So each
check reports one of:

    ok        working
    degraded  not available; here is exactly what you lose
    missing   nothing works without this

Exits non-zero only on `missing`, so a partial setup is usable and honest
rather than blocked.

    make doctor
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
MARK = {"ok": f"{GREEN}ok{RESET}", "degraded": f"{YELLOW}degraded{RESET}", "missing": f"{RED}missing{RESET}"}

results: list[tuple[str, str, str]] = []


def report(name: str, status: str, detail: str) -> None:
    results.append((name, status, detail))
    print(f"  {MARK[status]:<22} {name:<22} {DIM}{detail}{RESET}")


def check_database() -> None:
    url = os.environ.get("DATABASE_URL", "postgresql:///control_graph")
    try:
        import psycopg
    except ImportError:
        report("postgres", "missing", "psycopg not installed")
        return
    try:
        with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM control_node")
            nodes = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM control_edge")
            edges = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        report("postgres", "missing", f"{type(exc).__name__}: {str(exc).strip()[:90]}")
        return
    if nodes == 0:
        report("postgres", "degraded", "connected but empty — run `make seed`")
        return
    report("postgres", "ok", f"{nodes} controls, {edges} relationships")


def check_anthropic() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        report(
            "anthropic api", "degraded",
            "unset — apps have no model route, and evidence mapping falls back to rules",
        )
        return
    report("anthropic api", "ok", f"key present, model {os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-5-5')}")


def check_aws() -> None:
    try:
        import boto3
    except ImportError:
        report("aws", "degraded", "boto3 not installed — no AWS evidence")
        return
    if not (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")):
        report(
            "aws", "degraded",
            "no credentials — losing CloudTrail, IAM, S3, Bedrock guardrail and invocation logging evidence",
        )
        return
    try:
        identity = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1")).client("sts").get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        report("aws", "degraded", f"credentials rejected: {str(exc)[:70]}")
        return
    report("aws", "ok", f"account {identity['Account']} as {identity['Arn'].rsplit('/', 1)[-1]}")


def check_aws_is_readonly() -> None:
    """The collector must not be able to change the estate it audits (D9)."""
    try:
        import boto3

        session = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1"))
        identity = session.client("sts").get_caller_identity()
        user = identity["Arn"].rsplit("/", 1)[-1]
        attached = session.client("iam").list_attached_user_policies(UserName=user)
        names = [p["PolicyName"] for p in attached["AttachedPolicies"]]
    except Exception:  # noqa: BLE001
        return  # nothing to say when AWS itself is unavailable
    writable = [n for n in names if n not in {"SecurityAudit", "ReadOnlyAccess", "ViewOnlyAccess"}]
    if writable:
        report(
            "aws collector scope", "degraded",
            f"collector holds write policies {writable} — it can modify what it audits",
        )
    else:
        report("aws collector scope", "ok", f"read-only ({', '.join(names) or 'role-based'})")


def check_github() -> None:
    repo = os.environ.get("GITHUB_REPO")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        try:
            result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
            token = result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            token = None
    if not repo:
        report("github", "degraded", "GITHUB_REPO unset — no change-control evidence")
        return
    if not token:
        report("github", "degraded", "no token and `gh auth token` unavailable")
        return
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": "ai-control-graph"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            json.loads(response.read())
    except urllib.error.HTTPError as exc:
        report("github", "degraded", f"cannot read {repo}: HTTP {exc.code}")
        return
    except Exception as exc:  # noqa: BLE001
        report("github", "degraded", f"{type(exc).__name__}")
        return
    report("github", "ok", repo)


def check_endpoints() -> None:
    """Which registered systems the probe collector can actually reach."""
    try:
        import psycopg

        with psycopg.connect(os.environ.get("DATABASE_URL", "postgresql:///control_graph"),
                             connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT key, endpoint_url FROM ai_system ORDER BY key")
            systems = cur.fetchall()
    except Exception:  # noqa: BLE001
        return

    if not systems:
        report("ai systems", "degraded", "none registered — run `python -m collector.register`")
        return

    reachable = 0
    for key, endpoint in systems:
        if not endpoint:
            continue
        try:
            with urllib.request.urlopen(f"{endpoint.rstrip('/')}/healthz", timeout=6) as response:
                if response.status == 200:
                    reachable += 1
        except Exception:  # noqa: BLE001
            pass

    with_endpoints = sum(1 for _, e in systems if e)
    if reachable == 0:
        report(
            "ai system endpoints", "degraded",
            f"{len(systems)} registered, none reachable — no behavioural probe evidence",
        )
    else:
        report("ai system endpoints", "ok", f"{reachable}/{with_endpoints} reachable")


def check_secrets_not_committed() -> None:
    """Catch a credential on its way into a public repository."""
    patterns = ["AKIA", "sk-ant-", "ghp_", "-----BEGIN"]
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=20
        ).stdout.split()
    except Exception:  # noqa: BLE001
        return

    hits: list[str] = []
    for relative in tracked:
        path = REPO / relative
        if not path.is_file() or path.suffix in {".pdf", ".png", ".jpg", ".zip"}:
            continue
        try:
            content = path.read_text(errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            # the pattern list itself is not a finding
            if pattern in content and path.name != "doctor.py":
                hits.append(f"{relative} ({pattern})")
                break

    if hits:
        report("committed secrets", "missing", f"possible secrets in {', '.join(hits[:3])}")
    else:
        report("committed secrets", "ok", f"{len(tracked)} tracked files clean")


def main() -> int:
    print("\nai-control-graph — environment check\n")

    print(" core")
    check_database()
    check_secrets_not_committed()

    print("\n evidence sources")
    check_aws()
    check_aws_is_readonly()
    check_github()
    check_endpoints()

    print("\n model access")
    check_anthropic()

    missing = [r for r in results if r[1] == "missing"]
    degraded = [r for r in results if r[1] == "degraded"]

    print()
    if missing:
        print(f"{RED}{len(missing)} blocking problem(s).{RESET} Nothing works until these are fixed:")
        for name, _, detail in missing:
            print(f"  · {name}: {detail}")
        print(f"\nSee {REPO.name}/PREREQUISITES.md")
        return 1

    if degraded:
        print(f"{YELLOW}Usable, with {len(degraded)} degraded source(s).{RESET}")
        print("The graph, API and UI work. What you lose:")
        for name, _, detail in degraded:
            print(f"  · {name}: {detail}")
        print("\nNothing is simulated to fill these gaps — the evidence set is just smaller.")
        return 0

    print(f"{GREEN}Everything available.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
