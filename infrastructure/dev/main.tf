locals {
  name_prefix = "forge-${var.environment}"
}

# Resolve the OIDC deploy role ARN so the KMS module can include it as an
# explicit KeyAdministrators principal. The role is created out-of-band
# (it's the trust root for CI auth — Terraform can't manage the role it
# itself runs as). See PR #19 for the OIDC cutover details.
data "aws_iam_role" "oidc_deploy" {
  name = var.oidc_deploy_role_name
}

module "network" {
  source = "../modules/network"

  name_prefix = local.name_prefix
}

module "alb" {
  source = "../modules/alb"

  name_prefix       = local.name_prefix
  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids
}

module "ecr" {
  source = "../modules/ecr"

  name_prefix = local.name_prefix
}

# Customer-managed KMS key — encrypts Aurora storage, the DB master secret,
# and the ECS log group. Day-to-day administration flows through the OIDC
# deploy role via the KeyAdministrators statement, not the safety-net root.
module "kms" {
  source = "../modules/kms"

  name_prefix                 = local.name_prefix
  environment                 = var.environment
  key_administrator_role_arns = [data.aws_iam_role.oidc_deploy.arn]
}

# Aurora Serverless v2 cluster + Secrets Manager master credentials.
# Depends on network outputs + the ecs_service app SG (for the DB ingress
# rule) + the KMS module's CMK ARN.
module "database" {
  source = "../modules/database"

  name_prefix           = local.name_prefix
  vpc_id                = module.network.vpc_id
  private_subnet_ids    = module.network.private_subnet_ids
  app_security_group_id = module.ecs_service.app_security_group_id
  kms_key_arn           = module.kms.key_arn
  production_safety     = var.production_safety
}

module "ecs_service" {
  source = "../modules/ecs_service"

  name_prefix           = local.name_prefix
  vpc_id                = module.network.vpc_id
  private_subnet_ids    = module.network.private_subnet_ids
  alb_security_group_id = module.alb.security_group_id
  target_group_arn      = module.alb.target_group_arn
  app_image             = var.app_image
  aws_region            = var.aws_region
  environment           = var.environment

  # Data layer wiring. The database module supplies endpoint + secret ARN;
  # the ecs_service module sets DATABASE_* env vars on the container so
  # the future app can connect using the same env-var contract it'll use
  # locally.
  kms_key_arn       = module.kms.key_arn
  master_secret_arn = module.database.master_secret_arn
  database_host     = module.database.endpoint
  database_port     = module.database.port
  database_name     = module.database.database_name
  database_user     = module.database.master_username
  database_ssl_mode = "require"
}
