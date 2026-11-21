# 🏄 Angel's Surf Co. — a governed data platform (`data_product`) - Run it yourself

Source: docs/scenarios/surf_retail.md (this repository)

```bash
make k3s-data-core-up        # Kafka + data-ingest + data-api + the dbt CronJob
make k3s-data-seed           # the history, through the real pipeline (k3s-data-seed-lite loads one year)
make k3s-data-transform      # a dbt build now
make k3s-data-ml             # train the two models on the feature marts
make k3s-data-showcase       # all four tiers on a 16 GB laptop (AI-only pods scaled to 0)
```

Then open http://aiopen.localhost → *Angel's Surf Co. — Data Platform*.
