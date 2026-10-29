"""In-memory stand-ins for the warehouse."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from data_api.core.warehouse import Query


class FakeWarehouse:
    """Records every query; `handler(sql, params)` returns the rows."""

    def __init__(
        self,
        handler: Callable[[str, dict[str, Any]], list[dict[str, Any]]] | None = None,
        columns: list[str] | None = None,
        marts_engine: str = "postgres",
    ) -> None:
        """Script the rows and columns every query sees."""
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self._handler = handler or (lambda _sql, _params: [])
        self._columns = (
            columns
            if columns is not None
            else ["org_id", "day", "store_id", "store_name", "region", "channel", "revenue_cents", "receipts"]
        )
        self.marts_engine = marts_engine
        self.engine = None

    def dialect_for(self, layer: str) -> str:
        """Same split as the real warehouse."""
        return self.marts_engine if layer in ("silver", "gold") else "postgres"

    def query(self, layer: str) -> Query:
        """A new Query in the layer's dialect."""
        return Query(self.dialect_for(layer))

    def rows(self, query: Query, sql: str, *, timeout_ms: int = 15_000) -> list[dict[str, Any]]:
        """Record the query and return the handler's rows."""
        self.queries.append((sql, dict(query.params)))
        return self._handler(sql, query.params)

    def relation(self, product: Any, layer: str, name: str) -> tuple[str, str]:
        """Same naming as the real warehouse."""
        return {"silver": f"{product.slug}_silver", "gold": f"{product.slug}_gold"}.get(layer, product.slug), name

    def columns(self, product: Any, layer: str, name: str) -> list[str]:
        """The scripted columns."""
        return self._columns

    def last_transform(self, product: Any) -> dict[str, Any] | None:
        """No transform has run."""
        return None
