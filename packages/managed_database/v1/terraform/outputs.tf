# SPEC §6.4 rule 6: every resource package must declare these three outputs.
# The runner reads them after `terraform apply` succeeds and persists the
# encrypted blob to DEPLOYMENT.outputs_encrypted.
#
# E.2 stub: values are null placeholders so the top-level module parses /
# validates standalone. E.3 will introduce a top-level main.tf that wraps
# the provider-scoped module (./aws) and wires real outputs through here.

output "connection_host" {
  description = "Hostname clients use to connect to the database."
  value       = null
}

output "port" {
  description = "TCP port for client connections."
  value       = null
}

output "secret_ref" {
  description = "ARN of the Secrets Manager secret holding master credentials."
  value       = null
}
