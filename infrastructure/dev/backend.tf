# Remote state backend — provisioned by infrastructure/bootstrap/.
#
# The S3 bucket is NOT managed by this stack; it was created once, manually,
# via the bootstrap. This stack only reads/writes state through it.
#
# Locking strategy: `use_lockfile = true` enables S3-native state locking
# (Terraform 1.10+, AWS provider v5+). A `.tflock` object is created next to
# the state file using S3's conditional-write semantics. This replaces the
# legacy DynamoDB-based locking (`dynamodb_table = ...`), which is now
# deprecated. The bootstrap's forge-tfstate-lock DynamoDB table is left in
# place for now but unused; it can be deleted in a follow-up PR.
#
# The values below are intentionally hardcoded rather than parameterized — a
# `backend` block cannot reference variables (Terraform restriction), so any
# environment that needs a different bucket would need its own backend.tf.

terraform {
  backend "s3" {
    bucket       = "forge-tfstate-328926346833"
    key          = "envs/dev/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
