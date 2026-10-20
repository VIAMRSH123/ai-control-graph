# Infrastructure

The estate under governance: three AI applications on Lambda, plus the AWS-side
controls the collector looks for evidence of.

## Deploy

```bash
cd infra
terraform init
terraform plan   -var="deploy_profile=control-graph-deployer"
terraform apply  -var="deploy_profile=control-graph-deployer"
```

Then record the endpoints so the probe collector can reach them:

```bash
terraform output -json endpoints
```

and set each one on its system with the `UPDATE ai_system` statement that
`terraform output register_endpoints_command` prints.

## What this creates

| Resource | Why it is here |
|---|---|
| 3 Lambda functions + function URLs | the AI systems themselves |
| Bedrock guardrail | content and PII filtering on every model call |
| Bedrock model invocation logging | every inference recorded — the closest AWS analogue to EU AI Act Art.12 |
| CloudWatch log groups, KMS-encrypted | application and model logs, with declared retention |
| CloudTrail, multi-region, validated | who changed what, with tamper evidence |
| S3 bucket, encrypted + versioned + private | invocation payloads and trail storage |
| IAM collector user with `SecurityAudit` | read-only audit identity, separate from the deployer |

## One deliberate gap

`doc-rag`'s log group is set to **30 days**, below the six-month minimum EU AI
Act Art.19 requires.

That is intentional. A compliance demo where everything passes proves only that
the fixtures were written to pass. This shortfall is real, the collector finds
it, and the coverage view shows a genuine gap rather than a uniform wall of
green.

## Cost

Lambda, CloudWatch, S3 and CloudTrail sit inside or near the free tier at demo
volumes. **Bedrock is metered from the first token and has no free tier.** Set a
billing alarm before applying — see [PREREQUISITES.md](../PREREQUISITES.md).

`terraform destroy` removes everything except the CloudWatch log data already
written, which ages out on its retention schedule.

## Known limitations

- **Local state.** Fine for a demo, unacceptable in production, where state
  belongs in S3 with DynamoDB locking.
- **Function URLs are unauthenticated.** The probe collector has to reach them,
  and the demo has no user identity layer. A real deployment would put them
  behind an authorizer.
- **The collector's access key is not created by Terraform.** Minting it here
  would write the secret into state. Create it in the IAM console instead.
