# SPEC §6.1: provider-scoped Terraform body for managed_database/v1 on AWS.
#
# E.2 stub: this file is materialized into per-request workspaces by the
# worker but never invoked yet — `terraform init/plan/apply` arrives in
# E.3. The data-source tag schema below (forge-${var.environment}-vpc,
# Tier=private) is a placeholder; E.3 will confirm the real tags written
# by the network module and tighten this lookup.

variable "environment" {
  description = "Deployment environment (e.g. dev, prod). Used to find the matching shared VPC."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region for provider. Set per-workspace via terraform.tfvars.json when E.3 lands."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev-rr-<id>). Set per-workspace in E.3."
  type        = string
  default     = "forge-managed-db"
}

provider "aws" {
  region = var.aws_region
}

data "aws_vpc" "main" {
  tags = {
    Name = "forge-${var.environment}-vpc"
  }
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }

  tags = {
    Tier = "private"
  }
}

data "aws_security_group" "app" {
  vpc_id = data.aws_vpc.main.id

  tags = {
    Role = "app"
  }
}

data "aws_kms_key" "managed_resources" {
  # Account-level alias created by infrastructure/bootstrap. Single shared
  # CMK across environments — the bootstrap stack deliberately doesn't know
  # about per-env scope. If we ever need env-scoped keys, the bootstrap
  # stack grows a key-per-env loop and this lookup gains the `${var.environment}`
  # back into the alias string.
  key_id = "alias/forge-shared-managed-resources"
}

module "database" {
  source = "../../../../infrastructure/modules/database"

  name_prefix           = var.name_prefix
  vpc_id                = data.aws_vpc.main.id
  private_subnet_ids    = data.aws_subnets.private.ids
  app_security_group_id = data.aws_security_group.app.id
  kms_key_arn           = data.aws_kms_key.managed_resources.arn

  # db_engine / db_size / db_storage_gb are declared in ../variables.tf and
  # populated from terraform.tfvars.json at render time. E.3 will translate
  # them into the underlying module's engine_version / capacity / storage
  # inputs. For E.2 we just need terraform-valid syntax that consumes them.
}
