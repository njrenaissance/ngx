from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    limit: int
    total: int


def page_params(
    page: int = Query(1, ge=1, description="1-based page index"),
    limit: int = Query(50, ge=1, le=200, description="Items per page (max 200)"),
) -> tuple[int, int]:
    return page, limit
