#  AI Control Graph — the estate under governance.
#
#  Three AI applications on Lambda behind function URLs, plus the AWS-side
#  controls the collector later looks for evidence of.
#
#  This file is itself evidence. Declarative infrastructure under version
#  control, changed through reviewed pull requests, is what a change-management
#  control asks for — so the collector reads the posture this creates, and the
#  repo's own branch protection evidences how the posture gets changed.
#
#  Deliberately configured to PASS the controls, except where a deliberate gap
#  makes the demo honest: see `doc_rag` log retention below.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }
  # Local state is acceptable for a demo and unacceptable in production, where
  # it belongs in S3 with DynamoDB locking. Called out rather than left implicit.
}

provider "aws" {
  region  = var.region
  profile = var.deploy_profile

  default_tags {
    tags = {
      Project   = "ai-control-graph"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = "acg-${var.environment}"

  apps = {
    support_chat = {
      key            = "support-chat"
      handler        = "apps.support_chat.main.handler"
      risk_tier      = "limited"
      retention_days = 180
      timeout        = 30
    }
    cv_screener = {
      key            = "cv-screener"
      handler        = "apps.cv_screener.main.handler"
      risk_tier      = "high_risk"
      retention_days = 180
      timeout        = 60
    }
    doc_rag = {
      key       = "doc-rag"
      handler   = "apps.doc_rag.main.handler"
      risk_tier = "limited"
      # Deliberately below the EU AI Act Art.19 six-month minimum.
      #
      # A compliance demo where everything passes proves only that the fixtures
      # were written to pass. This one real shortfall means the collector has
      # something true to find, and the coverage view has a genuine gap to show.
      retention_days = 30
      timeout        = 30
    }
  }
}

# ---------------------------------------------------------------- logging

# CloudWatch log groups, created explicitly rather than implicitly by Lambda,
# so retention is declared rather than defaulting to "never expire".
resource "aws_cloudwatch_log_group" "app" {
  for_each          = local.apps
  name              = "/aws/lambda/${local.prefix}-${each.value.key}"
  retention_in_days = each.value.retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_cloudwatch_log_group" "bedrock" {
  name              = "/aws/bedrock/${local.prefix}-model-invocations"
  retention_in_days = 180
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_kms_key" "logs" {
  description             = "Encrypts AI application and model invocation logs"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  # CloudWatch Logs must be able to use the key for the log groups above
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRoot"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action    = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
      }
    ]
  })
}

# --------------------------------------------------------------- evidence

# Where model invocation payloads land. Encrypted, versioned, private.
resource "aws_s3_bucket" "evidence" {
  bucket        = "${local.prefix}-model-invocations-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.logs.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --------------------------------------------------------------- the apps

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = "${path.module}/../"
  output_path = "${path.module}/.build/app.zip"
  excludes    = ["web", "infra", "docs", ".git", "db", "tests", "node_modules"]
}

resource "aws_iam_role" "lambda" {
  for_each = local.apps
  name     = "${local.prefix}-${each.value.key}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Least privilege, deliberately: logs it owns, and Bedrock invoke. Nothing else.
resource "aws_iam_role_policy" "lambda" {
  for_each = local.apps
  name     = "least-privilege"
  role     = aws_iam_role.lambda[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.app[each.key].arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:ApplyGuardrail"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "app" {
  for_each = local.apps

  function_name    = "${local.prefix}-${each.value.key}"
  role             = aws_iam_role.lambda[each.key].arn
  handler          = each.value.handler
  runtime          = "python3.12"
  timeout          = each.value.timeout
  memory_size      = 512
  filename         = data.archive_file.bundle.output_path
  source_code_hash = data.archive_file.bundle.output_base64sha256

  environment {
    variables = {
      USE_BEDROCK             = "1"
      BEDROCK_MODEL_ID        = var.bedrock_model_id
      BEDROCK_GUARDRAIL_ID    = aws_bedrock_guardrail.estate.guardrail_id
      AWS_LAMBDA_EXEC_WRAPPER = ""
      LOG_RETENTION_DAYS      = tostring(each.value.retention_days)
      RATE_LIMIT_PER_MINUTE   = tostring(var.rate_limit_per_minute)
      PROVIDER_NAME           = var.provider_name
      PROVIDER_CONTACT        = var.provider_contact
    }
  }

  depends_on = [aws_cloudwatch_log_group.app]
}

resource "aws_lambda_function_url" "app" {
  for_each           = local.apps
  function_name      = aws_lambda_function.app[each.key].function_name
  authorization_type = "NONE" # public demo endpoints; probes must reach them
  cors {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST"]
  }
}

# -------------------------------------------------------------- guardrails

resource "aws_bedrock_guardrail" "estate" {
  name                      = "${local.prefix}-guardrail"
  blocked_input_messaging   = "That request can't be processed."
  blocked_outputs_messaging = "That response was withheld."
  description               = "Content and PII controls applied to every model call in the estate"

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT", "PROMPT_ATTACK"]
      content {
        type           = filters_config.value
        input_strength = "HIGH"
        # PROMPT_ATTACK is an input-only filter; AWS rejects an output strength on it
        output_strength = filters_config.value == "PROMPT_ATTACK" ? "NONE" : "HIGH"
      }
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = ["EMAIL", "PHONE", "CREDIT_DEBIT_CARD_NUMBER", "US_SOCIAL_SECURITY_NUMBER", "NAME", "ADDRESS"]
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }
}

# Every model call recorded. This is the closest AWS gets to EU AI Act Art.12.
resource "aws_bedrock_model_invocation_logging_configuration" "estate" {
  logging_config {
    embedding_data_delivery_enabled = false
    image_data_delivery_enabled     = false
    text_data_delivery_enabled      = true

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.bedrock.name
      role_arn       = aws_iam_role.bedrock_logging.arn

      large_data_delivery_s3_config {
        bucket_name = aws_s3_bucket.evidence.id
      }
    }
  }

  depends_on = [aws_iam_role_policy.bedrock_logging]
}

resource "aws_iam_role" "bedrock_logging" {
  name = "${local.prefix}-bedrock-logging"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_logging" {
  name = "write-invocation-logs"
  role = aws_iam_role.bedrock_logging.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.bedrock.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.evidence.arn}/*"
      }
    ]
  })
}

# ------------------------------------------------------------- audit trail

resource "aws_cloudtrail" "estate" {
  name                          = "${local.prefix}-trail"
  s3_bucket_name                = aws_s3_bucket.evidence.id
  s3_key_prefix                 = "cloudtrail"
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true # tamper evidence
  kms_key_id                    = aws_kms_key.logs.arn

  depends_on = [aws_s3_bucket_policy.cloudtrail]
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.evidence.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.evidence.arn
      },
      {
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.evidence.arn}/cloudtrail/*"
        Condition = {
          StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" }
        }
      }
    ]
  })
}

# ----------------------------------------------------------- the collector

# Read-only by construction. The collector must not be able to change the
# estate it audits — see docs/DECISIONS.md D9.
resource "aws_iam_user" "collector" {
  name          = "${local.prefix}-collector"
  force_destroy = true
}

resource "aws_iam_user_policy_attachment" "collector" {
  user       = aws_iam_user.collector.name
  policy_arn = "arn:aws:iam::aws:policy/SecurityAudit"
}
