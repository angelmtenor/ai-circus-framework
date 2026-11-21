# Architecture guide - Keeping this page true

Source: docs/architecture/README.md (this repository)

The catalog is [`libs/shared/src/ai_circus_shared/architecture_catalog.yaml`](../../libs/shared/src/ai_circus_shared/architecture_catalog.yaml).
After adding or changing a component, edit it and run:

```bash
libs/shared/.venv/bin/python scripts/generate_architecture.py
```

It rewrites `architecture.svg`, `components.md` and the app's copy. The libs/shared tests fail when a workload under
`k8s/` or `observability/bundle/` is missing from the catalog, or when a generated file is out of date.
