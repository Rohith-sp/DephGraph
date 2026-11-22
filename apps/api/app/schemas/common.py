from typing import Annotated, Any, Generic, TypeVar

from fastapi import Path, Query
from pydantic import BaseModel, Field

T = TypeVar("T")


# Ids reach BIGINT/INT columns: an unbounded int overflows asyncpg and surfaces as a 500, so every
# id parameter is bounded (-> 422). BigId = BIGINT path id, BigBodyId = the same inside a body.
BigId = Annotated[int, Path(ge=1, le=2**63 - 1)]
SmallId = Annotated[int, Path(ge=1, le=2**31 - 1)]
BigBodyId = Annotated[int, Field(ge=1, le=2**63 - 1)]


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def ok(data: Any) -> dict[str, Any]:
    return {"data": data, "error": None}


class Pagination:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> None:
        self.page, self.page_size = page, page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
