locals {
  name_prefix = "forge-${var.environment}"
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
}
