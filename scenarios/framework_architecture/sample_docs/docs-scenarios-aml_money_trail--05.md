# 💸 Follow the Money — transaction monitoring for a bank consortium - The data science

Source: docs/scenarios/aml_money_trail.md (this repository)

Behaviour alone (dwell time, fan-in/fan-out, pass-through, bursts) reaches ROC AUC ≈ 0.76; the
payment-format mix adds ≈ 0.03; network position alone ≈ 0.65. Every feature is label-free — the
data script prints each one's single-feature AUC (the best is 0.685) to catch leaks — and
transfers dated after 10 September were dropped because the generator's tail is 59 % laundering
and would leak the label through timing. Full table in the
[data README](../../scenarios/aml_money_trail/sample_data/README.md).
