#!/usr/bin/env python3
"""Synthetic data generator for the `financial_crime_desk` scenario (two models, ADR 0018).

One invented European bank — no real customer, bank, payment or investigation behind it —
written as two files under `scenarios/financial_crime_desk/sample_data/`:

  - `payment_messages.csv`, target `Escalated`: an outgoing cross-border credit transfer
    (an MT103 / pacs.008-style message) that the financial-crime team investigated and
    reported as suspicious (the main model). The free-text remittance information (field
    :70:, at most 140 characters in the bank's character set) is read word by word.
  - `payments.csv`, target `Fraud`: a customer payment (transfer or card) later confirmed as
    fraud (extra model `payments`), with the session signals a fraud engine sees.

Every message and payment comes from a *typology*, kept in a `Typology` display column and
never trained on: the reveal shows it. Suspicious typologies (trade-based laundering,
sanctions evasion through transshipment, terrorist-financing indicators, structuring, scam
proceeds) and ordinary ones (payroll, invoices, rent, tuition, family support, property,
humanitarian donations, legitimate trade and crypto purchases) overlap on purpose:
  - a share of suspicious messages is written like an ordinary one and a share of ordinary
    ones is vague, so the words alone never decide;
  - most messages to a higher-risk jurisdiction are ordinary (family support, humanitarian
    aid, trade), so a destination alone never decides either;
  - an investigation does not confirm every suspicious typology (90%), and confirms a few
    ordinary messages (1%).
The wording never names a religion, nationality or real organisation, and no field holds
a person's name: customers and beneficiaries are pseudonymous ids.

Run: `python scripts/generate_financial_crime.py` (numpy + pandas; any service venv has them).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 11
N_MESSAGES = 3000
N_PAYMENTS = 5000
OUT_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "financial_crime_desk" / "sample_data"
MAX_REMITTANCE = 140  # MT103 field 70: 4 lines of 35 characters

JURISDICTIONS = [
    "Eurozone",
    "Other EU",
    "United Kingdom",
    "Switzerland",
    "United States",
    "Asian trade hub",
    "Offshore centre",
    "High-risk third country",
    "Conflict-adjacent region",
]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
GOODS = [
    "LED PANELS",
    "OFFICE CHAIRS",
    "COTTON T-SHIRTS",
    "CERAMIC TILES",
    "BICYCLE PARTS",
    "OLIVE OIL",
    "SOLAR INVERTERS",
    "PACKAGING FILM",
    "SPORTS SHOES",
    "LAPTOP BAGS",
    "WINE BOTTLES",
    "GARDEN FURNITURE",
]
SERVICES = ["IT SUPPORT", "CLEANING SERVICES", "MARKETING CAMPAIGN", "LEGAL FEES", "AUDIT FEES", "TRANSLATION"]
DUAL_USE = [
    "SPARE PARTS FOR INDUSTRIAL MACHINERY",
    "ELECTRONIC COMPONENTS",
    "CNC CONTROLLERS",
    "NAVIGATION MODULES",
    "HIGH SPEC BEARINGS",
    "MICROCHIPS LOT",
]

# (name, suspicious, share of all messages)
MESSAGE_TYPOLOGIES = [
    ("Payroll", False, 0.14),
    ("Supplier invoice", False, 0.20),
    ("Legitimate trade", False, 0.12),
    ("Rent", False, 0.07),
    ("Tuition", False, 0.05),
    ("Family support", False, 0.12),
    ("Property purchase", False, 0.03),
    ("Humanitarian donation", False, 0.04),
    ("Crypto purchase", False, 0.03),
    ("Trade-based laundering", True, 0.045),
    ("Sanctions evasion", True, 0.03),
    ("Terrorist-financing indicators", True, 0.025),
    ("Structuring", True, 0.035),
    ("Scam proceeds", True, 0.035),
]


def _ref(rng: np.random.Generator, prefix: str, digits: int = 4) -> str:
    return f"{prefix}{rng.integers(10 ** (digits - 1), 10**digits)}"


def _month(rng: np.random.Generator) -> str:
    return f"{rng.choice(MONTHS)} 2026"


def _pick(rng: np.random.Generator, options: list[str]) -> str:
    return str(options[int(rng.integers(len(options)))])


def _remittance(rng: np.random.Generator, typology: str) -> str:
    """Field :70: for one message of `typology` (uppercase, the bank's character set)."""
    g, s, m = _pick(rng, GOODS), _pick(rng, SERVICES), _month(rng)
    templates: dict[str, list[str]] = {
        "Payroll": [
            f"SALARY {m} EMPLOYEE {_ref(rng, 'E')}",
            f"PAYROLL {m} NET PAY",
            f"WAGES {m} REF {_ref(rng, 'HR')}",
            f"BONUS PAYMENT {m} STAFF {_ref(rng, 'E')}",
        ],
        "Supplier invoice": [
            f"INV {_ref(rng, '2026-')} {s}",
            f"INVOICE {_ref(rng, 'F', 5)} {g} PO {_ref(rng, '')}",
            f"PAYMENT OF INVOICE {_ref(rng, 'INV')} DATED {m}",
            f"{s} {m} CONTRACT {_ref(rng, 'C')}",
        ],
        "Legitimate trade": [
            f"INV {_ref(rng, '2026-')} {g} {rng.integers(50, 2000)} PCS PO {_ref(rng, '')}",
            f"BALANCE PAYMENT {g} BL {_ref(rng, 'MSKU', 6)}",
            f"30 PCT DEPOSIT {g} PROFORMA {_ref(rng, 'PI')}",
            f"{g} SHIPMENT {m} LC {_ref(rng, 'LC')}",
        ],
        "Rent": [f"RENT {m} FLAT {rng.integers(1, 9)}{_pick(rng, list('ABCD'))}", f"OFFICE RENT {m}", f"MONTHLY RENT {m} LEASE {_ref(rng, 'L')}"],
        "Tuition": [
            f"TUITION FEE SEMESTER {rng.integers(1, 3)} STUDENT {_ref(rng, 'S', 6)}",
            f"UNIVERSITY FEES {m} STUDENT ID {_ref(rng, '', 6)}",
            f"SCHOOL FEES TERM {rng.integers(1, 4)}",
        ],
        "Family support": [
            "FAMILY SUPPORT",
            f"MONTHLY HELP FOR PARENTS {m}",
            "MEDICAL BILLS FOR MOTHER",
            "GIFT FOR WEDDING",
            "HELP WITH HOUSE REPAIRS",
            f"SUPPORT FOR STUDIES {m}",
        ],
        "Property purchase": [
            f"DEPOSIT FLAT PURCHASE NOTARY {_ref(rng, 'N')}",
            f"COMPLETION PAYMENT PROPERTY REF {_ref(rng, 'P', 5)}",
            "BALANCE HOUSE PURCHASE AS PER DEED",
        ],
        "Humanitarian donation": [
            f"HUMANITARIAN AID REG CHARITY NO {_ref(rng, '', 6)}",
            f"DONATION EMERGENCY APPEAL {m} RECEIPT {_ref(rng, 'R')}",
            "WATER WELLS PROJECT REGISTERED CHARITY",
            f"MEDICAL SUPPLIES APPEAL CHARITY REF {_ref(rng, 'CH')}",
        ],
        "Crypto purchase": [
            f"DEPOSIT TO EXCHANGE ACCOUNT {_ref(rng, 'U', 6)}",
            f"BTC PURCHASE ORDER {_ref(rng, '', 6)}",
            f"TOP UP TRADING ACCOUNT {_ref(rng, 'ACC', 5)}",
        ],
        "Trade-based laundering": [
            "PAYMENT FOR GENERAL MERCHANDISE",
            "ADVANCE PAYMENT GOODS AS PER CONTRACT",
            "CONSULTANCY SERVICES",
            f"GOODS {m}",
            "COMMISSION AS AGREED",
            "PAYMENT ON BEHALF OF THIRD PARTY",
            "SETTLEMENT OF ACCOUNT",
        ],
        "Sanctions evasion": [
            f"{_pick(rng, DUAL_USE)} FOR RE-EXPORT",
            f"{_pick(rng, DUAL_USE)} TRANSIT SHIPMENT",
            f"{_pick(rng, DUAL_USE)} FINAL CONSIGNEE TO BE ADVISED",
            f"{_pick(rng, DUAL_USE)} DELIVERY VIA THIRD COUNTRY",
            "INDUSTRIAL EQUIPMENT ONWARD SHIPMENT",
        ],
        "Terrorist-financing indicators": [
            "SUPPORT FOR THE BROTHERS",
            "FOR THE TRAVEL OF THE VOLUNTEERS",
            "EQUIPMENT FOR THE GROUP",
            "CASH FOR THE CROSSING",
            "HELP FOR THE CAUSE",
            "FOR THE FAMILIES OF THE FIGHTERS",
        ],
        "Structuring": ["SAVINGS TRANSFER", "PERSONAL TRANSFER", "LOAN REPAYMENT", "TRANSFER TO OWN ACCOUNT", "PRIVATE PAYMENT"],
        "Scam proceeds": [
            "INVESTMENT PLATFORM DEPOSIT",
            "RELEASE FEE FOR INHERITANCE",
            "CUSTOMS FEE FOR PARCEL",
            "TAX TO UNLOCK PROFITS",
            "PAYMENT FOR GUARANTEED RETURN",
            "ACCOUNT VERIFICATION DEPOSIT",
        ],
    }
    return _pick(rng, templates[typology])[:MAX_REMITTANCE]


def _ordinary_lookalike(rng: np.random.Generator, typology: str) -> str:
    """A suspicious message written like an ordinary one (the words alone never decide)."""
    disguise = {
        "Trade-based laundering": "Legitimate trade",
        "Sanctions evasion": "Legitimate trade",
        "Terrorist-financing indicators": "Family support",
        "Structuring": "Family support",
        "Scam proceeds": "Crypto purchase",
    }[typology]
    return _remittance(rng, disguise)


def _vague_ordinary(rng: np.random.Generator) -> str:
    return _pick(rng, ["PAYMENT", "TRANSFER", "GOODS", "SERVICES", "AS AGREED", "INVOICE PAYMENT", "SETTLEMENT"])


def generate_messages(rng: np.random.Generator) -> pd.DataFrame:
    names = [t[0] for t in MESSAGE_TYPOLOGIES]
    shares = np.array([t[2] for t in MESSAGE_TYPOLOGIES])
    suspicious = {t[0]: t[1] for t in MESSAGE_TYPOLOGIES}
    typologies = rng.choice(names, size=N_MESSAGES, p=shares / shares.sum())
    rows = []
    for i, typ in enumerate(typologies):
        jur_p = {
            "Payroll": [0.75, 0.15, 0.04, 0.02, 0.02, 0.01, 0.0, 0.005, 0.005],
            "Supplier invoice": [0.55, 0.2, 0.08, 0.05, 0.07, 0.04, 0.005, 0.005, 0.0],
            "Legitimate trade": [0.05, 0.08, 0.07, 0.04, 0.12, 0.55, 0.03, 0.05, 0.01],
            "Rent": [0.8, 0.12, 0.05, 0.02, 0.01, 0.0, 0.0, 0.0, 0.0],
            "Tuition": [0.3, 0.2, 0.25, 0.08, 0.15, 0.02, 0.0, 0.0, 0.0],
            "Family support": [0.12, 0.15, 0.05, 0.02, 0.04, 0.08, 0.02, 0.27, 0.25],
            "Property purchase": [0.7, 0.15, 0.06, 0.04, 0.03, 0.0, 0.02, 0.0, 0.0],
            "Humanitarian donation": [0.25, 0.1, 0.1, 0.1, 0.05, 0.0, 0.0, 0.15, 0.25],
            "Crypto purchase": [0.3, 0.15, 0.1, 0.05, 0.1, 0.1, 0.2, 0.0, 0.0],
            "Trade-based laundering": [0.05, 0.05, 0.05, 0.05, 0.05, 0.35, 0.35, 0.05, 0.0],
            "Sanctions evasion": [0.0, 0.03, 0.02, 0.0, 0.0, 0.45, 0.15, 0.35, 0.0],
            "Terrorist-financing indicators": [0.05, 0.05, 0.03, 0.0, 0.0, 0.02, 0.05, 0.25, 0.55],
            "Structuring": [0.45, 0.2, 0.05, 0.05, 0.05, 0.05, 0.1, 0.03, 0.02],
            "Scam proceeds": [0.1, 0.1, 0.1, 0.05, 0.1, 0.15, 0.4, 0.0, 0.0],
        }[typ]
        jur = str(rng.choice(JURISDICTIONS, p=np.array(jur_p) / sum(jur_p)))
        company_orderer = typ in {"Payroll", "Supplier invoice", "Legitimate trade", "Trade-based laundering", "Sanctions evasion"}
        if typ in {"Rent", "Property purchase", "Supplier invoice"} and rng.random() < 0.3:
            company_orderer = not company_orderer
        beneficiary = {
            "Payroll": "Individual",
            "Supplier invoice": "Company",
            "Legitimate trade": "Company",
            "Rent": _pick(rng, ["Individual", "Company"]),
            "Tuition": "Company",
            "Family support": "Individual",
            "Property purchase": _pick(rng, ["Individual", "Company"]),
            "Humanitarian donation": "Charity",
            "Crypto purchase": "Crypto exchange",
            "Trade-based laundering": "Company",
            "Sanctions evasion": "Company",
            "Terrorist-financing indicators": _pick(rng, ["Individual", "Individual", "Charity"]),
            "Structuring": "Individual",
            "Scam proceeds": _pick(rng, ["Crypto exchange", "Crypto exchange", "Company", "Individual"]),
        }[typ]
        amount = {
            "Payroll": rng.lognormal(7.9, 0.4),
            "Supplier invoice": rng.lognormal(8.6, 1.0),
            "Legitimate trade": rng.lognormal(10.0, 0.9),
            "Rent": rng.lognormal(7.2, 0.4),
            "Tuition": rng.lognormal(8.6, 0.5),
            "Family support": rng.lognormal(6.3, 0.7),
            "Property purchase": rng.lognormal(11.6, 0.6),
            "Humanitarian donation": rng.lognormal(5.0, 1.0),
            "Crypto purchase": rng.lognormal(7.4, 1.0),
            "Trade-based laundering": rng.lognormal(10.9, 0.8),
            "Sanctions evasion": rng.lognormal(10.6, 0.7),
            "Terrorist-financing indicators": rng.lognormal(6.4, 0.6),
            "Structuring": rng.uniform(8800, 9990),
            "Scam proceeds": rng.lognormal(8.9, 0.8),
        }[typ]
        round_amount = typ in {"Trade-based laundering", "Structuring", "Scam proceeds"} and rng.random() < 0.65
        if round_amount or rng.random() < 0.12:
            amount = round(amount, -3 if amount > 5000 else -2) or 100.0
        amount = float(round(max(amount, 20.0), 2))
        if typ in {"Trade-based laundering", "Sanctions evasion"}:
            intermediaries = int(rng.choice([0, 1, 2, 3], p=[0.15, 0.35, 0.35, 0.15]))
        elif jur in {"Asian trade hub", "Offshore centre", "High-risk third country", "Conflict-adjacent region", "United States"}:
            intermediaries = int(rng.choice([0, 1, 2], p=[0.35, 0.5, 0.15]))
        else:
            intermediaries = int(rng.choice([0, 1], p=[0.85, 0.15]))
        new_beneficiary = int(rng.random() < (0.75 if suspicious[typ] else 0.2))
        prior = 0 if new_beneficiary else int(rng.integers(1, 40))
        if typ == "Structuring":
            new_beneficiary, prior = 0, int(rng.integers(3, 12))
        last7 = int(rng.integers(3, 9)) if typ == "Structuring" else int(rng.poisson(0.8 if not company_orderer else 4))
        account_age = (
            int(rng.integers(1, 9)) if typ == "Scam proceeds" and rng.random() < 0.6 else int(min(rng.exponential(60) + 1, 360))
        )
        if typ in {"Trade-based laundering", "Sanctions evasion"} and rng.random() < 0.5:
            account_age = int(rng.integers(1, 15))
        currency = (
            "EUR"
            if jur in {"Eurozone", "Other EU"} and rng.random() < 0.9
            else str(rng.choice(["USD", "GBP", "CHF", "CNY", "Other"], p=[0.55, 0.15, 0.08, 0.12, 0.1]))
        )
        if jur == "United Kingdom":
            currency = "GBP" if rng.random() < 0.8 else "EUR"
        if jur == "Switzerland":
            currency = "CHF" if rng.random() < 0.6 else "EUR"
        charges = str(rng.choice(["SHA", "OUR", "BEN"], p=[0.75, 0.2, 0.05] if not suspicious[typ] else [0.45, 0.45, 0.1]))

        # The words never decide alone.
        if suspicious[typ] and rng.random() < 0.15:
            text = _ordinary_lookalike(rng, typ)
        elif not suspicious[typ] and rng.random() < 0.07:
            text = _vague_ordinary(rng)
        else:
            text = _remittance(rng, typ)

        escalated = int(rng.random() < (0.9 if suspicious[typ] else 0.01))
        rows.append(
            {
                "MessageRef": f"TRN{2600000 + i * 7 + int(rng.integers(0, 7))}",
                "MessageType": "pacs.008" if rng.random() < 0.6 else "MT103",
                "ValueDate": f"2026-{rng.integers(1, 11):02d}-{rng.integers(1, 29):02d}",
                "OrderingCustomer": f"C-{rng.integers(10000, 99999)}",
                "Beneficiary": f"B-{rng.integers(10000, 99999)}",
                "Typology": typ,
                "Currency": currency,
                "AmountEUR": amount,
                "RoundAmount": int(amount >= 1000 and amount % 1000 == 0),
                "OrderingType": "Company" if company_orderer else "Individual",
                "AccountAgeMonths": account_age,
                "PaymentsLast7Days": last7,
                "BeneficiaryType": beneficiary,
                "BeneficiaryJurisdiction": jur,
                "Intermediaries": intermediaries,
                "NewBeneficiary": new_beneficiary,
                "PriorPaymentsToBeneficiary": prior,
                "ChargesCode": charges,
                "RemittanceInfo": text,
                "Escalated": escalated,
            }
        )
    return pd.DataFrame(rows)


# (name, fraud, share of all payments)
PAYMENT_TYPOLOGIES = [
    ("Genuine", False, 0.94),
    ("Account takeover", True, 0.018),
    ("Impersonation scam", True, 0.014),
    ("Investment scam", True, 0.012),
    ("Card-not-present", True, 0.016),
]
CHANNELS = ["Mobile app transfer", "Online banking transfer", "Card online", "Card in store"]


def generate_payments(rng: np.random.Generator) -> pd.DataFrame:
    names = [t[0] for t in PAYMENT_TYPOLOGIES]
    shares = np.array([t[2] for t in PAYMENT_TYPOLOGIES])
    typologies = rng.choice(names, size=N_PAYMENTS, p=shares / shares.sum())
    rows = []
    for i, typ in enumerate(typologies):
        fraud_typ = typ != "Genuine"
        age = int(np.clip(rng.normal(46, 16), 18, 92))
        if typ == "Impersonation scam" and rng.random() < 0.6:
            age = int(rng.integers(62, 90))
        channel = str(
            rng.choice(CHANNELS, p=[0.35, 0.15, 0.25, 0.25])
            if typ == "Genuine"
            else {
                "Account takeover": rng.choice(CHANNELS, p=[0.55, 0.45, 0.0, 0.0]),
                "Impersonation scam": rng.choice(CHANNELS, p=[0.6, 0.4, 0.0, 0.0]),
                "Investment scam": rng.choice(CHANNELS, p=[0.4, 0.45, 0.15, 0.0]),
                "Card-not-present": "Card online",
            }[typ]
        )
        card = channel.startswith("Card")
        usual = rng.lognormal(4.3, 0.8)  # the customer's usual payment, EUR
        ratio = {
            "Genuine": rng.lognormal(0.1, 0.9),
            "Account takeover": rng.lognormal(2.2, 0.8),
            "Impersonation scam": rng.lognormal(2.6, 0.7),
            "Investment scam": rng.lognormal(1.8, 0.8),
            "Card-not-present": rng.lognormal(0.8, 0.7),
        }[typ]
        amount = float(round(min(usual * ratio, 48000), 2))
        hour = int(rng.choice(range(24), p=_hours(night=typ in {"Account takeover", "Card-not-present"})))
        new_device = int(rng.random() < {"Genuine": 0.08, "Account takeover": 0.75, "Impersonation scam": 0.1, "Investment scam": 0.08, "Card-not-present": 0.7}[typ])
        new_payee = int(rng.random() < {"Genuine": 0.18, "Account takeover": 0.85, "Impersonation scam": 0.92, "Investment scam": 0.55, "Card-not-present": 0.6}[typ])
        payee_age = int(
            rng.integers(1, 60) if fraud_typ and rng.random() < 0.6 or rng.random() < 0.04 else min(rng.exponential(1500) + 30, 7000)
        )
        login_to_pay = int(
            {
                "Genuine": rng.lognormal(4.4, 0.6),
                "Account takeover": rng.lognormal(3.5, 0.7),
                "Impersonation scam": rng.lognormal(5.4, 0.7),
                "Investment scam": rng.lognormal(5.2, 0.6),
                "Card-not-present": rng.lognormal(3.6, 0.6),
            }[typ]
        )
        failed = int(rng.poisson({"Account takeover": 2.5, "Card-not-present": 0.6}.get(typ, 0.1)))
        creds = int(rng.random() < {"Account takeover": 0.6, "Impersonation scam": 0.12}.get(typ, 0.015))
        on_call = int(not card and rng.random() < {"Impersonation scam": 0.75, "Investment scam": 0.3}.get(typ, 0.03))
        remote = int(not card and rng.random() < {"Impersonation scam": 0.3, "Investment scam": 0.35, "Account takeover": 0.1}.get(typ, 0.005))
        tenure = int(min(rng.exponential(8), 40))
        last24 = int(rng.poisson({"Account takeover": 4, "Card-not-present": 5, "Investment scam": 1.5}.get(typ, 1.2)))
        cross = int(rng.random() < {"Genuine": 0.12, "Investment scam": 0.65, "Card-not-present": 0.55, "Account takeover": 0.3, "Impersonation scam": 0.15}[typ])
        mule = "Yes" if (fraud_typ and not card and rng.random() < 0.12) or rng.random() < 0.002 else "No"
        sanctions = "Yes" if rng.random() < 0.0015 else "No"
        if card:
            vop = "Not applicable"
        else:
            vop = str(
                rng.choice(["Match", "Close match", "No match"], p=[0.62, 0.18, 0.2] if fraud_typ else [0.93, 0.05, 0.02])
            )
        fraud = int(fraud_typ and rng.random() < 0.97) or int(not fraud_typ and rng.random() < 0.002)
        rows.append(
            {
                "PaymentId": f"PAY{3100000 + i * 5 + int(rng.integers(0, 5))}",
                "Typology": typ,
                "Channel": channel,
                "AmountEUR": amount,
                "AmountVsUsual": round(ratio, 2),
                "HourOfDay": hour,
                "NewDevice": new_device,
                "NewPayee": new_payee,
                "PayeeAccountAgeDays": payee_age,
                "SecondsLoginToPayment": login_to_pay,
                "FailedLogins24h": failed,
                "CredentialsChanged7d": creds,
                "OnCallDuringSession": on_call,
                "RemoteAccessApp": remote,
                "TenureYears": tenure,
                "PaymentsLast24h": last24,
                "CrossBorder": cross,
                "CustomerAge": age,
                "PayeeOnMuleList": mule,
                "SanctionsMatch": sanctions,
                "VerificationOfPayee": vop,
                "Fraud": fraud,
            }
        )
    return pd.DataFrame(rows)


def _hours(night: bool) -> np.ndarray:
    """Hour-of-day weights: daytime peaks, or a fraud-like night shoulder."""
    base = np.array([1, 0.6, 0.4, 0.3, 0.3, 0.5, 1.5, 3, 5, 6, 6, 6, 7, 6, 5, 5, 5, 6, 7, 7, 6, 4, 3, 2], dtype=float)
    if night:
        base[0:6] += 4
        base[22:24] += 2
    return base / base.sum()


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    messages = generate_messages(rng)
    payments = generate_payments(rng)
    messages.to_csv(OUT_DIR / "payment_messages.csv", index=False)
    payments.to_csv(OUT_DIR / "payments.csv", index=False)
    for name, df, target in (("payment_messages", messages, "Escalated"), ("payments", payments, "Fraud")):
        print(f"{name}: {len(df)} rows, positive rate {df[target].mean():.3f}")


if __name__ == "__main__":
    main()
