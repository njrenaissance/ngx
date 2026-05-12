# State-address relocations for the module refactor in PR #13.
#
# Each `moved` block tells Terraform that a resource previously living at the
# root level now lives inside a module. At plan time these are reported as
# "Resource has moved" rather than "to be destroyed/created", and at apply
# time the state file is rewritten in place. No AWS API calls are made.
#
# These blocks are cheap to leave in place indefinitely; per Hashicorp's
# guidance they may also be removed in a follow-up cleanup PR once apply
# completes successfully on main.

# ─── network module ────────────────────────────────────────────────────────────

moved {
  from = aws_vpc.main
  to   = module.network.aws_vpc.main
}

moved {
  from = aws_internet_gateway.main
  to   = module.network.aws_internet_gateway.main
}

moved {
  from = aws_subnet.public
  to   = module.network.aws_subnet.public
}

moved {
  from = aws_subnet.private
  to   = module.network.aws_subnet.private
}

moved {
  from = aws_eip.nat
  to   = module.network.aws_eip.nat
}

moved {
  from = aws_nat_gateway.main
  to   = module.network.aws_nat_gateway.main
}

moved {
  from = aws_route_table.public
  to   = module.network.aws_route_table.public
}

moved {
  from = aws_route_table.private
  to   = module.network.aws_route_table.private
}

moved {
  from = aws_route_table_association.public
  to   = module.network.aws_route_table_association.public
}

moved {
  from = aws_route_table_association.private
  to   = module.network.aws_route_table_association.private
}

# ─── alb module ────────────────────────────────────────────────────────────────

moved {
  from = aws_security_group.alb
  to   = module.alb.aws_security_group.alb
}

moved {
  from = aws_lb.main
  to   = module.alb.aws_lb.main
}

moved {
  from = aws_lb_target_group.app
  to   = module.alb.aws_lb_target_group.app
}

moved {
  from = aws_lb_listener.http
  to   = module.alb.aws_lb_listener.http
}

# ─── ecr module ────────────────────────────────────────────────────────────────

moved {
  from = aws_ecr_repository.forge
  to   = module.ecr.aws_ecr_repository.forge
}

moved {
  from = aws_ecr_lifecycle_policy.forge
  to   = module.ecr.aws_ecr_lifecycle_policy.forge
}

# ─── ecs_service module ────────────────────────────────────────────────────────

# aws_security_group.app moved twice in this repo's history:
#   1. Root-level → module.ecs_service (PR #13 initial module refactor)
#   2. module.ecs_service → module.network (PR #54, this PR — cache↔ecs_service
#      cycle break; see ADR-010)
#
# Terraform requires each destination to have only one `from`, so we chain
# the moves: any state file at the root address gets relayed through the
# ecs_service address before landing at the final network address. The
# intermediate hop is a no-op for state files that already passed it.
moved {
  from = aws_security_group.app
  to   = module.ecs_service.aws_security_group.app
}

moved {
  from = module.ecs_service.aws_security_group.app
  to   = module.network.aws_security_group.app
}

moved {
  from = aws_cloudwatch_log_group.app
  to   = module.ecs_service.aws_cloudwatch_log_group.app
}

moved {
  from = aws_iam_role.ecs_execution
  to   = module.ecs_service.aws_iam_role.ecs_execution
}

moved {
  from = aws_iam_role_policy_attachment.ecs_execution
  to   = module.ecs_service.aws_iam_role_policy_attachment.ecs_execution
}

moved {
  from = aws_ecs_cluster.main
  to   = module.ecs_service.aws_ecs_cluster.main
}

moved {
  from = aws_ecs_task_definition.app
  to   = module.ecs_service.aws_ecs_task_definition.app
}

moved {
  from = aws_ecs_service.app
  to   = module.ecs_service.aws_ecs_service.app
}
