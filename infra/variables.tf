variable "region" {
  description = "One region, used everywhere. us-east-1 has the widest Bedrock model availability."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment suffix for resource names."
  type        = string
  default     = "demo"
}

variable "deploy_profile" {
  description = "Named AWS profile with write access. NOT the collector's read-only credential."
  type        = string
  default     = "control-graph-deployer"
}

variable "bedrock_model_id" {
  description = "Pinned model id. Pinned deliberately: an unpinned model changes system behaviour without a change record."
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "rate_limit_per_minute" {
  type    = number
  default = 20
}

variable "provider_name" {
  description = "Shown on each AI system card as the provider."
  type        = string
  default     = "AI Control Graph demo estate"
}

variable "provider_contact" {
  type    = string
  default = "ai-governance@example.invalid"
}
