"""Tests for ai_circus_shared.db."""

from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy import make_url

from ai_circus_shared.db import database_url


class _Config:
    POSTGRES_HOST = "postgres"
    POSTGRES_PORT = "5432"
    POSTGRES_DB = "platform"
    POSTGRES_USER = "ai_circus"
    POSTGRES_PASSWORD = SecretStr("p@ss/w:rd%")


def test_database_url_escapes_special_characters_in_the_password() -> None:
    """An f-string DSN would parse `p@ss/w:rd%` as a different host/database — the URL
    must round-trip to exactly the configured parts."""
    url = make_url(database_url(_Config()).render_as_string(hide_password=False))
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "ai_circus",
        "p@ss/w:rd%",
        "postgres",
        5432,
        "platform",
    )
    assert url.drivername == "postgresql+psycopg"
