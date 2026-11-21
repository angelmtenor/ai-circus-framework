# 🕵️ Payment Fraud & Financial Crime - The LLM analyst

Source: docs/scenarios/financial_crime_desk.md (this repository)

The scenario's `rubric_check` gives the active LLM (through llm-gateway) the transfer *as text*: its fields and
remittance information, never its score, outcome or typology. The LLM reads it against a rubric and must quote the
message as evidence for every signal; a quote not found in the message is marked as unverified.

- **Red flags:** a vague purpose, a payment for a third party, transit or re-export, sensitive or dual-use goods,
  support for an armed or extremist group, scam hallmarks, possible structuring, a profile mismatch.
- **Legitimate indicators:** a specific, verifiable reference, a recognisable everyday purpose, a registered
  charity, an established relationship.

`rubric_check.reviewer_role` sets the prompt's opening (here, a financial-crime analyst). The guidance tells the
model that a jurisdiction, currency or customer type is a reason to look closer and never a red flag by itself. It
must never judge a nationality, a religion or a person, and never state that a crime happened.
