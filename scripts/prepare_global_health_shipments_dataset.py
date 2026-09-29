#!/usr/bin/env python3
"""Dataset preparation for the `global_health_shipments` scenario.

Source: USAID's "Supply Chain Shipment Pricing Data" — the delivery history of the
PEPFAR Supply Chain Management System (SCMS, 2006-2015): 10,324 line items of HIV/AIDS
antiretrovirals and HIV test kits shipped to 43 countries. Published by USAID on
data.usaid.gov (dataset a3rc-nmf6) as US federal government data, public domain. That
portal is offline since 2025, so the file is fetched from two independent GitHub
mirrors of the same export, pinned by commit and required to hash to the same SHA-256
(`RAW_SHA256`) — a re-run can never silently train on a different file.

One row per line item. The label is what a planner cares about: **was it delivered to
the client after its scheduled delivery date?** (`delivered_late`, 11.5% of rows).
Every model input is known when the shipment is planned — never the delivery itself,
its freight invoice or insurance:

- **Route**: destination country, fulfilment route (direct drop from the vendor vs.
  from an SCMS regional distribution centre), shipment mode (air / air charter /
  truck / ocean);
- **Goods**: product group, sub-classification (adult / pediatric ARVs, HIV tests...),
  dosage form (grouped), first-line regimen, line-item quantity and value, pack and
  unit price, and how many line items travel in the same shipment (same ASN/DN);
- **Plan**: planned lead time — days from the price quote first sent to the client
  (or, when no quote was recorded, from the purchase order to the vendor) to the
  scheduled delivery date — which of those two it was measured from, and the
  scheduled year and month. `lead_time_basis = Not recorded` rows (mostly the early
  "pre-PQ" years) get the median lead time of their fulfilment route.

Display-only columns ride along for the UI's globe: item description, vendor,
manufacturing site with its city-level coordinates (hand-mapped below; a few sites
carry no location and their 17 rows are dropped), the destination's capital city as
its map anchor, the scheduled and actual delivery dates, and days late. The 360 rows
with no shipment mode are dropped too (the mode is the planner's main lever).

Caveat (documented in the scenario): the scheduled date is the one on record at
delivery; if some were revised after a delay, a long planned lead time partly reflects
that revision.

Run: `cd services/training && uv run python ../../scripts/prepare_global_health_shipments_dataset.py`
(any service venv with pandas). `--refresh` ignores the raw-file cache.
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios/global_health_shipments/sample_data"
OUT_PATH = SCENARIO_DIR / "global_health_shipments.csv"
CACHE_PATH = Path.home() / ".cache/ai-circus/scms/SCMS_Delivery_History_Dataset.csv"
MIRRORS = (
    "https://raw.githubusercontent.com/Shaista043/SCMS-Delivery-History-EDA/"
    "5399f6023c4a2271b59d91c34ffaba50c218dc1f/SCMS_Delivery_History_Dataset.csv",
    "https://raw.githubusercontent.com/suheb1231/SCMS-Delivery-History-Dataset/"
    "3915b65046f313b98f9f0a8c6d3e05ec4fcfa968/SCMS_Delivery_History_Dataset%20(1).csv",
)
RAW_SHA256 = "918b992dd3e8d4b64d2a727b2c4ea607603d0c58f19484e73f7b78528c6a8673"
EXPECTED_ROWS = 10_324

# Manufacturing site -> (city, country, lat, lon), city-level. None = no location on
# record ("Not Applicable", "INVERNESS ANY", ...) — those rows are dropped.
SITES: dict[str, tuple[str, str, float, float] | None] = {
    "Aurobindo Unit III, India": ("Hyderabad", "India", 17.39, 78.49),
    "Mylan (formerly Matrix) Nashik": ("Nashik", "India", 20.00, 73.79),
    "Hetero Unit III Hyderabad IN": ("Hyderabad", "India", 17.39, 78.49),
    "Cipla, Goa, India": ("Goa", "India", 15.49, 73.83),
    "Strides, Bangalore, India.": ("Bengaluru", "India", 12.97, 77.59),
    "Alere Medical Co., Ltd.": ("Matsudo", "Japan", 35.78, 139.90),
    "Trinity Biotech, Plc": ("Bray", "Ireland", 53.20, -6.10),
    "ABBVIE Ludwigshafen Germany": ("Ludwigshafen", "Germany", 49.48, 8.44),
    "Inverness Japan": ("Matsudo", "Japan", 35.78, 139.90),
    "ABBVIE (Abbott) Logis. UK": ("Maidenhead", "United Kingdom", 51.52, -0.72),
    "BMS Meymac, France": ("Meymac", "France", 45.54, 2.15),
    "Aspen-OSD, Port Elizabeth, SA": ("Gqeberha", "South Africa", -33.96, 25.60),
    "Chembio Diagnostics Sys. Inc.": ("Medford, NY", "United States", 40.82, -72.99),
    "MSD, Haarlem, NL": ("Haarlem", "Netherlands", 52.39, 4.65),
    "Standard Diagnostics, Korea": ("Yongin", "South Korea", 37.24, 127.18),
    "Aurobindo Unit VII, IN": ("Mahbubnagar", "India", 16.74, 78.00),
    "KHB Test Kit Facility, Shanghai China": ("Shanghai", "China", 31.23, 121.47),
    "Emcure Plot No.P-2, I.T-B.T. Park, Phase II, MIDC, Hinjwadi, Pune, India": ("Pune", "India", 18.59, 73.74),
    "GSK Mississauga (Canada)": ("Mississauga", "Canada", 43.59, -79.64),
    "Janssen-Cilag, Latina, IT": ("Latina", "Italy", 41.47, 12.90),
    "Micro labs, Verna, Goa, India": ("Verna, Goa", "India", 15.36, 73.94),
    "Cipla, Kurkumbh, India": ("Kurkumbh", "India", 18.40, 74.50),
    "Roche Basel": ("Basel", "Switzerland", 47.56, 7.59),
    "Hetero, Jadcherla, unit 5, IN": ("Jadcherla", "India", 16.76, 78.14),
    "Pacific Biotech, Thailand": ("Bangkok", "Thailand", 13.76, 100.50),
    "Cipla, Patalganga, India": ("Patalganga", "India", 18.87, 73.18),
    "Ranbaxy, Paonta Shahib, India": ("Paonta Sahib", "India", 30.44, 77.62),
    "Bio-Rad Laboratories": ("Marnes-la-Coquette", "France", 48.83, 2.17),
    "GSK Ware (UK)": ("Ware", "United Kingdom", 51.81, -0.03),
    "ABBVIE GmbH & Co.KG Wiesbaden": ("Wiesbaden", "Germany", 50.08, 8.24),
    "Gilead(Nycomed) Oranienburg DE": ("Oranienburg", "Germany", 52.75, 13.24),
    "ABBVIE (Abbott) France": ("Rungis", "France", 48.75, 2.35),
    "Bristol-Myers Squibb Anagni IT": ("Anagni", "Italy", 41.74, 13.16),
    "Mylan,  H-12 & H-13, India": ("Aurangabad", "India", 19.88, 75.34),
    "MSD Midrand, J'burg, SA": ("Midrand", "South Africa", -25.99, 28.13),
    "GSK Cape Town Factory (South Africa)": ("Cape Town", "South Africa", -33.92, 18.42),
    "Roche Madrid": ("Madrid", "Spain", 40.42, -3.70),
    "Cipla Ltd A-42 MIDC Mahar. IN": ("Patalganga", "India", 18.87, 73.18),
    "BMS Evansville, US": ("Evansville, IN", "United States", 37.97, -87.57),
    "Premier Med. Corp Ltd. India": ("Mumbai", "India", 19.08, 72.88),
    "GSK Aranda": ("Aranda de Duero", "Spain", 41.67, -3.69),
    "Boehringer Ing., Koropi, GR": ("Koropi", "Greece", 37.90, 23.87),
    "Not Applicable": None,
    "Orasure Technologies, Inc USA": ("Bethlehem, PA", "United States", 40.63, -75.37),
    "BI, Ingelheim, Germany": ("Ingelheim", "Germany", 49.97, 8.05),
    "MSD Elkton USA": ("Elkton, VA", "United States", 38.41, -78.62),
    "Ranbaxy per Shasun Pharma": ("Puducherry", "India", 11.94, 79.81),
    "Inverness USA": ("Waltham, MA", "United States", 42.38, -71.24),
    "MSD Manati, Puerto Rico, (USA)": ("Manatí", "Puerto Rico", 18.43, -66.48),
    "Novartis Pharma Suffern, USA": ("Suffern, NY", "United States", 41.11, -74.15),
    "Micro Labs, Hosur, India": ("Hosur", "India", 12.74, 77.83),
    "Macleods Daman Plant INDIA": ("Daman", "India", 20.40, 72.83),
    "ABBVIE (Abbott) St. P'burg USA": ("St. Petersburg, FL", "United States", 27.77, -82.64),
    "GSK Crawley": ("Crawley", "United Kingdom", 51.11, -0.19),
    "Orasure Technologies, Inc": ("Bethlehem, PA", "United States", 40.63, -75.37),
    "Boehringer Ingelheim Roxane US": ("Columbus, OH", "United States", 39.96, -83.00),
    "Novartis Pharma AG, Switzerland": ("Basel", "Switzerland", 47.56, 7.59),
    "bioLytical Laboratories": ("Richmond, BC", "Canada", 49.17, -123.14),
    "Ipca Dadra/Nagar Haveli IN": ("Silvassa", "India", 20.27, 73.02),
    "Micro Labs Ltd. (Brown & Burk), India": ("Bengaluru", "India", 12.97, 77.59),
    "MSD Patheon, Canada": ("Toronto", "Canada", 43.65, -79.38),
    "GSK, U1, Poznan, Poland": ("Poznań", "Poland", 52.41, 16.93),
    "INVERNESS ANY": None,
    "Human Diagnostic": ("Wiesbaden", "Germany", 50.08, 8.24),
    "Ranbaxy Fine Chemicals LTD": ("New Delhi", "India", 28.61, 77.21),
    "MSD South Granville Australia": ("Sydney", "Australia", -33.86, 151.03),
    "EY Laboratories, USA": ("San Mateo, CA", "United States", 37.56, -122.33),
    "Medopharm Malur Factory, INDIA": ("Malur", "India", 13.00, 77.94),
    "ABBVIE (Abbott) Japan Co. Ltd.": ("Tokyo", "Japan", 35.68, 139.69),
    "Janssen Ortho LLC, Puerto Rico": ("Gurabo", "Puerto Rico", 18.25, -65.97),
    "Guilin OSD site, No 17, China": ("Guilin", "China", 25.27, 110.29),
    "Ranbaxy per Shasun Pharma Ltd": ("Puducherry", "India", 11.94, 79.81),
    "Gland Pharma Ltd Pally Factory": ("Hyderabad", "India", 17.39, 78.49),
    "ABBSP": None,
    "OMEGA Diagnostics, UK": ("Alva", "United Kingdom", 56.15, -3.80),
    "BUNDI INTERNATIONAL DIAGNOSTICS LTD": None,
    "INVERNESS ORGENICS LINE": ("Yavne", "Israel", 31.88, 34.74),
    "Meditab (for Cipla) Daman IN": ("Daman", "India", 20.40, 72.83),
    # Verbatim from the raw file, mojibake included — it is the lookup key.
    "Weifa A.S., Hausmanngt. 6, P.O. Box 9113 GrÃ¸nland, 0133, Oslo, Norway": ("Oslo", "Norway", 59.91, 10.75),  # noqa: RUF001
    "Premier Medical Corporation": ("Mumbai", "India", 19.08, 72.88),
    "ABBVIE Labs North Chicago US": ("North Chicago, IL", "United States", 42.33, -87.84),
    "Medochemie Factory A, CY": ("Limassol", "Cyprus", 34.71, 33.02),
    "Remedica, Limassol, Cyprus": ("Limassol", "Cyprus", 34.71, 33.02),
    "GSK Barnard Castle UK": ("Barnard Castle", "United Kingdom", 54.54, -1.92),
    "Gland Pharma, Hyderabad, IN": ("Hyderabad", "India", 17.39, 78.49),
    "Access BIO, L.C.": ("Somerset, NJ", "United States", 40.50, -74.49),
    "Mepro Pharm Wadhwan Unit II": ("Wadhwan", "Gujarat, India", 22.70, 71.68),
    "MedMira Inc.": ("Halifax", "Canada", 44.65, -63.58),
}

# Destination country -> its capital (the globe's anchor for every shipment to it).
DESTINATIONS: dict[str, tuple[float, float]] = {
    "South Africa": (-25.75, 28.19), "Nigeria": (9.08, 7.40), "Côte d'Ivoire": (5.36, -4.01),
    "Uganda": (0.35, 32.58), "Vietnam": (21.03, 105.85), "Zambia": (-15.39, 28.32),
    "Haiti": (18.59, -72.31), "Mozambique": (-25.97, 32.57), "Zimbabwe": (-17.83, 31.05),
    "Tanzania": (-6.79, 39.21), "Rwanda": (-1.95, 30.06), "Congo, DRC": (-4.32, 15.31),
    "Guyana": (6.80, -58.16), "Ethiopia": (9.03, 38.74), "South Sudan": (4.85, 31.58),
    "Kenya": (-1.29, 36.82), "Burundi": (-3.38, 29.36), "Namibia": (-22.56, 17.08),
    "Cameroon": (3.85, 11.50), "Botswana": (-24.65, 25.91), "Ghana": (5.60, -0.19),
    "Dominican Republic": (18.49, -69.93), "Sudan": (15.50, 32.56), "Swaziland": (-26.31, 31.14),
    "Mali": (12.64, -8.00), "Pakistan": (33.68, 73.05), "Guatemala": (14.63, -90.51),
    "Malawi": (-13.96, 33.79), "Benin": (6.50, 2.60), "Lebanon": (33.89, 35.50),
    "Libya": (32.89, 13.19), "Angola": (-8.84, 13.23), "Liberia": (6.30, -10.80),
    "Lesotho": (-29.31, 27.48), "Sierra Leone": (8.48, -13.23), "Senegal": (14.72, -17.47),
    "Togo": (6.13, 1.22), "Afghanistan": (34.56, 69.21), "Kazakhstan": (51.17, 71.45),
    "Kyrgyzstan": (42.87, 74.59), "Burkina Faso": (12.37, -1.52), "Guinea": (9.64, -13.58),
    "Belize": (17.25, -88.76),
}  # fmt: skip

# The raw file's 17 dosage forms, folded into what a logistics planner would group.
DOSAGE_GROUPS = {
    "Tablet": "Tablet", "Tablet - FDC": "Tablet", "Tablet - FDC + co-blister": "Tablet",
    "Tablet - FDC + blister": "Tablet", "Tablet - blister": "Tablet",
    "Chewable/dispersible tablet - FDC": "Chewable tablet", "Chewable/dispersible tablet": "Chewable tablet",
    "Capsule": "Capsule", "Delayed-release capsules": "Capsule", "Delayed-release capsules - blister": "Capsule",
    "Oral solution": "Oral liquid", "Oral suspension": "Oral liquid", "Powder for oral solution": "Oral liquid",
    "Oral powder": "Oral liquid", "Injection": "Injection", "Test kit": "Test kit", "Test kit - Ancillary": "Test kit",
}  # fmt: skip

OUTPUT_COLUMNS = [
    "shipment_id",
    # display
    "item_description",
    "vendor",
    "manufacturing_site",
    "origin_city",
    "origin_country",
    "origin_lat",
    "origin_lon",
    "destination_lat",
    "destination_lon",
    "scheduled_date",
    "delivered_date",
    "days_late",
    # features
    "destination_country",
    "fulfill_via",
    "shipment_mode",
    "product_group",
    "sub_classification",
    "dosage_form",
    "first_line",
    "line_item_quantity",
    "line_item_value_usd",
    "pack_price_usd",
    "unit_price_usd",
    "lines_in_shipment",
    "planned_lead_days",
    "lead_time_basis",
    "scheduled_year",
    "scheduled_month",
    # target
    "delivered_late",
]


def fetch_raw(refresh: bool) -> bytes:
    """The raw export, from cache or from the first mirror that serves the pinned bytes."""
    if CACHE_PATH.exists() and not refresh:
        raw = CACHE_PATH.read_bytes()
    else:
        raw = b""
        for url in MIRRORS:
            with urllib.request.urlopen(url, timeout=60) as response:
                raw = response.read()
            if hashlib.sha256(raw).hexdigest() == RAW_SHA256:
                break
            print(f"warning: {url} does not match the pinned SHA-256, trying the next mirror")
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != RAW_SHA256:
        raise SystemExit(f"raw SCMS file hash {digest} != pinned {RAW_SHA256} — refusing to build.")
    return raw


def build(raw: pd.DataFrame) -> pd.DataFrame:
    if len(raw) != EXPECTED_ROWS:
        raise SystemExit(f"expected {EXPECTED_ROWS} rows, got {len(raw)}")
    unknown_sites = set(raw["Manufacturing Site"]) - set(SITES)
    unknown_countries = set(raw["Country"]) - set(DESTINATIONS)
    if unknown_sites or unknown_countries:
        raise SystemExit(f"unmapped sites {sorted(unknown_sites)} / countries {sorted(unknown_countries)}")

    def day(column: str, fmt: str) -> pd.Series:
        return pd.to_datetime(raw[column], format=fmt, errors="coerce")

    scheduled = day("Scheduled Delivery Date", "%d-%b-%y")
    delivered = day("Delivered to Client Date", "%d-%b-%y")
    quoted = day("PQ First Sent to Client Date", "%m/%d/%y")
    ordered = day("PO Sent to Vendor Date", "%m/%d/%y")

    out = pd.DataFrame(index=raw.index)
    out["shipment_id"] = raw["ID"].astype(int)
    out["item_description"] = raw["Item Description"].str.strip()
    out["vendor"] = raw["Vendor"].str.strip()
    out["manufacturing_site"] = raw["Manufacturing Site"].str.strip()
    site = raw["Manufacturing Site"].map(SITES)
    located = site.notna()
    out["origin_city"] = site.map(lambda s: s[0] if s else None)
    out["origin_country"] = site.map(lambda s: s[1] if s else None)
    out["origin_lat"] = site.map(lambda s: s[2] if s else np.nan)
    out["origin_lon"] = site.map(lambda s: s[3] if s else np.nan)
    out["destination_lat"] = raw["Country"].map(lambda c: DESTINATIONS[c][0])
    out["destination_lon"] = raw["Country"].map(lambda c: DESTINATIONS[c][1])
    out["scheduled_date"] = scheduled.dt.strftime("%Y-%m-%d")
    out["delivered_date"] = delivered.dt.strftime("%Y-%m-%d")
    out["days_late"] = (delivered - scheduled).dt.days

    out["destination_country"] = raw["Country"]
    out["fulfill_via"] = raw["Fulfill Via"]
    out["shipment_mode"] = raw["Shipment Mode"]
    out["product_group"] = raw["Product Group"]
    out["sub_classification"] = raw["Sub Classification"]
    out["dosage_form"] = raw["Dosage Form"].map(DOSAGE_GROUPS)
    out["first_line"] = raw["First Line Designation"]
    out["line_item_quantity"] = raw["Line Item Quantity"].astype(int)
    out["line_item_value_usd"] = raw["Line Item Value"].astype(float).round(2)
    out["pack_price_usd"] = raw["Pack Price"].astype(float).round(2)
    out["unit_price_usd"] = raw["Unit Price"].astype(float).round(2)
    out["lines_in_shipment"] = raw.groupby("ASN/DN #")["ID"].transform("size").astype(int)

    from_quote = (scheduled - quoted).dt.days
    from_order = (scheduled - ordered).dt.days
    out["lead_time_basis"] = np.where(
        from_quote.notna(), "Quote to client", np.where(from_order.notna(), "Order to vendor", "Not recorded")
    )
    lead = from_quote.fillna(from_order)
    lead = lead.fillna(lead.groupby(raw["Fulfill Via"]).transform("median"))
    # A handful of scheduled dates precede their own quote (re-planned orders) — keep
    # the sign, cap the tails the slider can show.
    out["planned_lead_days"] = lead.clip(-60, 720).round().astype(int)
    out["scheduled_year"] = scheduled.dt.year.astype(int)
    out["scheduled_month"] = scheduled.dt.month.astype(int)
    out["delivered_late"] = (delivered > scheduled).astype(int)

    keep = located & raw["Shipment Mode"].notna()
    no_mode = raw["Shipment Mode"].isna().sum()
    print(f"dropped {(~located).sum()} rows without a site location, {no_mode} without a mode")
    out = out.loc[keep, OUTPUT_COLUMNS].sort_values(["scheduled_date", "shipment_id"])
    if out.isna().any().any():
        raise SystemExit(f"unexpected missing values: {out.columns[out.isna().any()].tolist()}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--refresh", action="store_true", help="re-download the raw file")
    args = parser.parse_args()
    raw_bytes = fetch_raw(args.refresh)
    raw = pd.read_csv(pd.io.common.BytesIO(raw_bytes), encoding="utf-8-sig")
    out = build(raw)
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_PATH, index=False)
    late = out["delivered_late"].mean()
    print(f"wrote {OUT_PATH}")
    print(f"{len(out):,} line items, {out['destination_country'].nunique()} countries, late rate {late:.1%}")


if __name__ == "__main__":
    main()
