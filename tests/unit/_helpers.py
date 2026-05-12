"""Shared test helpers for the unit suite.

Lives outside conftest.py so pytest doesn't try to auto-load it as a plugin.
"""

from __future__ import annotations

from typing import Any


def assert_problem_details(resp: Any, status: int, type_suffix: str) -> dict:
    """Assert a response is a valid RFC 7807 Problem Details body.

    Returns the parsed body so callers can chain further assertions on
    ``detail``, ``errors``, etc.
    """
    assert resp.status_code == status
    assert resp.headers.get("content-type") == "application/problem+json"
    body = resp.json()
    assert body["type"] == f"urn:forge:error:{type_suffix}"
    assert body["status"] == status
    assert "title" in body
    assert "detail" in body
    assert "instance" in body
    return body
