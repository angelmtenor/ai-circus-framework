# Kubernetes (k3s) — local single-node dev

Dev-parity manifests for running this platform on a local [k3d](https://k3d.io/) cluster
(k3s-in-Docker) instead of `docker compose` — **not** a production/multi-node setup. Images are
built locally and imported straight into the cluster's containerd (no registry, no
`imagePullSecrets`), and k3d's bundled Traefik serves the exact same `*.localhost` hostnames the
compose setup uses, so `make verify`'s checks are reusable unchanged.

## Why k3d

The rest of this repo's tooling is Docker-based (`docker build`, `docker compose`) — k3d runs k3s
*inside* Docker, so `k3d image import` reuses the same local image cache `make k3s-build` already
populated, with none of bare k3s's systemd install footprint or `k3s ctr images import`
tarball round-trip.

## Prerequisites

- Docker (already required for the rest of this repo)
- [`k3d`](https://k3d.io/#installation)
- [`kubectl`](https://kubernetes.io/docs/tasks/tools/#kubectl)
- `.env` and `infra/traefik/console.htpasswd`/`infra/seaweedfs/s3.json` already bootstrapped —
  run `make bootstrap` first if you haven't (see root README's "Getting started")

## Layout

```
k8s/
  base/            # namespace, shared config, infra (postgres/logto/qdrant/seaweedfs),
                    # every backend Deployment + Service + IngressRoute, kustomization.yaml
  jobs/             # etl-tabular / training / etl-vectorize — one-shot batch.Job manifests,
                    # applied manually via `make k3s-pipeline`, not part of `make k3s-up`
```

Plain YAML + a single Kustomize base — no overlays, no Helm chart. This is deliberately as flat
as a working setup allows: dev-parity for one local cluster doesn't need per-environment overlays.

## Workflow

```bash
make k3s-cluster    # create the local k3d cluster (idempotent) — port 80, ./scenarios bind-mounted
make k3s-build      # docker build every service image, tagged ai-circus/<service>:local
make k3s-import     # import those images into the k3d cluster's containerd
make k3s-secrets    # generate the app-env/traefik-basicauth/seaweedfs-s3-config Secrets
make k3s-up         # kubectl apply -k k8s/base
make k3s-wait       # wait for every pod to actually be Ready (not just Running)
make k3s-pipeline   # optional: run the churn ETL -> training pipeline as k8s Jobs
make k3s-verify     # reuse `make verify`'s curl checks against this cluster
```

Open `http://aiopen.localhost` once `k3s-verify` passes — same login flow as the docker-compose
setup. Re-run `make k3s-secrets` any time `.env`/`infra/traefik/console.htpasswd`/
`infra/seaweedfs/s3.json` change; re-run `make k3s-build k3s-import` and
`kubectl -n ai-circus rollout restart deployment/<service>` after code changes.

Tear down with `make k3s-down` (deletes the applied manifests; StatefulSet PVCs are retained by
k8s convention) or `k3d cluster delete ai-circus` for a full wipe including all volumes.

## Design notes

- **Secrets are never committed.** `make k3s-secrets` (`scripts/k3s_generate_secrets.sh`) creates
  them from local, gitignored files — the same rule `.env` itself follows. Every backend pod
  receives the entire `app-env` Secret via `envFrom` (harmless: pydantic-settings only reads the
  env vars a service's own `EnvConfig` declares) plus a small number of explicit `env:` overrides
  for keys docker-compose.yml itself renames (e.g. `LLM_GATEWAY_API_KEY` from `.env`'s
  `LITELLM_MASTER_KEY`) or that must be a k8s-internal literal (e.g. `LOGTO_JWKS_URL`).
- **`./scenarios` is a `hostPath` volume**, mounted at `/app/scenarios` in every pod that needs
  it — the k8s-native equivalent of docker-compose.yml's read-only bind mount, viable here because
  this is single-node local dev. `make k3s-cluster` bind-mounts the repo's `scenarios/` directory
  into every k3d node at `/scenarios` for this to resolve.
- **Traefik `IngressRoute`/`Middleware` CRDs**, not plain `Ingress` — k3s ships Traefik already,
  and its CRDs let the `Host(...)` rules and the `admin-basicauth` gate (on `admin.logto.localhost`
  and `console.objectstore.localhost`) carry over almost verbatim from docker-compose.yml's own
  Traefik labels.
- **`ui-react` needs no separate build.** Its backend base URLs are baked in at `docker build` time
  via `VITE_*` args, defaulting to the same `*.localhost` hostnames this cluster's Traefik also
  serves — so the same image `make k3s-build` produces works unchanged. A runtime-injected
  `/config.js` (see `ui-react/src/config.ts`) would only be needed for a cluster using different
  hostnames — out of scope here.
- **Optional Ollama and the one-shot pipeline services are intentionally excluded** from
  `k8s/base/`'s default `k3s-up` — Ollama mirrors compose's own opt-in `profiles: ["ollama"]`
  (add a Deployment/PVC for it yourself if you need the free local LLM fallback here too), and the
  pipeline services are `k8s/jobs/*` applied only via `make k3s-pipeline`, matching their
  one-shot, non-`k3s-up` nature in docker-compose.yml too (`profiles: ["pipeline"]`).
