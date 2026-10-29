"""In-memory stand-ins for SQLAlchemy engines, Kafka and the object store."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from typing import Any


class FakeResult:
    """A minimal SQLAlchemy result over fixed rows."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        """Hold the rows."""
        self._rows = rows or []

    def all(self) -> list[tuple[Any, ...]]:
        """All rows."""
        return self._rows

    def one(self) -> tuple[Any, ...]:
        """The first row."""
        return self._rows[0]

    def first(self) -> tuple[Any, ...] | None:
        """The first row, or None."""
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        """The first column of the first row, or None."""
        return self._rows[0][0] if self._rows else None

    def scalars(self) -> FakeScalars:
        """The first column of every row."""
        return FakeScalars([r[0] for r in self._rows])

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self._rows)


class FakeScalars:
    """The scalars() view of a FakeResult."""

    def __init__(self, values: list[Any]) -> None:
        """Hold the values."""
        self._values = values

    def all(self) -> list[Any]:
        """All values."""
        return self._values


Handler = Callable[[str, Any], list[tuple[Any, ...]] | None]


class FakeEngine:
    """Records every statement; `handler(sql, params)` may return rows for it."""

    def __init__(self, handler: Handler | None = None) -> None:
        """Start with no statements."""
        self.statements: list[tuple[str, Any]] = []
        self._handler = handler or (lambda _sql, _params: None)

    def execute(self, statement: Any, params: Any = None) -> FakeResult:
        """Record the statement and return the handler's rows."""
        sql = str(statement)
        self.statements.append((sql, params))
        return FakeResult(self._handler(sql, params))

    @contextmanager
    def begin(self) -> Generator[FakeEngine]:
        """A transaction context yielding this fake."""
        yield self

    @contextmanager
    def connect(self) -> Generator[FakeEngine]:
        """A connection context yielding this fake."""
        yield self

    def sql(self, fragment: str) -> list[tuple[str, Any]]:
        """Recorded statements containing `fragment`."""
        return [(s, p) for s, p in self.statements if fragment in s]


class FakeEvents:
    """Collects stage events instead of publishing them."""

    def __init__(self) -> None:
        """Start empty."""
        self.records: list[tuple[Any, ...]] = []
        self.batches: list[tuple[Any, ...]] = []

    def record(self, org_id: str, slug: str, stage: str, status: str, **kwargs: Any) -> None:
        """Collect a record event."""
        self.records.append((org_id, slug, stage, status, kwargs.get("record_id")))

    def batch(self, org_id: str, slug: str, stage: str, status: str, count: int, detail: Any = None) -> None:
        """Collect a batch event."""
        self.batches.append((org_id, slug, stage, status, count))

    def flush(self) -> None:
        """Nothing to flush."""
        pass


class FakeStore:
    """An in-memory tenant-scoped object store."""

    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        """Start with the given objects."""
        self.objects = dict(objects)

    def tenants(self) -> list[str]:
        """The orgs that have objects."""
        return sorted({org for org, _ in self.objects})

    def list(self, org: str, prefix: str = "") -> list[str]:
        """Keys of one org under a prefix."""
        return sorted(k for o, k in self.objects if o == org and k.startswith(prefix))

    def get(self, org: str, key: str) -> bytes:
        """One object's bytes."""
        return self.objects[org, key]

    def exists(self, org: str, key: str) -> bool:
        """Whether an object exists."""
        return (org, key) in self.objects

    def put(self, org: str, key: str, data: bytes) -> str:
        """Store an object."""
        self.objects[org, key] = data
        return key

    def delete(self, org: str, key: str) -> None:
        """Remove an object."""
        self.objects.pop((org, key), None)


class FakeMessage:
    """A Kafka message stand-in."""

    def __init__(self, topic: str, value: Any, *, error: Any = None) -> None:
        """Encode the value as JSON unless it is raw bytes."""
        self._topic = topic
        self._value = value if isinstance(value, bytes) else json.dumps(value).encode()
        self._error = error

    def topic(self) -> str:
        """The topic."""
        return self._topic

    def value(self) -> bytes:
        """The raw value."""
        return self._value

    def error(self) -> Any:
        """The error, if any."""
        return self._error
