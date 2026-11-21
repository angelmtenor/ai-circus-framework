# 🤝 Emergency Social Aid — rules, a model and a person - Run it yourself

Source: docs/scenarios/prestaciones_sociales.md (this repository)

```bash
make k3s-pipeline                                    # ETL (rules before the model) + training
make k3s-text-embeddings SCENARIOS=prestaciones_sociales   # host GPU: embeds the 3 boxes, retrains the challenger
kubectl -n ai-circus delete job etl-vectorize --ignore-not-found && kubectl apply -f k8s/jobs/etl-vectorize-job.yaml
```

Refresh the real legal texts (cached under `~/.cache/ai-circus/prestaciones_normativa/`):

```bash
uv run --with httpx python scripts/prepare_prestaciones_normativa.py
```

etl-vectorize only bootstraps a tenant's documents when its `normativa/` prefix is empty — after editing
them, delete that prefix in SeaweedFS (or the tenant keeps its copy) and re-run the job.

Regenerate the corpus (the texts need the cluster's llm-gateway on `localhost:4000`):

```bash
cd services/training
uv run python ../../scripts/generate_prestaciones_sociales_dataset.py structure   # seeded facts + resolutions
LLM_GATEWAY_API_KEY=… uv run python ../../scripts/generate_prestaciones_sociales_dataset.py texts --model gemini-flash
uv run python ../../scripts/generate_prestaciones_sociales_dataset.py build       # CSV + ablation
cd ../form-agent && uv run python ../../scripts/generate_prestaciones_sample_documents.py   # the four scans
```
