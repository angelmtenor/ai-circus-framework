# 🕵️ Payment Fraud & Financial Crime - Run it yourself

Source: docs/scenarios/financial_crime_desk.md (this repository)

```bash
python scripts/generate_financial_crime.py            # regenerate the transfers and payments (seeded)
python scripts/generate_financial_crime_mules.py      # regenerate the mule accounts and their network (seeded)
make k3s-pipeline                                     # ETL + training of every tabular scenario
make k3s-vectorize                                    # index the AMLR articles for the assistant's document tool
make k3s-text-embeddings SCENARIOS=financial_crime_desk   # optional: the challenger (host GPU), then retrain
```
