# AI inventory (CycloneDX AI-BOM)

`GET /governance/inventory?format=cyclonedx` on platform-registry returns the platform's AI inventory as a
[CycloneDX](https://cyclonedx.org/) **1.6** BOM. Off by default (`GOVERNANCE_INVENTORY_ENABLED=true` turns
it on; until then the route answers `404`); admin bearer token required; read-only. The framework *supports*
an AI inventory — what it lists about risk and oversight is what the scenario owner **declared**
(`governance:` block, [schema](schemas/ai-system-governance.json)); nothing is classified automatically.

| Component | CycloneDX type | From |
|---|---|---|
| each scenario | `application` (`scenario:<slug>`) | `scenario.yaml` — declared governance as `aicf:*` properties (`ai_system_id`, owner, purpose, EU AI Act role / risk category / Annex III area, human oversight, personal-data flags, limitations, known risks) |
| each llm-gateway route, chat and embedding (`llm:<alias>`) | `machine-learning-model` | the live gateway, resolved to provider and model id (no keys, no URLs) |
| each tenant's trained model (`model:<slug>:<org>`) | `machine-learning-model` + `modelCard` | the model metadata in the scenario's bucket: task, architecture, holdout metrics, content-derived `version`, plus the declared use case, limitations and risks |
| each scenario's data (`dataset:<slug>`) | `data` | bucket, target, attribution; `classification` / `sensitiveData` only when the owner declared them |

Dependencies link a scenario to its dataset and models. `metadata.properties` says whether the gateway routes
and trained models could be read (`available` / `partial` / `unavailable`), so a tool never mistakes a gap for
an empty inventory.

Trained models are read from SeaweedFS with the registry's read-only `OBJECT_STORE_*` settings (it never
creates a bucket); without them the BOM still lists every scenario and says the models are `unavailable`.
Content-free: ids, names, versions, declared metadata and aggregate metrics — no records, prompts or keys.
SPDX 3.0 AI profile is a possible later format; `format` accepts only `cyclonedx` today.
