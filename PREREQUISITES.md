# Prerequisites

Everything you need in place before this project runs end to end. Items marked **BLOCKING**
gate the live-evidence demo; everything else can be done while the graph is being built.

Work through section 1 first — Bedrock model access is the slowest item and is the only one
that can realistically hold up the demo.

---

## 1. AWS — BLOCKING, do this first

The collector reads real configuration out of a real AWS account. There is no simulated mode
and no fixture data standing in for it, which was an explicit requirement.

### 1.1 Create the account

<https://portal.aws.amazon.com/billing/signup>. A payment card is required at signup. The
estate this project builds sits inside or near the free tier, but **Amazon Bedrock is metered
from the first token** — it has no free tier.

### 1.2 Set a billing alarm before deploying anything

Billing → Budgets → Create budget → Zero spend or a $10 monthly cost budget with an email
alert at 80%. Do this *before* step 1.4, not after.

### 1.3 Request Bedrock model access — the long pole

Bedrock console → Model access → Enable specific models → request **Anthropic Claude**.
Approval is sometimes instant and sometimes takes hours. Request it now; the rest of the
setup can proceed while it is pending.

Region matters. Pick one region and use it everywhere. `us-east-1` has the widest model
availability and is the default assumed throughout this repo.

### 1.4 Two sets of credentials

The collector and the deployer are deliberately separated. The collector must never be able
to change the estate it is auditing — a collector that can mutate its own evidence is not
evidence. This separation is itself an auditable control and is cited in the graph.

**Collector — read-only.** IAM → Users → create `control-graph-collector`, attach the AWS
managed policy `SecurityAudit`, create an access key.

**Deployer — write.** IAM → Users → create `control-graph-deployer` with permissions for
Lambda, IAM role creation, S3, CloudWatch Logs, CloudTrail and Bedrock. `PowerUserAccess`
plus `IAMFullAccess` is the pragmatic choice for a demo account; do not use it in a real one.

Never commit either key. Both go in `.env`, which is gitignored.

---

## 2. Neon Postgres — BLOCKING

The control graph lives in Postgres. <https://neon.tech> free tier is sufficient.

Create a project, copy the pooled connection string into `.env` as `DATABASE_URL`. It looks
like `postgresql://user:pass@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`.

Postgres 15+. No extensions are required — the graph is plain tables plus recursive CTEs, by
design (see [docs/DECISIONS.md](docs/DECISIONS.md)).

---

## 3. Anthropic API key

<https://console.anthropic.com> → API keys. Used for two things: the fallback path for the
three AI applications when Bedrock access is still pending, and the agent that proposes
evidence-to-control mappings.

Set `ANTHROPIC_API_KEY` in `.env`.

---

## 4. Local tooling

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | preinstalled on macOS, or `brew install python@3.12` |
| uv | latest | `brew install uv` |
| Node | 20+ | `brew install node` |
| Terraform | 1.6+ | `brew install terraform` |
| AWS CLI | v2 | `brew install awscli` |
| GitHub CLI | latest | `brew install gh` |

---

## 5. GitHub token

The collector reads repository governance posture — branch protection, required reviews,
CODEOWNERS, Dependabot alerts, signed commits, CI runs — as real evidence.

If `gh auth status` already shows you logged in, the collector reuses that token and there is
nothing to do. Otherwise create a fine-grained PAT with read access to this repository and set
`GITHUB_TOKEN` in `.env`.

---

## 6. Fill in `.env`

```bash
cp .env.example .env
```

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Neon pooled connection string |
| `ANTHROPIC_API_KEY` | yes | apps + mapping agent |
| `AWS_REGION` | yes | pick one and keep it, e.g. `us-east-1` |
| `AWS_ACCESS_KEY_ID` | live demo | collector, read-only |
| `AWS_SECRET_ACCESS_KEY` | live demo | collector, read-only |
| `AWS_DEPLOY_PROFILE` | deploy only | named profile for Terraform |
| `GITHUB_TOKEN` | optional | falls back to `gh` CLI auth |
| `PROPAGATION_THRESHOLD` | no | default `0.45` |
| `PROPAGATION_MAX_DEPTH` | no | default `3` |

---

## 7. Verify your setup

```bash
make doctor
```

Checks every credential, reports exactly what is missing, and exits non-zero if the live
demo cannot run. It distinguishes "missing, demo degraded" from "missing, nothing works".

---

## What works without AWS

If AWS access is still pending, most of the system still demos. The framework graph, the
crosswalk, the API, the propagation engine and the full UI are all independent of AWS. The
GitHub and live-probe collectors produce real evidence on their own.

What you lose without AWS is the AWS-sourced evidence specifically — CloudTrail, IAM posture,
S3 encryption, Bedrock guardrail configuration and model invocation logging. `make doctor`
will say so plainly rather than silently substituting fake data.
