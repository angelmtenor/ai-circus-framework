# 🏄 Angel's Surf Co. — a governed data platform (`data_product`) - The workspace

Source: docs/scenarios/surf_retail.md (this repository)

| Tab | What it shows |
|---|---|
| **Guided tour** | The platform as a live map: eleven steps, each at three depths (*Simple*, *Detailed*, *Engineer*), with live numbers and a *try it* button that does the real thing — ring up a sale, send a malformed receipt, re-send a duplicate, view as a store manager, ask the assistant |
| **Surf HQ** | Five years of seasons as a heatmap (summer lights up SUP, spring and autumn light up kites, Black Friday flashes every row) and next week's forecast, shop by shop, from `surf_ml` |
| **Data product** | The product's overview: domain, number of sources, curated datasets and governed metrics, today's live tiles, and the four platform tiers with whether each is on |
| **Live flow** | Per-minute live tiles, the path every record takes (till / web shop / migration → Kafka → contract + rules → bronze → live tile → Iceberg lake → dbt → gold marts → API · BI · assistant), pipeline events, and controls to simulate sales, malformed receipts, legacy files with a missing receipt and ERP price changes |
| **Dashboards** | Governed KPIs, map and ranking of the shops, period and comparison pickers, all from the same metrics every other door uses |
| **Explore** | Records with their processing trail, datasets, SQL, quarantine |
| **Quality & lineage** | Freshness of each silver and gold table against its SLO, the last dbt test run, open quarantine, file-delivery reconciliation, and lineage |
| **Web shop** | A fictional storefront with a recommender (association rules over every basket); checkout is a hidden simulator action, so an order becomes a real web order travelling the real pipeline |

<table>
<tr>
<td width="50%" valign="top"><img src="../screenshots/surf_retail/surf-hq.webp" alt="Surf HQ: five years of weekly units sold per category as a heatmap"></td>
<td width="50%" valign="top"><img src="../screenshots/surf_retail/dashboards.webp" alt="Dashboards: revenue, receipts, average basket, gross margin and loyalty share with a map of the shops"></td>
</tr>
<tr>
<td colspan="2" valign="top"><img src="../screenshots/surf_retail/web-shop.webp" alt="The fictional web shop with best sellers and a cart"></td>
</tr>
</table>
