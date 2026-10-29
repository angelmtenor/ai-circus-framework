"""Angel's Surf Co. master data — stores, boards, accessories, promotions, customers.

Everything here is fictional: Angel's Surf Co. was invented by the framework's author, Ángel
Martínez Tenor, a surf enthusiast, and so were its four house brands (Levante Shapes, Poniente
SUP, Tramontana Kite, Garbí Gear) — named after Mediterranean winds. Prices are indicative of
the European market for each kind of product, not of any real product or brand.

Deterministic for a given seed, so every cluster seeds the same ERP and the demo script's
stores (S001 Tarifa, S020 Glyfada), products (FSH-0103, a Bonito fish) and promotion
(AUTUMN-SWELL) always exist.
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

# ── stores ─────────────────────────────────────────────────────────────────────────────
VAT = {"ES": 0.21, "FR": 0.20, "IT": 0.22, "GR": 0.24, "CY": 0.19, "MT": 0.18}
COUNTRY = {"ES": "Spain", "FR": "France", "IT": "Italy", "GR": "Greece", "CY": "Cyprus", "MT": "Malta"}
ONLINE_VAT = VAT["ES"]  # the web shop ships from the Spanish warehouse
# Mix of the riders a store serves: share of board sales that are surf, SUP or kite.
SPOT_MIX = {
    "atlantic": {"surf": 0.84, "sup": 0.10, "kite": 0.06},
    "surf": {"surf": 0.62, "sup": 0.26, "kite": 0.12},
    "kite": {"surf": 0.22, "sup": 0.18, "kite": 0.60},
    "flat": {"surf": 0.34, "sup": 0.50, "kite": 0.16},
}
RECEIPTS_PER_DAY = {"S": 16, "M": 27, "L": 42}

# (store_id, city, country, spot, size, opened, format) — the chain's expansion along the coast:
# Spain first, France 2022, Italy 2023, the Greek franchise (still on its old tills, which
# deliver daily files) acquired in 2024, Cyprus and Malta in 2025.
_STORES = [
    ("S001", "Tarifa", "ES", "kite", "L", "2018-04-01", "modern"),
    ("S002", "Málaga", "ES", "flat", "L", "2018-06-01", "modern"),
    ("S003", "Valencia", "ES", "flat", "L", "2019-03-01", "modern"),
    ("S004", "Barcelona", "ES", "surf", "L", "2019-05-01", "modern"),
    ("S005", "Algeciras", "ES", "kite", "M", "2020-03-01", "modern"),
    ("S006", "Almería", "ES", "flat", "M", "2020-06-01", "modern"),
    ("S007", "Motril", "ES", "flat", "S", "2021-03-01", "modern"),
    ("S008", "Cartagena", "ES", "flat", "S", "2021-05-01", "modern"),
    ("S009", "Biarritz", "FR", "atlantic", "L", "2022-03-15", "modern"),
    ("S010", "Hossegor", "FR", "atlantic", "M", "2022-04-01", "modern"),
    ("S011", "Montpellier", "FR", "kite", "M", "2022-05-01", "modern"),
    ("S012", "Marseille", "FR", "flat", "M", "2022-06-01", "modern"),
    ("S013", "Nice", "FR", "flat", "M", "2022-06-15", "modern"),
    ("S014", "Varazze", "IT", "surf", "M", "2023-03-01", "modern"),
    ("S015", "Livorno", "IT", "surf", "S", "2023-04-01", "modern"),
    ("S016", "Santa Marinella", "IT", "surf", "M", "2023-05-01", "modern"),
    ("S017", "Naples", "IT", "flat", "M", "2023-05-15", "modern"),
    ("S018", "Cagliari", "IT", "kite", "M", "2023-06-01", "modern"),
    ("S019", "Palermo", "IT", "flat", "S", "2023-06-15", "modern"),
    ("S020", "Glyfada", "GR", "flat", "M", "2024-03-01", "legacy"),
    ("S021", "Thessaloniki", "GR", "flat", "S", "2024-03-01", "legacy"),
    ("S022", "Heraklion", "GR", "kite", "M", "2024-03-01", "legacy"),
    ("S023", "Limassol", "CY", "kite", "S", "2025-04-01", "modern"),
    ("S024", "St Julian's", "MT", "flat", "S", "2025-05-01", "modern"),
]


@dataclass(frozen=True)
class Store:
    store_id: str
    name: str
    region: str  # the country — the dashboards' and the map's grouping
    city: str
    country: str  # ISO code, picks the VAT rate
    spot: str
    format: str  # modern (streams receipts) | legacy (daily files)
    size: str  # S | M | L
    registers: int
    opened: date


def build_stores(seed: int) -> list[Store]:
    del seed  # the chain's stores are fixed — the demo script names them
    return [
        Store(
            store_id=sid,
            name=f"Angel's Surf Co. {city}",
            region=COUNTRY[country],
            city=city,
            country=country,
            spot=spot,
            format=fmt,
            size=size,
            registers={"S": 1, "M": 2, "L": 3}[size],
            opened=date.fromisoformat(opened),
        )
        for sid, city, country, spot, size, opened, fmt in _STORES
    ]


# ── products ───────────────────────────────────────────────────────────────────────────
# prefix: (category, rider group, board shape for the drawings, price range in cents, cost ratio)
CATEGORIES = {
    "SHT": ("Shortboards", "surf", "shortboard", (52000, 89000), 0.52),
    "FSH": ("Fish", "surf", "fish", (56000, 82000), 0.52),
    "HYB": ("Hybrids & funboards", "surf", "hybrid", (54000, 79000), 0.52),
    "MID": ("Mid-lengths", "surf", "midlength", (65000, 95000), 0.53),
    "LNG": ("Longboards", "surf", "longboard", (85000, 135000), 0.54),
    "SFT": ("Soft-tops", "surf", "softtop", (22900, 39900), 0.48),
    "SUP": ("SUP boards", "sup", "sup", (89900, 199900), 0.55),
    "ISP": ("Inflatable SUP", "sup", "isup", (39900, 79900), 0.50),
    "KTE": ("Kites", "kite", "kite", (109900, 169900), 0.58),
    "TWT": ("Twin-tips", "kite", "twintip", (49900, 89900), 0.55),
    "KSB": ("Kite surfboards", "kite", "kitesurf", (89900, 129900), 0.55),
    "BAR": ("Bars & lines", "kite", "bar", (49900, 69900), 0.56),
    "HRN": ("Harnesses", "kite", "harness", (14900, 29900), 0.50),
    "LSH": ("Leashes", "accessory", "leash", (2900, 4900), 0.40),
    "FIN": ("Fins", "accessory", "fins", (4900, 13900), 0.42),
    "PAD": ("Traction pads", "accessory", "pad", (3900, 5900), 0.40),
    "WAX": ("Wax", "accessory", "wax", (300, 500), 0.30),
    "BAG": ("Board bags", "accessory", "bag", (6900, 24900), 0.45),
    "PDL": ("Paddles", "accessory", "paddle", (8900, 39900), 0.48),
    "PMP": ("Pumps", "accessory", "pump", (3900, 8900), 0.45),
    "RCK": ("Racks & straps", "accessory", "rack", (2900, 7900), 0.42),
}
BOARD_PREFIXES = [p for p, c in CATEGORIES.items() if c[1] != "accessory"]
BRANDS = {"surf": "Levante Shapes", "sup": "Poniente SUP", "kite": "Tramontana Kite", "accessory": "Garbí Gear"}
COLOURS = ["#f4efe6", "#2bb3c0", "#ff6b4a", "#f2c14e", "#3a86ff", "#06d6a0", "#ef476f", "#1b263b", "#e9c46a",
           "#8ecae6"]

# (prefix, model, launched, retired or None, sizes, construction) — sizes: board lengths in
# inches, kite areas in m², or labels; every size is its own SKU.
_MODELS = [
    ("SHT", "Sirocco", 2019, None, [70, 72, 74, 76], "PU"),
    ("SHT", "Medusa", 2018, 2023, [70, 72, 74], "PU"),
    ("SHT", "Garbí Pro", 2022, None, [69, 71, 73, 75], "Carbon"),
    ("SHT", "Xaloc", 2024, None, [70, 72, 74], "EPS epoxy"),
    ("FSH", "Bonito", 2019, None, [64, 66, 68, 70], "PU"),
    ("FSH", "Dorada Twin", 2023, None, [62, 64, 66, 68], "EPS epoxy"),
    ("FSH", "Lampuga", 2017, 2022, [64, 66, 68], "PU"),
    ("HYB", "Calima", 2020, None, [72, 76, 80, 84], "EPS epoxy"),
    ("HYB", "Gregal", 2022, None, [70, 74, 78], "PU"),
    ("MID", "Alborán", 2021, None, [86, 90, 94], "PU"),
    ("MID", "Posidonia Glide", 2025, None, [84, 88, 92], "EPS epoxy"),
    ("LNG", "Gaditano Log", 2019, None, [110, 114, 116], "PU"),
    ("LNG", "Nerja Noserider", 2024, None, [112, 116], "PU"),
    ("SFT", "Escuela", 2018, None, [84, 96, 108], "Soft-top"),
    ("SFT", "Brisa Soft", 2023, None, [72, 84, 96], "Soft-top"),
    ("SUP", "Cabo All-round", 2019, None, [126, 132], "EPS epoxy"),
    ("SUP", "Faro Touring", 2020, None, [150, 168], "EPS epoxy"),
    ("SUP", "Regata Race", 2023, None, [168], "Carbon"),
    ("SUP", "Ola Surf SUP", 2021, None, [106, 114], "EPS epoxy"),
    ("ISP", "Bahía iSUP", 2019, None, [126, 132], "Inflatable"),
    ("ISP", "Cala iSUP", 2024, None, [120, 128], "Inflatable"),
    ("KTE", "Ventolera", 2019, None, [7, 9, 10, 12, 14], "Kite"),
    ("KTE", "Leveche Freeride", 2024, None, [8, 10, 12], "Kite"),
    ("KTE", "Torre Wave", 2022, None, [6, 8, 9, 10], "Kite"),
    ("TWT", "Estrecho TT", 2019, None, [136, 139, 142], "Twin-tip"),
    ("TWT", "Punta Paloma TT", 2025, None, [135, 138, 141], "Twin-tip"),
    ("KSB", "Valdevaqueros", 2021, None, [64, 68], "EPS epoxy"),
    ("BAR", "Control Bar", 2019, None, ["45 cm", "52 cm"], "Bar"),
    ("BAR", "Pro Bar", 2023, None, ["45 cm", "52 cm"], "Bar"),
    ("HRN", "Waist Harness", 2019, None, ["S", "M", "L", "XL"], "Harness"),
    ("LSH", "Comp Leash", 2018, None, ["6 ft"], "Leash"),
    ("LSH", "Regular Leash", 2018, None, ["7 ft", "8 ft"], "Leash"),
    ("LSH", "Knee Leash", 2018, None, ["9 ft", "10 ft"], "Leash"),
    ("LSH", "SUP Coiled Leash", 2019, None, ["10 ft"], "Leash"),
    ("FIN", "Thruster Set FCS", 2018, None, ["M", "L"], "Fins"),
    ("FIN", "Thruster Set Futures", 2018, None, ["M", "L"], "Fins"),
    ("FIN", "Twin Keel", 2019, None, ["One size"], "Fins"),
    ("FIN", "Quad Set", 2020, None, ["M", "L"], "Fins"),
    ("FIN", "Single Fin", 2018, None, ["8 in", "9 in"], "Fins"),
    ("FIN", "SUP Centre Fin", 2019, None, ["9 in"], "Fins"),
    ("PAD", "Three-piece Pad", 2018, None, ["One size"], "Pad"),
    ("PAD", "Arch Bar Pad", 2021, None, ["One size"], "Pad"),
    ("WAX", "Surf Wax", 2018, None, ["Cold", "Cool", "Warm", "Tropical", "Base coat"], "Wax"),
    ("BAG", "Day Bag", 2018, None, ["6 ft 0", "6 ft 6", "7 ft 6", "9 ft 6"], "Bag"),
    ("BAG", "Travel Coffin", 2020, None, ["6 ft 6", "7 ft 6"], "Bag"),
    ("BAG", "SUP Bag", 2019, None, ["11 ft", "12 ft 6"], "Bag"),
    ("PDL", "Alu Adjustable Paddle", 2018, None, ["One size"], "Paddle"),
    ("PDL", "Carbon Adjustable Paddle", 2020, None, ["One size"], "Paddle"),
    ("PDL", "Race Paddle", 2023, None, ["One size"], "Paddle"),
    ("PMP", "Double-action Pump", 2018, None, ["One size"], "Pump"),
    ("PMP", "Electric Pump", 2022, None, ["One size"], "Pump"),
    ("RCK", "Soft Rack", 2018, None, ["One size"], "Rack"),
    ("RCK", "Tie-down Straps", 2018, None, ["One size"], "Rack"),
]
# The demo script's products — a Bonito fish under AUTUMN-SWELL, a kite for a price change.
DEMO_SKU = "FSH-0102"
PRICE_CHANGE_SKUS = ["FSH-0102", "KTE-0103", "SUP-0101", "LSH-0201"]


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    prefix: str
    brand: str
    price_cents: int
    cost_cents: int
    group: str = ""
    model: str = ""
    size_label: str = ""
    length_in: int | None = None
    volume_l: float | None = None
    construction: str = ""
    shape: str = ""
    colour: str = ""
    launched: date | None = None
    retired: date | None = None


def size_label(prefix: str, size: int | str) -> str:
    if isinstance(size, str):
        return size
    if prefix == "KTE":
        return f"{size} m²"
    if prefix == "TWT":
        return f"{size} cm"
    return f"{size // 12}'{size % 12}\""


def _volume(prefix: str, length: int) -> float | None:
    per_inch = {"SHT": 0.40, "FSH": 0.48, "HYB": 0.50, "MID": 0.56, "LNG": 0.62, "SFT": 0.70, "SUP": 1.45,
                "ISP": 2.2, "KSB": 0.36}.get(prefix)
    return round(length * per_inch, 1) if per_inch else None


def build_products(seed: int) -> list[Product]:
    rng = random.Random(seed + 1)
    products = []
    model_no: dict[str, int] = {}
    for prefix, model, launched, retired, sizes, construction in _MODELS:
        category, group, shape, (low, high), cost_ratio = CATEGORIES[prefix]
        model_no[prefix] = model_no.get(prefix, 0) + 1
        base = rng.uniform(low, high)
        premium = {"Carbon": 1.22, "EPS epoxy": 1.08}.get(construction, 1.0)
        colour = rng.choice(COLOURS)
        for i, size in enumerate(sizes, start=1):
            length = size if isinstance(size, int) and prefix not in ("KTE", "TWT") else None
            # Longer boards and bigger kites cost a little more; prices end in 9 euros (or ,99).
            scale = 1 + 0.03 * (i - 1) if prefix in BOARD_PREFIXES else 1.0
            raw = min(high * 1.25, base * premium * scale)
            price = int(round(raw / 1000) * 1000 - 100) if raw > 2000 else int(round(raw / 50) * 50 - 1)
            label = size_label(prefix, size)
            products.append(
                Product(
                    sku=f"{prefix}-{model_no[prefix]:02d}{i:02d}",
                    name=f"{model} {label}" if label != "One size" else model,
                    category=category,
                    prefix=prefix,
                    brand=BRANDS[group],
                    price_cents=price,
                    cost_cents=round(price / 1.21 * cost_ratio * rng.uniform(0.92, 1.08)),
                    group=group,
                    model=model,
                    size_label=label,
                    length_in=length,
                    volume_l=_volume(prefix, length) if length else None,
                    construction=construction,
                    shape=shape,
                    colour=colour,
                    launched=date(launched, 2, 1),
                    retired=date(retired, 9, 30) if retired else None,
                )
            )
    return products


# ── promotions ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Promotion:
    promo_id: str
    name: str
    pct: int
    skus: tuple[str, ...]
    channel: str  # all | store | online
    start: date
    end: date


def _black_friday(year: int) -> date:
    nov30 = date(year, 11, 30)
    return nov30 - timedelta(days=(nov30.weekday() - 4) % 7)


def build_promotions(products: list[Product], first: date, today: date) -> list[Promotion]:
    """The chain's yearly calendar from `first` to `today`, plus AUTUMN-SWELL around today."""
    by = lambda *prefixes: tuple(sorted(p.sku for p in products if p.prefix in prefixes))  # noqa: E731
    promos = []
    for year in range(first.year, today.year + 1):
        bf = _black_friday(year)
        old = tuple(sorted(p.sku for p in products if p.launched and p.launched.year <= year - 2
                           and p.prefix in BOARD_PREFIXES))
        calendar = [
            Promotion(f"BLACK-FRIDAY-{year}", f"Black Friday {year}: 20% off boards", 20,
                      by("SHT", "FSH", "HYB", "MID", "LNG", "SFT", "SUP", "ISP", "KTE", "TWT", "KSB"), "all",
                      bf, bf + timedelta(days=3)),
            Promotion(f"SUMMER-SALE-{year}", f"End of summer {year}: 25% off last seasons' boards", 25, old,
                      "all", date(year, 8, 20), date(year, 9, 15)),
            Promotion(f"SUP-WEEK-{year}", f"SUP week {year}: 15% off SUP boards", 15, by("SUP", "ISP"), "store",
                      date(year, 6, 1), date(year, 6, 8)),
            Promotion(f"KITE-SPRING-{year}", f"Kite season {year}: 10% off kites online", 10, by("KTE"), "online",
                      date(year, 4, 1), date(year, 4, 21)),
        ]
        promos += [p for p in calendar if p.end >= first and p.start <= today and p.skus]
    promos.append(Promotion("AUTUMN-SWELL", "Autumn swell: 15% off fish and hybrids", 15, by("FSH", "HYB"), "all",
                            today - timedelta(days=13), today + timedelta(days=7)))
    return promos


# ── customers ──────────────────────────────────────────────────────────────────────────
# A loyalty customer's kind of riding drives what they buy and how often they replace a board —
# the signal the propensity model learns: beginners move up from a soft-top within the year,
# surfers replace boards every season or so, SUP and kite riders keep theirs longer. Each
# customer also has an activity level and, one day, drifts away (churn).
SEGMENTS = {"beginner": 0.30, "surfer": 0.30, "sup": 0.25, "kiter": 0.15}
BOARD_CADENCE_DAYS = {"beginner": 240, "surfer": 330, "sup": 720, "kiter": 480}  # time to the next board
HIDDEN_CUSTOMER_FIELDS = {"segment", "activity", "lapse_date"}
FIRST_NAMES = ["Lucía", "Hugo", "Martina", "Mateo", "Sofía", "Leo", "Julia", "Daniel", "Paula", "Pablo", "Chloé",
               "Louis", "Camille", "Giulia", "Marco", "Chiara", "Eleni", "Nikos", "Maria", "Andreas", "Elena",
               "Luca", "Inès", "Jules", "Sara", "Matteo", "Zoe", "Yannis", "Nina", "Theo"]
LAST_NAMES = ["García", "Rodríguez", "Fernández", "López", "Martínez", "Sánchez", "Martin", "Bernard", "Dubois",
              "Laurent", "Rossi", "Russo", "Ferrari", "Esposito", "Bianchi", "Papadopoulos", "Georgiou",
              "Nikolaidis", "Ioannou", "Borg", "Camilleri", "Vella", "Moreno", "Romero"]


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def build_customers(seed: int, stores: list[Store], today: date, count: int = 30000) -> list[dict]:
    """Loyalty customers, each attached to a store that was open when they signed up."""
    rng = random.Random(seed + 2)
    weights = [RECEIPTS_PER_DAY[s.size] for s in stores]
    customers = []
    for i in range(1, count + 1):
        store = rng.choices(stores, weights)[0]
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        span = max(1, (today - store.opened).days)
        segment = rng.choices(list(SEGMENTS), list(SEGMENTS.values()))[0]
        signup = store.opened + timedelta(days=int(span * rng.random() ** 1.6))
        customers.append(
            {
                "loyalty_id": f"LOY-{i:08d}",
                "full_name": f"{first} {last}",
                "email": _ascii(f"{first}.{last}{i}@example.org".lower()),
                "home_store_id": store.store_id,
                "segment": segment,
                "signup_date": signup.isoformat(),
                # Hidden behaviour (never in the ERP): how often they shop, and when they drift away.
                "activity": round(rng.lognormvariate(0, 0.8), 3),
                "lapse_date": (signup + timedelta(days=int(rng.expovariate(1 / 1500)) + 90)).isoformat(),
            }
        )
    return customers
