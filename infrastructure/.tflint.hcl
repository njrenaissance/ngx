plugin "aws" {
  enabled = true
  version = "0.38.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

config {
  # Scan all child modules when running with --recursive
  call_module_type = "all"
}
