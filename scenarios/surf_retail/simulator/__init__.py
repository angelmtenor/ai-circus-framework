"""Angel's Surf Co. simulator — the surf_retail data product's source systems.

A plugin of the generic data tier (see ai_circus_shared.data_simulator and docs/adr/0013):
modern-store tills and the web shop publish to Kafka, the Greek franchise's legacy tills drop
daily files with a control file into the landing bucket, and the ERP (stores, products,
promotions, loyalty customers) is a set of tables in the operational source database that CDC
captures. Prices and promotions are always read back from the ERP, so an ERP price change is
what the next receipt charges.

History: up to five years. The months before the last `STREAM_DAYS` arrive as the old POS
and web-shop exports (monthly migration files, reconciled like any delivery); the recent days
come the way they do today — streamed, or the legacy stores' daily files. Receipt and order
numbers are derived from the day, so a re-run of the backfill re-sends the same keys.

Storytelling built into the data: SUP sells in summer, kites in spring and autumn, Black
Friday spikes boards, the chain opens France in 2022, Italy in 2023, buys the Greek franchise
in 2024 and opens Cyprus and Malta in 2025, the web shop grows every year, AUTUMN-SWELL lifts
fish and hybrids right now — and Livorno (S015) has been losing traffic for two weeks.
"""

from __future__ import annotations

import bisect
import math
import random
import re
import zlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import catalogue
from .catalogue import ONLINE_VAT, VAT, Product, Promotion, Store

STREAM_DAYS = 30  # the most recent days arrive as they do live (Kafka / daily files)
STORE_WEEKDAY = [0.8, 0.8, 0.85, 0.9, 1.1, 1.55, 1.0]
ONLINE_WEEKDAY = [1.25, 1.1, 1.0, 1.0, 0.95, 0.85, 1.15]
# Beach-town traffic: quiet winters, a long summer.
TRAFFIC_MONTH = [0.55, 0.6, 0.8, 0.95, 1.1, 1.35, 1.6, 1.55, 1.1, 0.85, 0.9, 0.85]
# What riders buy across the year (Mediterranean): SUP in summer, kites spring to autumn.
GROUP_MONTH = {
    "surf": [1.1, 1.0, 1.1, 1.1, 1.0, 0.9, 0.85, 0.85, 1.0, 1.15, 1.1, 1.05],
    "sup": [0.3, 0.3, 0.5, 0.8, 1.2, 1.7, 2.0, 1.9, 1.1, 0.6, 0.4, 0.4],
    "kite": [0.5, 0.6, 1.0, 1.4, 1.4, 1.3, 1.3, 1.2, 1.2, 1.0, 0.6, 0.5],
}
CATEGORY_MIX = {
    "surf": {"SHT": 0.26, "FSH": 0.18, "HYB": 0.20, "MID": 0.12, "LNG": 0.08, "SFT": 0.16},
    "beginner": {"SFT": 0.45, "HYB": 0.32, "MID": 0.15, "LNG": 0.08},
    "sup": {"SUP": 0.55, "ISP": 0.45},
    "kite": {"KTE": 0.45, "TWT": 0.25, "KSB": 0.07, "BAR": 0.10, "HRN": 0.13},
}
SEGMENT_GROUP = {"beginner": "surf", "surfer": "surf", "sup": "sup", "kiter": "kite"}
ACCESSORY_ONLY = {"WAX": 0.32, "LSH": 0.16, "FIN": 0.14, "PAD": 0.08, "BAG": 0.07, "RCK": 0.06, "PDL": 0.04,
                  "PMP": 0.03, "HRN": 0.03, "BAR": 0.02}
# What goes in the basket with a board: (accessory prefix, probability, model names that fit).
ATTACH = {
    "SHT": [("LSH", 0.7, ("Comp Leash", "Regular Leash")), ("FIN", 0.55, ("Thruster Set FCS", "Thruster Set Futures",
                                                                             "Quad Set")),
            ("PAD", 0.45, None), ("WAX", 0.6, None), ("BAG", 0.35, ("Day Bag", "Travel Coffin"))],
    "FSH": [("LSH", 0.7, ("Comp Leash", "Regular Leash")), ("FIN", 0.6, ("Twin Keel", "Quad Set")),
            ("PAD", 0.4, None), ("WAX", 0.6, None), ("BAG", 0.3, ("Day Bag",))],
    "HYB": [("LSH", 0.7, ("Regular Leash",)), ("FIN", 0.45, ("Thruster Set FCS", "Quad Set")), ("PAD", 0.3, None),
            ("WAX", 0.55, None), ("BAG", 0.3, ("Day Bag",))],
    "MID": [("LSH", 0.65, ("Regular Leash", "Knee Leash")), ("FIN", 0.45, ("Single Fin",)), ("WAX", 0.55, None),
            ("BAG", 0.3, ("Day Bag", "Travel Coffin"))],
    "LNG": [("LSH", 0.6, ("Knee Leash",)), ("FIN", 0.5, ("Single Fin",)), ("WAX", 0.6, None),
            ("BAG", 0.35, ("Day Bag",))],
    "SFT": [("LSH", 0.3, ("Regular Leash",)), ("WAX", 0.4, None)],
    "KSB": [("PAD", 0.5, None), ("FIN", 0.4, ("Thruster Set FCS",)), ("WAX", 0.3, None)],
    "SUP": [("PDL", 0.7, None), ("LSH", 0.5, ("SUP Coiled Leash",)), ("FIN", 0.15, ("SUP Centre Fin",)),
            ("BAG", 0.2, ("SUP Bag",)), ("RCK", 0.3, None)],
    "ISP": [("PMP", 0.2, ("Electric Pump",)), ("LSH", 0.3, ("SUP Coiled Leash",)),
            ("PDL", 0.15, ("Carbon Adjustable Paddle",))],
    "KTE": [("BAR", 0.45, None), ("HRN", 0.3, None), ("TWT", 0.15, None)],
    "TWT": [("HRN", 0.2, None), ("KTE", 0.1, None)],
}
ONLINE_ORDERS_PER_DAY = 140  # in 2026; the web shop grows ~25% a year
LOYALTY_RATE = {"store": 0.45, "online": 0.6}
RETURN_REASONS = ["damaged", "wrong_size", "not_as_described", "changed_mind", "late_delivery"]
UNDERPERFORMER = "S015"  # loses traffic over the last 14 days — something for the assistant to find
LIVE_STATUS_DELAYS = [20, 60, 120, 240]  # seconds between an order's live status updates
LIVE_ORDER_BASE = 9_000_000  # live/web-shop order numbers; history numbers are day-of-year based
MAX_CART_LINES = 20


def _stable_rng(*parts: object) -> random.Random:
    return random.Random(zlib.crc32("|".join(str(p) for p in parts).encode()))


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 40:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="milliseconds")


STORE_HOURS = {9: 0.3, 10: 0.8, 11: 1.0, 12: 1.2, 13: 1.1, 14: 0.7, 15: 0.6, 16: 0.9, 17: 1.3, 18: 1.5, 19: 1.4,
               20: 1.0}
ONLINE_HOURS = {h: w for h, w in enumerate([0.3, 0.2, 0.1, 0.1, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0, 1.0, 1.1, 1.1,
                                            1.0, 1.0, 1.0, 1.1, 1.2, 1.4, 1.7, 1.8, 1.4, 0.8])}


def _hour_sample(rng: random.Random, day: date, curve: dict[int, float]) -> datetime:
    hours, weights = zip(*curve.items(), strict=True)
    hour = rng.choices(hours, weights)[0]
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC) + timedelta(seconds=rng.uniform(0, 3599))


def _receipt_seq(day: date, i: int) -> int:
    # Receipts numbered per business day (day ordinal * 1000 + n): a re-sent day carries the
    # same keys. Live tills use their own sequence (small numbers) — no overlap.
    return (day - date(2000, 1, 1)).days * 1000 + i


def _growth(day: date, per_year: float) -> float:
    return per_year ** ((day - date(2026, 1, 1)).days / 365.25)


class Catalogue:
    """The ERP's products and promotions, indexed for fast basket building."""

    def __init__(self, products: list[Product], promotions: list[Promotion]) -> None:
        self.products = products
        self.by_sku = {p.sku: p for p in products}
        self.by_prefix: dict[str, list[Product]] = {}
        for product in products:
            self.by_prefix.setdefault(product.prefix, []).append(product)
        self.promotions = promotions
        self._day: date | None = None
        self._active: dict[str, list[Product]] = {}

    def active(self, prefix: str, day: date) -> list[Product]:
        if self._day != day:
            self._day = day
            self._active = {}
        if prefix not in self._active:
            self._active[prefix] = [
                p for p in self.by_prefix.get(prefix, [])
                if (p.launched is None or p.launched <= day) and (p.retired is None or day <= p.retired)
            ]
        return self._active[prefix]

    def promos(self, day: date, channel: str) -> dict[str, Promotion]:
        return {sku: p for p in self.promotions if p.start <= day <= p.end and p.channel in ("all", channel)
                for sku in p.skus}


class SurfSimulator:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.stores = catalogue.build_stores(seed)
        self.store_by_id = {s.store_id: s for s in self.stores}
        self._customers: list[dict[str, Any]] | None = None

    # ── ERP ────────────────────────────────────────────────────────────────────────
    def setup(self, ctx: Any) -> None:
        schema, org = f'"{ctx.slug}"', ctx.org_id
        today = datetime.now(UTC).date()
        ddl = [
            f"CREATE SCHEMA IF NOT EXISTS {schema}",
            f"""CREATE TABLE IF NOT EXISTS {schema}.stores (org_id text, store_id text, name text, region text,
                city text, country text, spot text, format text, size text, registers int, opened date,
                updated_at timestamptz DEFAULT now(), PRIMARY KEY (org_id, store_id))""",
            f"""CREATE TABLE IF NOT EXISTS {schema}.products (org_id text, sku text, name text, category text,
                rider_group text, brand text, model text, size_label text, length_in int, volume_l numeric,
                construction text, shape text, colour text, launched date, retired date, price_cents int,
                cost_cents int, updated_at timestamptz DEFAULT now(), PRIMARY KEY (org_id, sku))""",
            f"""CREATE TABLE IF NOT EXISTS {schema}.promotions (org_id text, promo_id text, name text, pct int,
                skus text, channel text, start_date date, end_date date, updated_at timestamptz DEFAULT now(),
                PRIMARY KEY (org_id, promo_id))""",
            f"""CREATE TABLE IF NOT EXISTS {schema}.customers (org_id text, loyalty_id text, full_name text,
                email text, home_store_id text, signup_date date, updated_at timestamptz DEFAULT now(),
                PRIMARY KEY (org_id, loyalty_id))""",
            f"""CREATE TABLE IF NOT EXISTS {schema}.pos_sequences (org_id text, store_id text, last_seq bigint,
                PRIMARY KEY (org_id, store_id))""",
            f"CREATE TABLE IF NOT EXISTS {schema}.web_sequences (org_id text PRIMARY KEY, last_seq bigint)",
        ]
        for statement in ddl:
            ctx.ops.execute(statement)
        products = catalogue.build_products(self.seed)
        ctx.ops.execute(
            f"""INSERT INTO {schema}.stores VALUES (:org_id, :store_id, :name, :region, :city, :country, :spot,
                :format, :size, :registers, :opened, now()) ON CONFLICT DO NOTHING""",
            [{"org_id": org, **s.__dict__} for s in self.stores],
        )
        ctx.ops.execute(
            f"""INSERT INTO {schema}.products VALUES (:org_id, :sku, :name, :category, :group, :brand, :model,
                :size_label, :length_in, :volume_l, :construction, :shape, :colour, :launched, :retired,
                :price_cents, :cost_cents, now()) ON CONFLICT DO NOTHING""",
            [{"org_id": org, **{k: v for k, v in p.__dict__.items() if k != "prefix"}} for p in products],
        )
        first = today - timedelta(days=5 * 366 + 31)
        ctx.ops.execute(
            f"""INSERT INTO {schema}.promotions VALUES (:org_id, :promo_id, :name, :pct, :skus, :channel, :start,
                :end, now()) ON CONFLICT DO NOTHING""",
            [
                {"org_id": org, "promo_id": p.promo_id, "name": p.name, "pct": p.pct, "skus": ",".join(p.skus),
                 "channel": p.channel, "start": p.start, "end": p.end}
                for p in catalogue.build_promotions(products, first, today)
            ],
        )
        ctx.ops.execute(
            f"""INSERT INTO {schema}.customers VALUES (:org_id, :loyalty_id, :full_name, :email, :home_store_id,
                CAST(:signup_date AS date), now()) ON CONFLICT DO NOTHING""",
            [{"org_id": org, **{k: v for k, v in c.items() if k not in catalogue.HIDDEN_CUSTOMER_FIELDS}}
             for c in self._all_customers()],
        )
        ctx.ops.execute(
            f"INSERT INTO {schema}.pos_sequences VALUES (:org_id, :store_id, 0) ON CONFLICT DO NOTHING",
            [{"org_id": org, "store_id": s.store_id} for s in self.stores],
        )
        ctx.ops.execute(f"INSERT INTO {schema}.web_sequences VALUES (:org_id, :base) ON CONFLICT DO NOTHING",
                        {"org_id": org, "base": LIVE_ORDER_BASE})

    def _all_customers(self) -> list[dict[str, Any]]:
        # Fixed reference date: the same seed always yields the same customers.
        if self._customers is None:
            self._customers = catalogue.build_customers(self.seed, self.stores, date(2026, 10, 1))
            pools: dict[str, list[tuple[date, int]]] = {}
            for index, customer in enumerate(self._customers):
                pools.setdefault(customer["home_store_id"], []).append(
                    (date.fromisoformat(customer["signup_date"]), index))
            self._pools = {store: sorted(pool) for store, pool in pools.items()}
            self._signups = {store: [d for d, _ in pool] for store, pool in self._pools.items()}
            # Cumulative activity in signup order: picks weigh members by how often they shop.
            self._activity = {}
            for store, pool in self._pools.items():
                total, cumulative = 0.0, []
                for _, index in pool:
                    total += self._customers[index]["activity"]
                    cumulative.append(total)
                self._activity[store] = cumulative
            self._lapse = [date.fromisoformat(c["lapse_date"]) for c in self._customers]
            self._last_board: dict[int, date] = {}
        return self._customers

    def _customer(self, rng: random.Random, store_id: str | None, day: date) -> dict[str, Any] | None:
        """A loyalty customer of that store (any store online), signed up by `day` and still
        coming — picked by how often they shop; None when no active member is found."""
        customers = self._all_customers()
        if store_id is None:
            store_id = rng.choice(list(self._pools))
        pool, signups = self._pools.get(store_id, []), self._signups.get(store_id, [])
        n = bisect.bisect_right(signups, day)
        if n == 0:
            return None
        cumulative = self._activity[store_id]
        for _ in range(6):
            index = pool[min(n - 1, bisect.bisect_left(cumulative, rng.random() * cumulative[n - 1]))][1]
            if day <= self._lapse[index]:
                return customers[index] | {"_index": index}
        return None

    def _board_readiness(self, customer: dict[str, Any] | None, day: date) -> float:
        """How ready a customer is for a new board: grows with the time since their last one."""
        if customer is None:
            return 1.0
        last = self._last_board.get(customer["_index"])
        if last is None:
            return 0.7
        return min(1.8, (day - last).days / catalogue.BOARD_CADENCE_DAYS[customer["segment"]]) ** 2

    def _erp(self, ctx: Any) -> tuple[Catalogue, int]:
        """Current products and promotions, read back from the ERP."""
        schema, params = f'"{ctx.slug}"', {"org_id": ctx.org_id}
        reference = {p.sku: p for p in catalogue.build_products(self.seed)}
        products = []
        for sku, price, cost in ctx.ops.execute(
            f"SELECT sku, price_cents, cost_cents FROM {schema}.products WHERE org_id = :org_id ORDER BY sku", params
        ):
            base = reference.get(sku)
            if base is not None:
                products.append(Product(**{**base.__dict__, "price_cents": price, "cost_cents": cost}))
        promotions = [
            Promotion(promo_id=r[0], name=r[1], pct=r[2], skus=tuple(r[3].split(",")), channel=r[4], start=r[5],
                      end=r[6])
            for r in ctx.ops.execute(
                f"SELECT promo_id, name, pct, skus, channel, start_date, end_date FROM {schema}.promotions "
                "WHERE org_id = :org_id", params)
        ]
        customers = ctx.ops.execute(f"SELECT count(*) FROM {schema}.customers WHERE org_id = :org_id", params)[0][0]
        if not products:
            raise RuntimeError("the ERP has no products — run the simulator setup first")
        return Catalogue(products, promotions), int(customers)

    def _next_seq(self, ctx: Any, store_id: str, n: int = 1) -> int:
        """Reserve `n` till sequence numbers; returns the first."""
        last = ctx.ops.execute(
            f'UPDATE "{ctx.slug}".pos_sequences SET last_seq = last_seq + :n '
            "WHERE org_id = :org_id AND store_id = :store_id RETURNING last_seq",
            {"n": n, "org_id": ctx.org_id, "store_id": store_id},
        )[0][0]
        return int(last) - n + 1

    def _next_order(self, ctx: Any, n: int = 1) -> int:
        last = ctx.ops.execute(
            f'UPDATE "{ctx.slug}".web_sequences SET last_seq = last_seq + :n WHERE org_id = :org_id '
            "RETURNING last_seq",
            {"n": n, "org_id": ctx.org_id},
        )[0][0]
        return int(last) - n + 1

    # ── baskets ────────────────────────────────────────────────────────────────────
    def _pick(self, rng: random.Random, cat: Catalogue, prefix: str, day: date, promos: dict[str, Promotion],
              models: tuple[str, ...] | None = None, length: int | None = None) -> Product | None:
        pool = cat.active(prefix, day)
        if models:
            pool = [p for p in pool if p.model in models] or pool
        if not pool:
            return None
        weights = []
        for p in pool:
            w = 2.0 if p.sku in promos else 1.0
            if p.launched and (day - p.launched).days < 540:
                w *= 1.6  # this season's models sell best
            if length and p.length_in:
                w *= 1.0 / (1 + abs(p.length_in - length) / 6)
            weights.append(w)
        if not models and prefix in catalogue.BOARD_PREFIXES:
            # Middle sizes of a model sell most: pick a model, then a size around its middle.
            chosen = rng.choices(pool, weights)[0]
            sizes = [p for p in pool if p.model == chosen.model]
            mid = (len(sizes) - 1) / 2
            return rng.choices(sizes, [1.0 / (1 + abs(i - mid)) for i in range(len(sizes))])[0]
        return rng.choices(pool, weights)[0]

    def _basket(self, rng: random.Random, cat: Catalogue, day: date, channel: str, store: Store | None,
                customer: dict[str, Any] | None = None, force_sku: str | None = None) -> list[Product]:
        promos = cat.promos(day, channel)
        black_friday = any(p.promo_id.startswith("BLACK-FRIDAY") for p in promos.values())
        segment = customer["segment"] if customer else None
        anchor_rate = min(0.85, (0.34 if channel == "online" else 0.24) * (1.7 if black_friday else 1.0)
                          * self._board_readiness(customer, day))
        chosen: list[Product] = []
        anchor: Product | None = cat.by_sku.get(force_sku) if force_sku else None
        if anchor is None and rng.random() < anchor_rate:
            spot = catalogue.SPOT_MIX[store.spot if store else "surf"]
            groups = list(spot)
            weights = [spot[g] * GROUP_MONTH[g][day.month - 1] for g in groups]
            group = rng.choices(groups, weights)[0]
            if segment and rng.random() < 0.65:
                group = SEGMENT_GROUP[segment]
            mix = CATEGORY_MIX["beginner" if segment == "beginner" and group == "surf" else group]
            if channel == "online" and group == "sup":
                mix = {"SUP": 0.35, "ISP": 0.65}
            anchor = self._pick(rng, cat, rng.choices(list(mix), list(mix.values()))[0], day, promos)
        if anchor is not None:
            chosen.append(anchor)
            if customer is not None and anchor.prefix in catalogue.BOARD_PREFIXES and anchor.prefix not in ("BAR", "HRN"):
                self._last_board[customer["_index"]] = day
            for prefix, probability, models in ATTACH.get(anchor.prefix, []):
                if rng.random() < probability:
                    extra = self._pick(rng, cat, prefix, day, promos, models, anchor.length_in)
                    if extra is not None and extra not in chosen:
                        chosen.append(extra)
        else:
            for _ in range(1 + _poisson(rng, 0.35)):
                prefix = rng.choices(list(ACCESSORY_ONLY), list(ACCESSORY_ONLY.values()))[0]
                extra = self._pick(rng, cat, prefix, day, promos)
                if extra is not None and extra not in chosen:
                    chosen.append(extra)
        return chosen

    @staticmethod
    def _lines(rng: random.Random, products: list[Product], quantities: dict[str, int] | None,
               promos: dict[str, Promotion], tax_rate: float) -> list[dict[str, Any]]:
        lines = []
        for line_no, product in enumerate(products, start=1):
            if quantities is not None:
                qty = quantities[product.sku]
            else:
                qty = rng.choices([1, 2, 3, 4], [0.45, 0.35, 0.15, 0.05])[0] if product.prefix == "WAX" else 1
            promo = promos.get(product.sku)
            gross = product.price_cents * qty
            discount = round(gross * promo.pct / 100) if promo else 0
            lines.append({"line_no": line_no, "sku": product.sku, "qty": qty, "unit_price_cents": product.price_cents,
                          "discount_cents": discount, "promo_id": promo.promo_id if promo else None,
                          "tax_rate": tax_rate, "line_total_cents": gross - discount})
        return lines

    # ── documents ──────────────────────────────────────────────────────────────────
    def _receipt(self, rng: random.Random, cat: Catalogue, store: Store, seq: int, ts: datetime, *,
                 source: str = "pos", loyalty: bool | None = None, force_sku: str | None = None) -> dict[str, Any]:
        day = ts.date()
        identified = (rng.random() < LOYALTY_RATE["store"]) if loyalty is None else loyalty
        customer = self._customer(rng, store.store_id, day) if identified else None
        products = self._basket(rng, cat, day, "store", store, customer, force_sku)
        if not products:  # nothing on sale yet for that day's pick: sell wax
            products = [cat.active("WAX", day)[0]]
        tax_rate = VAT[store.country]
        lines = self._lines(rng, products, None, cat.promos(day, "store"), tax_rate)
        gross = sum(line["unit_price_cents"] * line["qty"] for line in lines)
        discount = sum(line["discount_cents"] for line in lines)
        total = gross - discount
        base = round(total / (1 + tax_rate))
        method = rng.choices(["card", "cash", "mobile"], [0.74, 0.1, 0.16])[0]
        payments = [{"method": method, "amount_cents": total}]
        if method == "card" and total > 20000 and rng.random() < 0.06:
            payments = [{"method": "voucher", "amount_cents": 5000}, {"method": "card", "amount_cents": total - 5000}]
        register = rng.randint(1, store.registers)
        return {
            "event_id": f"{store.store_id}-{seq:09d}-{zlib.crc32(_iso(ts).encode()):08x}",
            "event_type": "pos.receipt.created",
            "contract_version": "1.0",
            "source": source,
            "ts": _iso(ts),
            "produced_at": _iso(ts + timedelta(milliseconds=rng.randint(80, 900))),
            "receipt_id": f"{store.store_id}-R{register:02d}-{seq:09d}",
            "store_id": store.store_id,
            "register_id": f"R{register:02d}",
            "cashier_id": f"C{rng.randint(100, 999):04d}",
            "channel": "store",
            "currency": "EUR",
            "loyalty_id": customer["loyalty_id"] if customer else None,
            "lines": lines,
            "taxes": [{"rate": tax_rate, "base_cents": base, "amount_cents": total - base}],
            "payments": payments,
            "totals": {"gross_cents": gross, "discount_cents": discount, "grand_total_cents": total},
            "notes": None,
        }

    def _open_modern(self, day: date) -> list[Store]:
        return [s for s in self.stores if s.format == "modern" and s.opened <= day]

    def _order_versions(self, rng: random.Random, cat: Catalogue, number: int, ts: datetime, *,
                        cart: list[tuple[Product, int]] | None = None, loyalty_id: str | None = None,
                        fulfilment: str | None = None, store_id: str | None = None, returned: bool | None = None,
                        live: bool = False) -> list[dict[str, Any]]:
        day = ts.date()
        fulfilment = fulfilment or ("click_collect" if rng.random() < 0.22 else "home_delivery")
        if fulfilment == "click_collect" and store_id is None:
            stores = self._open_modern(day)
            store_id = rng.choices(stores, [catalogue.RECEIPTS_PER_DAY[s.size] for s in stores])[0].store_id
        customer = None
        if loyalty_id is None and cart is None and rng.random() < LOYALTY_RATE["online"]:
            customer = self._customer(rng, None, day)
            loyalty_id = customer["loyalty_id"] if customer else None
        if cart is None:
            products = self._basket(rng, cat, day, "online", None, customer)
            if not products:
                products = [cat.active("WAX", day)[0]]
            quantities = None
        else:
            products, quantities = [p for p, _ in cart], {p.sku: q for p, q in cart}
        lines = self._lines(rng, products, quantities, cat.promos(day, "online"), ONLINE_VAT)
        lines_total = sum(line["line_total_cents"] for line in lines)
        bulky = any(p.prefix in catalogue.BOARD_PREFIXES and p.prefix not in ("BAR", "HRN", "KTE") for p in products)
        shipping = 0 if fulfilment == "click_collect" or lines_total >= 7900 else (2900 if bulky else 499)
        total = lines_total + shipping
        if fulfilment == "click_collect":
            path = [("paid", 0), ("picked", 2), ("ready_for_collection", 20), ("collected", rng.uniform(26, 60))]
        else:
            path = [("paid", 0), ("picked", 3), ("shipped", 22), ("delivered", rng.uniform(44, 96))]
        if cart is None and rng.random() < 0.02:
            path = [("paid", 0), ("cancelled", 1)]
        is_returned = (rng.random() < (0.12 if bulky else 0.06)) if returned is None else returned
        if is_returned and path[-1][0] in ("collected", "delivered"):
            path.append(("returned", path[-1][1] + rng.uniform(96, 220)))
        reason = rng.choice(RETURN_REASONS) if is_returned else None
        base = {
            "contract_version": "1.0",
            "source": "webshop",
            "order_id": f"W-{ts.year}-{number:07d}",
            "channel": "online",
            "store_id": store_id if fulfilment == "click_collect" else None,
            "customer_loyalty_id": loyalty_id,
            "fulfilment": {"type": fulfilment, "promised_at": _iso(ts + timedelta(days=2))},
            "lines": lines,
            "payment": {"method": rng.choices(["card", "paypal", "mobile"], [0.7, 0.2, 0.1])[0], "amount_cents": total},
            "totals": {"lines_total_cents": lines_total,
                       "discount_cents": sum(line["discount_cents"] for line in lines),
                       "shipping_cents": shipping, "grand_total_cents": total},
        }
        versions = []
        for version, (status, hours) in enumerate(path, start=1):
            at = ts + (timedelta(seconds=sum(LIVE_STATUS_DELAYS[: version - 1])) if live else timedelta(hours=hours))
            versions.append({**base, "event_id": f"{base['order_id']}-v{version}",
                             "event_type": "web.order.created" if version == 1 else "web.order.updated",
                             "ts": _iso(at), "produced_at": _iso(at), "version": version, "status": status,
                             "return_reason": reason if status == "returned" else None})
        return versions

    # ── history ────────────────────────────────────────────────────────────────────
    def _store_receipts_for_day(self, store: Store, day: date, n_days_before_end: int) -> int:
        if day < store.opened:
            return 0
        rng = _stable_rng(self.seed, store.store_id, day, "count")
        ramp = 0.55 + 0.45 * min(1.0, (day - store.opened).days / 180)
        lam = (catalogue.RECEIPTS_PER_DAY[store.size] * STORE_WEEKDAY[day.weekday()] * TRAFFIC_MONTH[day.month - 1]
               * _growth(day, 1.08) * ramp * rng.uniform(0.88, 1.12))
        if store.spot == "atlantic" and day.month in (7, 8):
            lam *= 1.25  # surf-camp season on the Atlantic coast
        if store.store_id == UNDERPERFORMER and n_days_before_end < 14:
            lam *= 0.6
        return _poisson(rng, lam)

    def _store_day(self, cat: Catalogue, store: Store, day: date, end: datetime) -> list[dict[str, Any]]:
        n = self._store_receipts_for_day(store, day, (end.date() - day).days)
        if n == 0:
            return []
        rng = _stable_rng(self.seed, store.store_id, day, "receipts")
        stamps = [ts for ts in sorted(_hour_sample(rng, day, STORE_HOURS) for _ in range(n)) if ts <= end]
        source = "legacy_file" if store.format == "legacy" else "pos"
        return [self._receipt(rng, cat, store, _receipt_seq(day, i + 1), ts, source=source)
                for i, ts in enumerate(stamps)]

    def _web_day(self, cat: Catalogue, day: date, end: datetime) -> list[dict[str, Any]]:
        rng = _stable_rng(self.seed, "web", day)
        lam = (ONLINE_ORDERS_PER_DAY * ONLINE_WEEKDAY[day.weekday()] * TRAFFIC_MONTH[day.month - 1]
               * _growth(day, 1.25) * rng.uniform(0.88, 1.12))
        if any(p.promo_id.startswith("BLACK-FRIDAY") for p in cat.promotions if p.start <= day <= p.end):
            lam *= 2.2
        stamps = [ts for ts in sorted(_hour_sample(rng, day, ONLINE_HOURS) for _ in range(_poisson(rng, lam)))
                  if ts <= end]
        number = day.timetuple().tm_yday * 10000
        records = []
        for i, ts in enumerate(stamps[:9999]):
            records += [v for v in self._order_versions(rng, cat, number + i + 1, ts)
                        if datetime.fromisoformat(v["ts"]) <= end]
        return records

    def backfill(self, ctx: Any, days: int, end: datetime) -> Iterator[str]:
        cat, _ = self._erp(ctx)
        first_day = end.date() - timedelta(days=days - 1)
        stream_from = max(first_day, end.date() - timedelta(days=STREAM_DAYS - 1))
        produced = 0
        # Older months: the migration exports — one file per store and month, the web shop's per fortnight.
        month = date(first_day.year, first_day.month, 1)
        while month < stream_from:
            next_month = (month + timedelta(days=32)).replace(day=1)
            days_in = [month + timedelta(days=i) for i in range((next_month - month).days)]
            days_in = [d for d in days_in if first_day <= d < stream_from]
            for store in self.stores:
                receipts = [r for d in days_in for r in self._store_day(cat, store, d, end)]
                if receipts:
                    self._export(ctx, "pos_history_files", f"date={month.isoformat()}/store={store.store_id}",
                                 receipts, business_date=month)
                    produced += len(receipts)
            for half in (days_in[:15], days_in[15:]):
                orders = [v for d in half for v in self._web_day(cat, d, end)]
                if orders:
                    self._export(ctx, "web_history_files", f"date={half[0].isoformat()}/channel=online", orders,
                                 business_date=half[0])
                    produced += len(orders)
            yield f"{month.strftime('%Y-%m')}: {produced} records exported"
            month = next_month
        # The recent days, as they arrive today.
        day = stream_from
        while day <= end.date():
            for store in self.stores:
                receipts = self._store_day(cat, store, day, end)
                if not receipts:
                    continue
                if store.format == "legacy":
                    if day < end.date():  # a legacy store's file for a day arrives the morning after
                        self._drop(ctx, store, day, receipts, missing=0)
                    continue
                for receipt in receipts:
                    ctx.records.publish("pos_receipts", receipt)
                produced += len(receipts)
            for version in self._web_day(cat, day, end):
                ctx.records.publish("web_orders", version)
                produced += 1
            ctx.records.flush()
            if (day - stream_from).days % 5 == 4 or day == end.date():
                yield f"{day.isoformat()}: {produced} records published"
            day += timedelta(days=1)

    @staticmethod
    def _export(ctx: Any, source_id: str, delivery: str, records: list[dict[str, Any]], business_date: date) -> str:
        control = {"record_count": len(records),
                   "grand_total_cents": sum(r["totals"]["grand_total_cents"] for r in records),
                   "business_date": business_date.isoformat(), "exported_by": "legacy system migration"}
        return ctx.files.drop(source_id, delivery, records, control)

    def _drop(self, ctx: Any, store: Store, day: date, receipts: list[dict[str, Any]], missing: int) -> str:
        control = {"store_id": store.store_id, "business_date": day.isoformat(), "receipt_count": len(receipts),
                   "grand_total_cents": sum(r["totals"]["grand_total_cents"] for r in receipts),
                   "generated_at": _iso(datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
                                        + timedelta(hours=2)),
                   "file": "receipts.jsonl"}
        delivered = receipts[: len(receipts) - missing] if missing else receipts
        return ctx.files.drop("legacy_pos_files", f"date={day.isoformat()}/store={store.store_id}", delivered, control)

    # ── live ───────────────────────────────────────────────────────────────────────
    def live_tick(self, ctx: Any, now: datetime, seconds: float) -> int:
        cache = ctx.state.get("erp")
        if cache is None or (now - cache[0]).total_seconds() > 30:
            ctx.state["erp"] = cache = (now, self._erp(ctx))
        cat = cache[1][0]
        rng = ctx.state.setdefault("rng", random.Random())
        rate = ctx.config.simulator.live_rate_per_second if ctx.config.simulator else 1.0
        # Busier at lunch and early evening, never silent (a demo can happen at any hour).
        shape = max(0.35, STORE_HOURS.get(now.hour, 0.0) / 1.5)
        produced = 0
        modern = self._open_modern(now.date())
        for _ in range(_poisson(rng, rate * seconds * shape)):
            if rng.random() < 0.15:
                produced += self._start_live_order(ctx, rng, cat, now)
                continue
            store = rng.choices(modern, [catalogue.RECEIPTS_PER_DAY[s.size] for s in modern])[0]
            receipt = self._receipt(rng, cat, store, self._next_seq(ctx, store.store_id), now)
            ctx.records.publish("pos_receipts", receipt)
            ctx.state["last_receipt"] = receipt
            produced += 1
        pending = ctx.state.setdefault("pending_versions", [])
        due = [v for v in pending if datetime.fromisoformat(v["ts"]) <= now]
        for version in due:
            ctx.records.publish("web_orders", version)
            pending.remove(version)
        ctx.records.flush()
        return produced + len(due)

    def _start_live_order(self, ctx: Any, rng: random.Random, cat: Catalogue, now: datetime, **kwargs: Any) -> int:
        versions = self._order_versions(rng, cat, self._next_order(ctx), now, live=True, **kwargs)
        ctx.records.publish("web_orders", versions[0])
        ctx.state.setdefault("pending_versions", []).extend(versions[1:])
        ctx.state["last_order"] = versions
        return 1

    # ── on-demand actions (scenario.yaml simulator.actions) ────────────────────────
    def _cart(self, cat: Catalogue, raw: Any, day: date) -> list[tuple[Product, int]]:
        """A web-shop cart ([{id: sku, qty}]) checked against the catalogue: known, on sale, bounded."""
        if not isinstance(raw, list) or not raw:
            raise ValueError("the cart is empty")
        cart: dict[str, int] = {}
        for item in raw[:MAX_CART_LINES]:
            sku = str(item.get("id", "")) if isinstance(item, dict) else ""
            product = cat.by_sku.get(sku)
            if product is None or product not in cat.active(product.prefix, day):
                raise ValueError(f"{sku or 'an item'} is not on sale")
            qty = int(item.get("qty", 1) or 1)
            cart[sku] = max(1, min(10, cart.get(sku, 0) + qty))
        return [(cat.by_sku[sku], qty) for sku, qty in cart.items()]

    def act(self, ctx: Any, action: str, params: dict[str, Any], now: datetime) -> dict[str, Any]:
        cat, _ = self._erp(ctx)
        rng = random.Random()
        if action in ("sell", "sell_bad"):
            store = self.store_by_id[str(params.get("store_id") or "S001")]
            if store.format != "modern":
                raise ValueError(f"{store.store_id} is a legacy store: it only delivers daily files")
            force = catalogue.DEMO_SKU if action == "sell" and params.get("promo", True) else None
            loyalty = bool(params.get("loyalty", True)) if action == "sell" else None
            receipt = self._receipt(rng, cat, store, self._next_seq(ctx, store.store_id), now, loyalty=loyalty,
                                    force_sku=force)
            if action == "sell_bad":  # a till bug: the total no longer matches its lines
                receipt["totals"]["grand_total_cents"] += 500
                receipt["payments"] = [{"method": "card", "amount_cents": receipt["totals"]["grand_total_cents"]}]
            else:
                ctx.state["last_receipt"] = receipt
            ctx.records.publish("pos_receipts", receipt)
            ctx.records.flush()
            return {"entity": "receipt", "record_id": receipt["receipt_id"], "store_id": store.store_id,
                    "total_cents": receipt["totals"]["grand_total_cents"]}
        if action == "duplicate":
            receipt = ctx.state.get("last_receipt")
            if receipt is None:
                return self.act(ctx, "sell", {"store_id": "S001"}, now) | {"note": "no earlier receipt: sold one"}
            ctx.records.publish("pos_receipts", receipt)
            ctx.records.flush()
            return {"entity": "receipt", "record_id": receipt["receipt_id"], "note": "re-sent unchanged"}
        if action in ("web_order", "shop_checkout"):
            fulfilment = str(params.get("fulfilment") or ("home_delivery" if action == "shop_checkout"
                                                          else "click_collect"))
            cart = self._cart(cat, params.get("cart"), now.date()) if action == "shop_checkout" else None
            loyalty_id = str(params["loyalty_id"]) if params.get("loyalty_id") else None
            if loyalty_id is not None and not re.fullmatch(r"LOY-[0-9]{8}", loyalty_id):
                raise ValueError("unknown loyalty card")
            versions = self._order_versions(
                rng, cat, self._next_order(ctx), now - timedelta(seconds=5), cart=cart, loyalty_id=loyalty_id,
                fulfilment=fulfilment, store_id=str(params.get("store_id") or "S001"),
                returned=bool(params.get("returned", False)), live=True)
            ctx.records.publish("web_orders", versions[0])
            ctx.state.setdefault("pending_versions", []).extend(versions[1:])
            ctx.records.flush()
            return {"entity": "web_order", "record_id": versions[0]["order_id"],
                    "total_cents": versions[0]["totals"]["grand_total_cents"],
                    "statuses": [v["status"] for v in versions],
                    "note": "status updates follow over the next minutes (live mode publishes them)"}
        if action == "legacy_drop":
            store = self.store_by_id[str(params.get("store_id") or "S020")]
            if store.format != "legacy":
                raise ValueError(f"{store.store_id} is a modern store: it streams receipts")
            day = now.date() - timedelta(days=1)
            receipts = self._store_day(cat, store, day, now)
            missing = max(0, min(int(params.get("missing", 0) or 0), len(receipts)))
            key = self._drop(ctx, store, day, receipts, missing)
            return {"entity": "file", "record_id": key, "expected": len(receipts), "delivered": len(receipts) - missing}
        if action == "price_change":
            sku, pct = str(params.get("sku") or catalogue.DEMO_SKU), int(params.get("pct", -15) or 0)
            rows = ctx.ops.execute(
                f'UPDATE "{ctx.slug}".products SET price_cents = GREATEST(99, '
                "CAST(ROUND(price_cents * (100 + :pct) / 100.0 / 100) * 100 - 100 AS int)), updated_at = now() "
                "WHERE org_id = :org_id AND sku = :sku RETURNING price_cents",
                {"pct": pct, "org_id": ctx.org_id, "sku": sku},
            )
            if not rows:
                raise ValueError(f"unknown product {sku}")
            ctx.state.pop("erp", None)
            return {"entity": "product", "record_id": sku, "new_price_cents": rows[0][0]}
        raise ValueError(f"unknown action {action!r}")


def create(seed: int) -> SurfSimulator:
    return SurfSimulator(seed)
