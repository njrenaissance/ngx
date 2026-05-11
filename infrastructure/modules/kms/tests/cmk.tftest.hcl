# Contract tests for the KMS module.
#
# Runs in plan mode so no AWS API calls are made and no credentials are
# required. The provider block below sets the offline-safe flags so the AWS
# provider can initialize without contacting AWS. Mirrors the pattern in
# infrastructure/modules/ecr/tests/lifecycle.tftest.hcl.
#
# The load-bearing assertions:
#   - rotation enabled (Option-1 rubric requirement; CMK without rotation is
#     a security regression)
#   - alias matches the forge-<env> regex (consumers find the key by alias)
#   - no `Principal: "*"` in the key policy (the entire reason we use a CMK
#     instead of the AWS-managed default is to control who can decrypt)
#   - deletion window >= 7 days (AWS minimum; shorter is impossible)

provider "aws" {
  region                      = "us-east-1"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
}

variables {
  name_prefix = "forge-test"
  environment = "test"
  # Stand-in for the OIDC deploy role ARN that production passes in.
  # Lets the KeyAdministrators-statement assertions run with a known input.
  key_administrator_role_arns = ["arn:aws:iam::123456789012:role/test-key-admin"]
}

run "rotation_enabled" {
  command = plan

  assert {
    condition     = aws_kms_key.main.enable_key_rotation == true
    error_message = "CMK must have annual rotation enabled. Disabling rotation is a security regression — keys without rotation accumulate cryptographic risk over their lifetime."
  }
}

run "deletion_window_at_least_minimum" {
  command = plan

  assert {
    condition     = aws_kms_key.main.deletion_window_in_days >= 7
    error_message = "CMK deletion_window_in_days must be at least 7 days (AWS-imposed minimum). Variable validation should also catch this — this assertion is a defense-in-depth guard."
  }
}

run "alias_matches_forge_naming" {
  command = plan

  assert {
    condition     = can(regex("^alias/forge-[a-z0-9-]+$", aws_kms_alias.main.name))
    error_message = "CMK alias must match `^alias/forge-[a-z0-9-]+$` so it stays discoverable under the forge-* naming convention used everywhere else."
  }
}

run "key_policy_has_no_wildcard_principal" {
  command = plan

  # Regression guard: re-introducing `Principal: "*"` would defeat the
  # entire purpose of running a customer-managed key (anyone with the key
  # ARN could then decrypt). Walks every statement; passes only if zero
  # statements have a wildcard principal in any form.
  assert {
    condition = length([
      for s in jsondecode(aws_kms_key.main.policy).Statement :
      s if(
        try(s.Principal, null) == "*" ||
        try(s.Principal.AWS, null) == "*" ||
        try(contains(s.Principal.AWS, "*"), false)
      )
    ]) == 0
    error_message = "CMK key policy must not contain any statement with `Principal: \"*\"` or `Principal.AWS: \"*\"`. The whole point of a CMK is explicit principal control."
  }
}

run "key_policy_grants_root_full_access" {
  command = plan

  # Without root account access, the key becomes orphaned — there's no way
  # for a human admin to revise the policy or recover from a lockout.
  # AWS specifically warns against deploying CMKs without this statement.
  assert {
    condition = length([
      for s in jsondecode(aws_kms_key.main.policy).Statement :
      s if(
        try(s.Sid, "") == "EnableRootAccountAccess" &&
        s.Effect == "Allow" &&
        s.Action == "kms:*"
      )
    ]) == 1
    error_message = "CMK key policy must include exactly one EnableRootAccountAccess statement granting `kms:*` to the account root. Without it the key is orphaned and unrecoverable."
  }
}

run "key_policy_has_explicit_key_administrators_statement" {
  command = plan

  # Separation-of-duties guard: the day-to-day Terraform principal must
  # have an explicit KeyAdministrators statement, not rely on the safety-net
  # root statement. Catches accidental removal of the statement during
  # refactors (which would silently shift admin privileges back to root).
  assert {
    condition = length([
      for s in jsondecode(aws_kms_key.main.policy).Statement :
      s if try(s.Sid, "") == "KeyAdministrators"
    ]) == 1
    error_message = "Key policy must include exactly one KeyAdministrators statement so administration flows through an explicit principal, not the safety-net root statement."
  }
}

run "key_administrators_have_no_encrypt_decrypt_actions" {
  command = plan

  # The whole point of separating Admin from User: the Admin role
  # provisions/manages the key but should NOT be able to read encrypted
  # data. If this assertion fails, the Admin role can read every secret
  # the key protects — that's the breach this split was designed to prevent.
  assert {
    condition = length([
      for s in jsondecode(aws_kms_key.main.policy).Statement :
      s if(
        try(s.Sid, "") == "KeyAdministrators" &&
        anytrue([
          contains(s.Action, "kms:Encrypt"),
          contains(s.Action, "kms:Decrypt"),
          contains(s.Action, "kms:ReEncrypt*"),
          contains(s.Action, "kms:GenerateDataKey*"),
        ])
      )
    ]) == 0
    error_message = "KeyAdministrators must not include Encrypt/Decrypt/ReEncrypt/GenerateDataKey actions — those are Key User actions. Mixing them defeats separation of duties."
  }
}

run "key_policy_grants_required_service_principals" {
  command = plan

  # The three AWS services that need to use this CMK must all appear
  # in the policy as Service principals. Catches accidental deletion of
  # any single statement during refactors.
  assert {
    condition = length([
      for s in jsondecode(aws_kms_key.main.policy).Statement :
      s if try(s.Principal.Service, "") == "rds.amazonaws.com"
    ]) == 1
    error_message = "CMK key policy must grant rds.amazonaws.com encrypt/decrypt — Aurora cluster storage encryption requires this."
  }

  assert {
    condition = length([
      for s in jsondecode(aws_kms_key.main.policy).Statement :
      s if try(s.Principal.Service, "") == "secretsmanager.amazonaws.com"
    ]) == 1
    error_message = "CMK key policy must grant secretsmanager.amazonaws.com encrypt/decrypt — the DB master secret is encrypted with this key."
  }

  assert {
    condition = length([
      for s in jsondecode(aws_kms_key.main.policy).Statement :
      s if can(regex("^logs\\.[a-z0-9-]+\\.amazonaws\\.com$", try(s.Principal.Service, "")))
    ]) == 1
    error_message = "CMK key policy must grant the regional CloudWatch Logs service principal (logs.<region>.amazonaws.com) — encrypted log groups require this."
  }
}
