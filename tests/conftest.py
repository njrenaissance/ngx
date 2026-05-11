from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forge.main import get_app


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark tests based on their parent directory.

    Tests under `tests/unit/` get `@pytest.mark.unit`; tests under
    `tests/integration/` get `@pytest.mark.integration`. This removes the need
    to decorate every individual test and ensures no test is accidentally
    unmarked.
    """
    for item in items:
        rel_path = Path(item.fspath).resolve()
        parts = rel_path.parts
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """FastAPI TestClient bound to a fresh app instance per test."""
    with TestClient(get_app()) as test_client:
        yield test_client
