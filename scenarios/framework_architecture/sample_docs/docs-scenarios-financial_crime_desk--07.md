# 🕵️ Payment Fraud & Financial Crime - The data

Source: docs/scenarios/financial_crime_desk.md (this repository)

**Transfers** (`payment_messages.csv`, target `Escalated`, 15% positive). Each message comes from a typology:

- *Ordinary:* payroll, supplier invoice, legitimate trade, rent, tuition, family support, property purchase,
  humanitarian donation, crypto purchase.
- *Suspicious:* trade-based laundering, sanctions evasion, terrorist-financing indicators, structuring, scam proceeds.

They overlap on purpose:

- 15% of suspicious messages are written like ordinary ones, and 7% of ordinary ones are vague.
- Most transfers to a higher-risk jurisdiction are ordinary (family support, humanitarian aid, trade).
- An investigation confirms 90% of the suspicious typologies and 1% of the ordinary ones.

**Payments** (`payments.csv`, target `Fraud`, 6% positive). The typologies are genuine, account takeover,
impersonation scam, investment scam and card-not-present. The signals a fraud engine sees: new device, new payee,
payee account age, time from login to payment (very fast for a script, very slow for a coached victim), failed
logins, changed credentials, an active phone call or remote-access software during the session, amount against the
customer's usual, hour, cross-border.

**Mule accounts** (`mule_accounts.csv`, target `Mule`, 10% positive, and `mule_network.json`). Fifteen rings in
three shapes:

- *collector rings*: many outside payers → two or three collectors → layering accounts → crypto or cash;
- *chains*: A → B → C → D → exchange;
- *device farms*: recent accounts on shared devices and addresses that collect and cash out.

They overlap with genuine behaviour on purpose:

- a third of the rings are patient: they hold the money for days and keep a share of it;
- small businesses also collect from many payers, households and couples also share addresses and tablets;
- some people sweep their pay to another account the same day, and students also spend what they receive at once;
- some recruited mules are students, and some recruited accounts are years old.

An investigation confirms 88% of ring members and 1% of genuine accounts.
