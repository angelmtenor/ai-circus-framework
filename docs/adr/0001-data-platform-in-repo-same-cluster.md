# 0001 — Data Platform in this repo, on the same k3s cluster, as switchable tiers

**Context.** The proposal offered three integration options: same repo + cluster, separate repo +
cluster, or same repo with a separable deployment. The reference machines have 16 GB of RAM
(WSL2 gets ~11 GiB); the base framework already uses ~5.9 GiB. The full data stack (ClickHouse,
Dagster, Superset, OpenMetadata + OpenSearch) is estimated at 8–10 GiB on top.

**Decision.** One repo, one cluster, namespace `ai-circus`. The data tier is split into four
kustomize overlays (`k8s/data-platform/{core,analytics,bi,governance}`), each with its own
`make` up/down target and none applied by `k3s-all`. `k3s-data-showcase` scales the AI-only
extras to zero and applies every tier. Code reaches services by configuration
(`DATA_API_URL`, `KAFKA_BOOTSTRAP_SERVERS`), so moving the data tier to a second machine later is
a manifest change, not a code change.

**Consequences.** One identity provider, one gateway, one registry, one admin console — the "one
platform" story holds. A second k3d cluster on the same laptop was rejected: it shares the same
RAM and adds cross-cluster OIDC and networking. Not every tier can run next to every AI
extra on 16 GB; the showcase target makes that trade explicit instead of hiding it.
