output "endpoints" {
  description = "Function URLs. Record these against each ai_system so the probe collector can reach them."
  value       = { for k, v in aws_lambda_function_url.app : local.apps[k].key => v.function_url }
}

output "collector_user" {
  description = "Read-only identity for the collector. Create its access key in the console; Terraform does not mint it so the secret never enters state."
  value       = aws_iam_user.collector.name
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.estate.guardrail_id
}

output "evidence_bucket" {
  value = aws_s3_bucket.evidence.id
}

output "register_endpoints_command" {
  description = "Copy the endpoints into the registry so probes know where to look."
  value       = "psql $DATABASE_URL -c \"UPDATE ai_system SET endpoint_url='<url>' WHERE key='<key>';\""
}
