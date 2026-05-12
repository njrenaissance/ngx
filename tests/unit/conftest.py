"""Shared fixtures for unit tests.

Unit tests run without a real Redis broker. POST /v1/resources now goes
through TaskBroker.submit() — without this autouse fixture every unit
test that exercises the create endpoint would try to publish to redis
and fail. The integration suite uses the real broker.
"""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _stub_task_broker_submit(request: pytest.FixtureRequest) -> Iterator[MagicMock | None]:
    """Replace TaskBroker.submit with a no-op MagicMock for all unit tests.

    Tests that need to exercise the real submit method (e.g. broker-internals
    tests) can opt out with `@pytest.mark.no_broker_stub`.
    """
    if request.node.get_closest_marker("no_broker_stub"):
        yield None
        return
    with patch("forge.workers.broker.TaskBroker.submit") as mock_submit:
        mock_submit.return_value = "stub-task-id"
        yield mock_submit
