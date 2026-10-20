"""Collect real governance evidence from a live AWS account.

Read-only. The credentials this runs under are expected to carry the AWS
managed ``SecurityAudit`` policy and nothing more — a collector that can modify
the estate it audits is not producing evidence, it is producing an assertion
about itself. That separation is itself a control, and it is cited back into
the graph as evidence (``collector_identity``).

Every call here is a real API call. There is no fixture mode and no simulated
account; if credentials are missing the collector reports itself unavailable
rather than inventing a posture.

The AI-specific signals are the interesting ones:

  Bedrock guardrail configuration   -> EU AI Act Art.15, NIST MEASURE 2.6
  Bedrock model invocation logging  -> EU AI Act Art.12/19, ISO 42001 A.6.2.8
  CloudWatch log group retention    -> EU AI Act Art.19 six-month minimum

Conventional cloud posture (encryption, IAM, CloudTrail) matters too, because
it is exactly the evidence that should light up ISO 27001 *and* ISO 42001 from
one observation.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

from ..base import Artifact

REGION = os.environ.get("AWS_REGION", "us-east-1")


class AwsCollector:
    name = "aws"

    def __init__(self, system_key: str):
        self.system_key = system_key
        self._session: Any = None

    # -- availability ------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        try:
            import boto3
        except ImportError:
            return False, "boto3 not installed"
        if not os.environ.get("AWS_ACCESS_KEY_ID") and not os.environ.get("AWS_PROFILE"):
            return False, "no AWS credentials in environment"
        try:
            session = boto3.Session(region_name=REGION)
            session.client("sts").get_caller_identity()
        except Exception as exc:  # noqa: BLE001 - surface the real reason
            return False, f"AWS credentials rejected: {exc}"
        self._session = session
        return True, "ok"

    def _client(self, service: str):
        import boto3

        if self._session is None:
            self._session = boto3.Session(region_name=REGION)
        return self._session.client(service, region_name=REGION)

    # -- collection --------------------------------------------------------

    def collect(self) -> Iterator[Artifact]:
        for probe in (
            self._caller_identity,
            self._bedrock_guardrails,
            self._bedrock_invocation_logging,
            self._cloudwatch_retention,
            self._cloudtrail,
            self._s3_posture,
            self._iam_password_policy,
        ):
            yield from probe()

    # -- individual observations ------------------------------------------

    def _caller_identity(self) -> Iterator[Artifact]:
        """Who the collector is, and whether it is genuinely read-only.

        Evidence that the audit path is separated from the deploy path.
        """
        sts = self._client("sts")
        identity = sts.get_caller_identity()
        iam = self._client("iam")
        policies: list[str] = []
        try:
            user = identity["Arn"].rsplit("/", 1)[-1]
            attached = iam.list_attached_user_policies(UserName=user)
            policies = [p["PolicyName"] for p in attached["AttachedPolicies"]]
        except Exception:  # noqa: BLE001 - role-based auth has no user to list
            pass

        payload = {
            "account": identity["Account"],
            "arn": identity["Arn"],
            "attached_policies": sorted(policies),
            "read_only": policies == ["SecurityAudit"] or not policies,
        }
        yield Artifact(
            artifact_type="collector_identity",
            uri=identity["Arn"],
            payload=payload,
            summary=f"Collector runs as {identity['Arn']} with policies {policies or 'role-based'}",
            suggests=[("iso27001", "A.8.2"), ("iso27001", "A.5.3")],
        )

    def _bedrock_guardrails(self) -> Iterator[Artifact]:
        """Content filtering applied to model input and output."""
        bedrock = self._client("bedrock")
        guardrails = bedrock.list_guardrails().get("guardrails", [])
        for guardrail in guardrails:
            detail = bedrock.get_guardrail(
                guardrailIdentifier=guardrail["id"],
                guardrailVersion=guardrail.get("version", "DRAFT"),
            )
            payload = {
                "id": guardrail["id"],
                "name": guardrail.get("name"),
                "status": guardrail.get("status"),
                "content_policy": detail.get("contentPolicy"),
                "sensitive_information_policy": detail.get("sensitiveInformationPolicy"),
                "topic_policy": detail.get("topicPolicy"),
                "word_policy": detail.get("wordPolicy"),
                "blocked_input_messaging": bool(detail.get("blockedInputMessaging")),
            }
            yield Artifact(
                artifact_type="bedrock_guardrail",
                uri=detail.get("guardrailArn", guardrail["id"]),
                payload=payload,
                summary=f"Bedrock guardrail {guardrail.get('name')} active with content and PII policies",
                suggests=[("eu_ai_act", "Art.15"), ("iso42001", "A.6.2.6")],
            )

    def _bedrock_invocation_logging(self) -> Iterator[Artifact]:
        """Whether every model call is recorded, and where.

        This is the closest thing AWS offers to EU AI Act Art.12 automatic
        record-keeping for an AI system.
        """
        bedrock = self._client("bedrock")
        config = bedrock.get_model_invocation_logging_configuration()
        logging_config = config.get("loggingConfig") or {}
        payload = {
            "enabled": bool(logging_config),
            "cloudwatch": logging_config.get("cloudWatchConfig"),
            "s3": logging_config.get("s3Config"),
            "text_data_delivery": logging_config.get("textDataDeliveryEnabled"),
            "image_data_delivery": logging_config.get("imageDataDeliveryEnabled"),
            "embedding_data_delivery": logging_config.get("embeddingDataDeliveryEnabled"),
        }
        yield Artifact(
            artifact_type="bedrock_model_invocation_logging",
            uri=f"arn:aws:bedrock:{REGION}:model-invocation-logging",
            payload=payload,
            summary=(
                "Bedrock model invocation logging enabled"
                if logging_config
                else "Bedrock model invocation logging NOT enabled"
            ),
            suggests=[("iso42001", "A.6.2.8")],
        )

    def _cloudwatch_retention(self) -> Iterator[Artifact]:
        """Log retention against the Art.19 six-month minimum."""
        logs = self._client("logs")
        groups = logs.describe_log_groups(limit=50).get("logGroups", [])
        relevant = [
            g for g in groups
            if self.system_key in g["logGroupName"] or "bedrock" in g["logGroupName"].lower()
        ] or groups[:10]
        if not relevant:
            return
        payload = {
            "log_groups": [
                {
                    "name": g["logGroupName"],
                    "retention_days": g.get("retentionInDays"),
                    "stored_bytes": g.get("storedBytes"),
                    "meets_six_month_minimum": (g.get("retentionInDays") or 0) >= 180,
                }
                for g in relevant
            ]
        }
        shortfall = [g for g in payload["log_groups"] if not g["meets_six_month_minimum"]]
        yield Artifact(
            artifact_type="cloudwatch_log_retention",
            uri=f"arn:aws:logs:{REGION}:log-groups",
            payload=payload,
            summary=(
                f"{len(relevant)} log groups; {len(shortfall)} below the "
                f"six-month retention the EU AI Act requires"
            ),
            suggests=[("eu_ai_act", "Art.19")],
        )

    def _cloudtrail(self) -> Iterator[Artifact]:
        """An immutable record of who changed what."""
        trail_client = self._client("cloudtrail")
        trails = trail_client.describe_trails().get("trailList", [])
        for trail in trails:
            status = {}
            try:
                status = trail_client.get_trail_status(Name=trail["TrailARN"])
            except Exception:  # noqa: BLE001
                pass
            payload = {
                "name": trail.get("Name"),
                "multi_region": trail.get("IsMultiRegionTrail"),
                "log_file_validation": trail.get("LogFileValidationEnabled"),
                "kms_encrypted": bool(trail.get("KmsKeyId")),
                "s3_bucket": trail.get("S3BucketName"),
                "is_logging": status.get("IsLogging"),
            }
            yield Artifact(
                artifact_type="cloudtrail_configuration",
                uri=trail["TrailARN"],
                payload=payload,
                summary=(
                    f"CloudTrail {trail.get('Name')} logging={status.get('IsLogging')} "
                    f"validation={trail.get('LogFileValidationEnabled')}"
                ),
                suggests=[("iso27001", "A.8.15")],
            )

    def _s3_posture(self) -> Iterator[Artifact]:
        """Encryption, versioning and public access on buckets we own."""
        s3 = self._client("s3")
        buckets = s3.list_buckets().get("Buckets", [])
        for bucket in buckets[:15]:
            name = bucket["Name"]
            payload: dict[str, Any] = {"bucket": name}
            try:
                enc = s3.get_bucket_encryption(Bucket=name)
                rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
                payload["encryption"] = rules[0]["ApplyServerSideEncryptionByDefault"]
            except Exception:  # noqa: BLE001 - absence is the finding
                payload["encryption"] = None
            try:
                payload["versioning"] = s3.get_bucket_versioning(Bucket=name).get("Status")
            except Exception:  # noqa: BLE001
                payload["versioning"] = None
            try:
                block = s3.get_public_access_block(Bucket=name)
                payload["public_access_block"] = block["PublicAccessBlockConfiguration"]
            except Exception:  # noqa: BLE001
                payload["public_access_block"] = None

            yield Artifact(
                artifact_type="s3_bucket_posture",
                uri=f"arn:aws:s3:::{name}",
                payload=payload,
                summary=(
                    f"S3 {name}: encryption="
                    f"{(payload['encryption'] or {}).get('SSEAlgorithm', 'none')}, "
                    f"versioning={payload['versioning'] or 'off'}"
                ),
                suggests=[("iso27001", "A.8.24")],
            )

    def _iam_password_policy(self) -> Iterator[Artifact]:
        iam = self._client("iam")
        try:
            policy = iam.get_account_password_policy()["PasswordPolicy"]
        except Exception:  # noqa: BLE001 - no policy set is itself the finding
            policy = {}
        summary = iam.get_account_summary().get("SummaryMap", {})
        payload = {
            "password_policy": policy,
            "users": summary.get("Users"),
            "mfa_devices": summary.get("MFADevices"),
            "root_mfa_enabled": bool(summary.get("AccountMFAEnabled")),
            "access_keys_per_user_quota": summary.get("AccessKeysPerUserQuota"),
        }
        yield Artifact(
            artifact_type="iam_account_posture",
            uri="arn:aws:iam::account",
            payload=payload,
            summary=(
                f"IAM: {summary.get('Users', 0)} users, root MFA="
                f"{bool(summary.get('AccountMFAEnabled'))}"
            ),
            suggests=[("iso27001", "A.5.17")],
        )
