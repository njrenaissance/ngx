"""Celery task modules.

autodiscover_tasks(["forge.workers"]) imports this package. Since
individual task modules live in separate files, they must be imported
here so their `@shared_task` decorators run and register with Celery.
"""

from forge.workers.tasks.ping import ping  # noqa: F401
from forge.workers.tasks.provision_resource import provision_resource  # noqa: F401
