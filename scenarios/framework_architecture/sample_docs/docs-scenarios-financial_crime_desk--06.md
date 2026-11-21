# 🕵️ Payment Fraud & Financial Crime - The Mule network tab

Source: docs/scenarios/financial_crime_desk.md (this repository)

The `mules` model's `network_explorer` tab draws every account as a node, coloured by its out-of-fold score and
sized by the money it received. Lines are transfers between accounts, replayed day by day on the timeline.
Hexagons are the devices and home addresses that two or more accounts share. Outside parties (payers,
employers, crypto exchanges, the ATM network) are unscored; everyday card spending is counted in the features
but not drawn. From there you can:

- open an account's dossier: its score, the SHAP drivers rolled into six pillars (*Money in*, *Pass-through*,
  *Cash-out*, *Shared identity*, *Network position*, *Profile*) and its facts as percentiles;
- trace a path between two accounts, or switch to *Investigate an account* to work outwards from one account
  and score a hypothetical one from the ties you give it;
- reveal the investigation: who was confirmed as a mule.

The model never sees the network itself, only numbers computed from it offline: money in and out, the
pass-through ratio, the median dwell time between money arriving and leaving, distinct senders and recipients,
the shares cashed out or sent to crypto, night activity, the accounts sharing a device or an address, and
PageRank and clustering.
