# Lifecycle policy contract test for the ECR module.
#
# Runs in plan mode so no AWS API calls are made and no credentials are
# required. The provider block below sets the offline-safe flags so the AWS
# provider can initialize without contacting AWS.
#
# The load-bearing assertion is in run "rule_2_scoped_to_ephemeral_prefixes":
# it pins rule 2's tagPrefixList to ["sha-", "pr-"], which is the regression
# guard for issue #5 — if a future edit re-broadens the rule to
# tagStatus = "any", :latest and :<version> tags would start getting culled
# again.

provider "aws" {
  region                      = "us-east-1"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
}

variables {
  name_prefix = "forge-test"
}

run "repository_has_scan_on_push" {
  command = plan

  assert {
    condition     = aws_ecr_repository.forge.image_scanning_configuration[0].scan_on_push == true
    error_message = "ECR repository must have scan_on_push enabled so pushed images are scanned for known CVEs."
  }
}

run "lifecycle_policy_has_two_rules" {
  command = plan

  assert {
    condition     = length(jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules) == 2
    error_message = "ECR lifecycle policy must contain exactly 2 rules (untagged + ephemeral-prefix-scoped expiry)."
  }
}

run "rule_1_expires_untagged_after_one_day" {
  command = plan

  assert {
    condition = (
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[0].rulePriority == 1 &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[0].selection.tagStatus == "untagged" &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[0].selection.countUnit == "days" &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[0].selection.countNumber == 1
    )
    error_message = "Rule 1 must expire untagged images after 1 day."
  }
}

run "rule_2_scoped_to_ephemeral_prefixes" {
  command = plan

  # Regression guard for issue #5: rule 2's prefix list MUST be exactly
  # ["sha-", "pr-"]. Anything else (e.g., reverting to tagStatus = "any")
  # would re-broaden the cull and start pruning :latest / :<version> tags.
  assert {
    condition = (
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[1].rulePriority == 2 &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[1].selection.tagStatus == "tagged" &&
      length(jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[1].selection.tagPrefixList) == 2 &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[1].selection.tagPrefixList[0] == "sha-" &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[1].selection.tagPrefixList[1] == "pr-" &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[1].selection.countUnit == "days" &&
      jsondecode(aws_ecr_lifecycle_policy.forge.policy).rules[1].selection.countNumber == 30
    )
    error_message = "Rule 2 must target only the sha- and pr- tag prefixes with a 30-day TTL (issue #5 regression guard)."
  }
}
