"""Task registry — maps user-facing task names to Celery task names.

Only tasks listed here can be submitted via the API. Add new entries as
tasks are implemented (workspace materialization, terraform apply, destroy).
"""

TASK_REGISTRY: dict[str, str] = {
    "ping": "forge.ping",
    "provision_resource": "forge.provision_resource",
}
