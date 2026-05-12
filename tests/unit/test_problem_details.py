"""Tests for the RFC 7807 exception handlers."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge.api.problem_details import ProblemDetailsException, register_exception_handlers
from tests.unit._helpers import assert_problem_details

pytestmark = pytest.mark.unit


def _app_with_route(handler) -> TestClient:
    app = FastAPI()
    app.add_api_route("/boom", handler, methods=["GET"])
    register_exception_handlers(app)
    # TestClient defaults to re-raising server exceptions; disable so the
    # registered handler can produce a response we can assert against.
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_emits_problem_json_500() -> None:
    def route() -> None:
        raise RuntimeError("internal boom — should not leak to client")

    resp = _app_with_route(route).get("/boom")
    body = assert_problem_details(resp, 500, "internal-server-error")
    # Detail must NOT leak the exception message.
    assert "boom" not in body["detail"]
    assert body["detail"] == "An internal error occurred"


def test_problem_details_exception_takes_precedence_over_fallback() -> None:
    """ProblemDetailsException handler must win over the generic Exception handler."""

    def route() -> None:
        raise ProblemDetailsException(
            status=418,
            type="urn:forge:error:teapot",
            title="I'm a teapot",
            detail="short and stout",
        )

    resp = _app_with_route(route).get("/boom")
    assert_problem_details(resp, 418, "teapot")
