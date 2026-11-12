"""
customers.py
------------

The example API's data: a small, fixed, fictional customer list for one tenant (`lite-demo`), and the
rules that decide what a caller sees. Personal fields never leave masked (rule R9, docs/adr/0022):
the API answers with a display name and a masked e-mail, so nothing personal reaches a model.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

from dataclasses import dataclass

STORES = [f"S{n:03d}" for n in range(1, 25)]
_FIRST = ["Ana", "Luis", "Marta", "Pablo", "Lucia", "Hugo", "Elena", "Diego", "Sara", "Ivan", "Noa", "Teo"]
_LAST = [
    "Garcia",
    "Lopez",
    "Martin",
    "Sanchez",
    "Perez",
    "Gomez",
    "Ruiz",
    "Diaz",
    "Moreno",
    "Alvarez",
    "Romero",
    "Navarro",
]
_CITIES = ["Tarifa", "Cadiz", "Malaga", "Valencia", "Barcelona", "San Sebastian", "A Coruna", "Lisbon", "Biarritz"]
_TIERS = ["bronze", "silver", "gold"]


@dataclass(frozen=True)
class Customer:
    """One customer as stored; `email` is personal and is only ever returned masked."""

    customer_id: str
    first_name: str
    last_name: str
    email: str
    city: str
    home_store: str
    tier: str
    lifetime_value_eur: float
    last_purchase: str


def _build() -> tuple[Customer, ...]:
    rows = []
    # One customer per store, then three more for S012 so a store manager sees a handful.
    homes = [*STORES, "S012", "S012", "S012"]
    for i, store in enumerate(homes):
        first, last = _FIRST[i % len(_FIRST)], _LAST[(i * 5) % len(_LAST)]
        rows.append(
            Customer(
                customer_id=f"C{i + 1:03d}",
                first_name=first,
                last_name=last,
                email=f"{first.lower()}.{last.lower()}{i + 1}@example.com",
                city=_CITIES[i % len(_CITIES)],
                home_store=store,
                tier=_TIERS[(i * 7) % 3],
                lifetime_value_eur=round(180 + (i * 137.5) % 4200, 2),
                last_purchase=f"2026-{(i % 10) + 1:02d}-{(i * 3) % 27 + 1:02d}",
            )
        )
    return tuple(rows)


# Tenant (organization alias) → its customers. Any other tenant has none.
DATA: dict[str, tuple[Customer, ...]] = {"lite-demo": _build()}


def mask_email(email: str) -> str:
    """`ana.garcia1@example.com` → `a•••@example.com`."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain}"


def public_view(customer: Customer) -> dict[str, object]:
    """What leaves the API: no full name, no readable e-mail."""
    return {
        "customer_id": customer.customer_id,
        "display_name": f"{customer.first_name} {customer.last_name[:1]}.",
        "email": mask_email(customer.email),
        "city": customer.city,
        "home_store": customer.home_store,
        "tier": customer.tier,
        "lifetime_value_eur": customer.lifetime_value_eur,
        "last_purchase": customer.last_purchase,
    }


def visible_to(tenant: str, stores: tuple[str, ...] | None) -> tuple[Customer, ...]:
    """The tenant's customers, limited to `stores` when the role is store-filtered (None = every store)."""
    rows = DATA.get(tenant, ())
    return rows if stores is None else tuple(c for c in rows if c.home_store in stores)


def search(rows: tuple[Customer, ...], query: str, sort: str, limit: int) -> list[Customer]:
    """Case-insensitive match on name, city, store or tier; sorted by name or lifetime value."""
    q = query.strip().lower()
    found = [
        c
        for c in rows
        if not q or any(q in field.lower() for field in (c.first_name, c.last_name, c.city, c.home_store, c.tier))
    ]
    if sort == "lifetime_value":
        found.sort(key=lambda c: c.lifetime_value_eur, reverse=True)
    else:
        found.sort(key=lambda c: (c.last_name, c.first_name))
    return found[:limit]
