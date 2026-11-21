# 🕵️ Payment Fraud & Financial Crime - The Message screening tab

Source: docs/scenarios/financial_crime_desk.md (this repository)

Every transfer is scored by a model that never saw it (out-of-fold) and listed in an inbox, highest score first,
with a threshold that splits it into *Escalate* and *Release*. Opening a transfer draws it as the message the bank
sends, MT103 / pacs.008 style:

| Tag | Field | Columns |
|---|---|---|
| `:20:` | Reference | the message reference |
| `:23B:` | Message | MT103 or pacs.008 |
| `:32A:` | Value date · currency · amount | date, currency, amount in EUR, round amount |
| `:50K:` | Ordering customer | pseudonymous id, individual or company, account age, transfers in the last 7 days |
| `:56A:` | Intermediary banks | how many correspondents are in the chain |
| `:59:` | Beneficiary | pseudonymous id, type, jurisdiction group, new beneficiary, prior payments |
| `:71A:` | Charges | SHA, OUR or BEN |
| `:70:` | Remittance information | the free-text purpose, at most 140 characters |

Each field is tinted by how much it pushed the score: red towards *Escalated*, blue away. The out-of-fold SHAP values
are summed over the field's columns. The free text is shaded word by word by the deployed model. Next to the message:

- **What pushed the score**: the top drivers, as probability points.
- **Two trained models**: the deployed TF-IDF + LightGBM model and the challenger, which reads the text as
  `voyage-4-nano` sentence embeddings. The challenger is never promoted on its own.
- **The LLM analyst** (below), on request.

*Reveal the investigation* adds the real outcome, the typology the investigation found, the misses at the
threshold, and a table of every typology with the share the model sends to an analyst. The typology explains the
outcomes and is never a model input.

The tab draws only DOM: no canvas, no animation, and an inbox capped at 60 rows.
