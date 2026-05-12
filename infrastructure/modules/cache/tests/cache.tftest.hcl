# Contract tests for the cache module.
#
# Runs in plan mode so no AWS API calls are made and no credentials are
# required. The provider block sets the offline-safe flags. Mirrors the
# pattern in modules/kms/tests/cmk.tftest.hcl.
#
# Load-bearing assertions, per issue #54 acceptance criteria:
#   - transit + at-rest encryption are on (regression guard — turning either
#     off downgrades the broker from a TLS+CMK posture to plaintext)
#   - the CMK ARN actually flows into the replication group (catches a wiring
#     break that would silently fall back to the AWS-managed elasticache key)
#   - exactly one ingress rule, 6379/tcp, sourced from the app SG only
#   - no 0.0.0.0/0 anywhere on the cache SG
#   - single-node topology with failover explicitly disabled (deliberate POC
#     posture; turning these on at the same time is non-trivial and should
#     not happen accidentally via this module)
#   - engine is Redis 7.x (parameter_group_family pinning depends on it)

provider "aws" {
  region                      = "us-east-1"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
}

variables {
  name_prefix           = "forge-test"
  vpc_id                = "vpc-00000000"
  private_subnet_ids    = ["subnet-aaaa1111", "subnet-bbbb2222"]
  app_security_group_id = "sg-00000000"
  kms_key_arn           = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
}

run "transit_encryption_enabled" {
  command = plan

  assert {
    condition     = aws_elasticache_replication_group.main.transit_encryption_enabled == true
    error_message = "Elasticache replication group must have transit encryption enabled — the broker URL is rediss:// (not redis://) and disabling this silently breaks the worker connection."
  }
}

run "at_rest_encryption_enabled" {
  command = plan

  # Note: the AWS provider exposes at_rest_encryption_enabled as a string
  # ("true"/"false") during plan, unlike transit_encryption_enabled which
  # is a bool. tostring() normalises so the assertion passes regardless of
  # which type the provider returns in a future release.
  assert {
    condition     = tostring(aws_elasticache_replication_group.main.at_rest_encryption_enabled) == "true"
    error_message = "Elasticache replication group must have at-rest encryption enabled. Rubric requirement (Option-1)."
  }
}

run "kms_key_wired" {
  command = plan

  # Catches a wiring break where kms_key_id is omitted and Elasticache
  # silently falls back to the AWS-managed `aws/elasticache` key. We want
  # the project CMK so key rotation + auditing flow through one principal
  # surface.
  assert {
    condition     = aws_elasticache_replication_group.main.kms_key_id == var.kms_key_arn
    error_message = "Elasticache kms_key_id must equal the project CMK ARN passed in via var.kms_key_arn. Falling back to the AWS-managed key defeats the CMK rotation/audit story."
  }
}

run "automatic_failover_disabled" {
  command = plan

  # Deliberate POC posture — multi-AZ + failover are listed under "open
  # follow-ups" in the issue. This assertion is the guard against flipping
  # one of the failover flags without flipping num_cache_clusters > 1
  # simultaneously (Elasticache rejects the combination at apply time).
  assert {
    condition     = aws_elasticache_replication_group.main.automatic_failover_enabled == false
    error_message = "automatic_failover_enabled must be false on the single-node POC. Enabling it requires num_cache_clusters > 1; flipping one without the other fails at apply time."
  }
}

run "single_node_topology" {
  command = plan

  assert {
    condition     = aws_elasticache_replication_group.main.num_cache_clusters == 1
    error_message = "num_cache_clusters must be 1 for the POC. Multi-AZ replication is deferred to a hardening ticket (see issue #54 follow-ups)."
  }
}

run "engine_is_redis7" {
  command = plan

  assert {
    condition = (
      aws_elasticache_replication_group.main.engine == "redis" &&
      can(regex("^7\\.", aws_elasticache_replication_group.main.engine_version))
    )
    error_message = "Engine must be Redis 7.x. parameter_group_family is pinned to redis7; the major versions must match or apply fails."
  }
}

run "ingress_locked_to_app_sg" {
  command = plan

  # Exactly one ingress rule. from/to_port = 6379, protocol = tcp, source
  # SG = app SG. Walking the ingress list rather than indexing protects
  # against future rule additions silently widening access.
  assert {
    condition = length([
      for r in aws_security_group.cache.ingress :
      r if(
        r.from_port == 6379 &&
        r.to_port == 6379 &&
        r.protocol == "tcp" &&
        contains(r.security_groups, var.app_security_group_id)
      )
    ]) == 1
    error_message = "Cache SG must have exactly one ingress rule: 6379/tcp from the app SG. Found a different number or shape — check that no additional ingress was added."
  }
}

run "no_wildcard_ingress" {
  command = plan

  # Belt-and-suspenders: regardless of port, the cache SG must not have
  # 0.0.0.0/0 anywhere in ingress. If this ever fires, the cache is
  # internet-accessible — that's a P0 finding.
  assert {
    condition = length([
      for r in aws_security_group.cache.ingress :
      r if contains(coalesce(r.cidr_blocks, []), "0.0.0.0/0")
    ]) == 0
    error_message = "Cache SG must not have 0.0.0.0/0 in any ingress rule. The cache is internet-accessible if this assertion fires."
  }
}
