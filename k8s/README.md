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
  base/            # namespace, shared config, infra (postgres/keycloak/qdrant/seaweedfs),
                    # every backend Deployment + Service + IngressRoute, kustomization.yaml
  jobs/             # etl-tabular / training / etl-vectorize — one-shot batch.Job manifests,
                    # applied manually via `make k3s-pipeline`, not part of `make k3s-up`;
                    # plus dl-training-job.yaml (per-scenario template) and gpu-smoke-pod.yaml
  deep-learning/    # optional overlay: dl-inference (make k3s-dl-up), never part of k3s-up
  data-platform/    # optional overlay: Kafka (make k3s-data-platform-up)
infra/k3s-gpu/      # k3s node image with the NVIDIA runtime + device plugin (GPU clusters)
```

Plain YAML + a single Kustomize base — no overlays, no Helm chart. This is deliberately as flat
as a working setup allows: dev-parity for one local cluster doesn't need per-environment overlays.

## Workflow

```bash
make k3s-cluster    # create the local k3d cluster (idempotent) — port 80, ./scenarios bind-mounted
make k3s-build      # docker build every service image, tagged ai-circus/<service>:local
make k3s-import     # import those images into the k3d cluster's containerd
make k3s-secrets    # generate the per-workload/traefik-basicauth/seaweedfs-s3-config Secrets
make k3s-up         # kubectl apply -k k8s/base
make k3s-wait       # wait for every pod to actually be Ready (not just Running)
make k3s-pipeline   # run the ETL -> training pipeline (every tabular_ml scenario, not just
                    # churn — SCENARIOS is unset/empty) as k8s Jobs
make k3s-verify     # reuse `make verify`'s curl checks against this cluster
```

Or run the first six of those in one shot with `make k3s-all` (still run `make k3s-pipeline`/
`make k3s-verify` yourself afterward — they're not part of it, since they're separate steps, not
"getting the cluster up").

**`make k3s-pipeline` is required, not optional, before any `tabular_ml` scenario can serve a
prediction** — a freshly created or freshly recreated cluster (including after `k3d cluster
delete`/`k3s-cluster` again, e.g. to recover from Gotcha 8) starts with zero trained model
artifacts in SeaweedFS. Skipping it doesn't fail loudly at deploy time: `k3s-up`/`k3s-wait`/
`k3s-verify` all pass, because they only check that `prediction` is reachable, not that any
scenario actually has a model — the first real prediction request 503s instead, with `No trained
model artifacts for scenario='<slug>' (org='<org>', fallback org='demo' also has none — has
`training` run for it?)`. There's no equivalent `make k3s-*` target yet for
`k8s/jobs/etl-vectorize-job.yaml` (the `conversational_rag`/`assisted_form` counterpart, seeding
each scenario's document catalog into Qdrant) — apply it the same way as the other Jobs when a
RAG-backed scenario needs its documents seeded on a fresh cluster:
```bash
kubectl -n ai-circus delete job etl-vectorize --ignore-not-found
kubectl apply -f k8s/jobs/etl-vectorize-job.yaml
kubectl -n ai-circus wait --for=condition=complete job/etl-vectorize --timeout=300s
```

### Deep learning (optional) and GPUs

The two `kind: deep_learning` healthcare scenarios (`symptom_triage` — NLP, `chest_xray_pneumonia`
— computer vision) are served by a separate, opt-in `dl-inference` pod (onnxruntime only, no
torch, ~0.6 GB with both models loaded) and trained by `dl-training`. **Training is never part of
`make all`/`k3s-all`** — minutes on a GPU, far longer on a CPU:

```bash
make k3s-all-dl        # k3s-all + build/import the DL images + deploy dl-inference (no training)
make dl-gpu-check      # does this host have a GPU? does the cluster expose one?
make dl-train-nlp      # fine-tune on THIS host's GPU (CPU asks first), publish to SeaweedFS
make dl-train-cv       #   "  — dl-inference serves the new model within a minute, no restart
make k3s-dl-train-nlp  # the same as an in-cluster Job (CPU budget unless the cluster has a GPU)
```

Admins get the same in-cluster training button, GPU status and each model's card (trained on
which device, held-out metrics) under **Platform → Deep Learning** in the UI.

**Giving the cluster the GPU.** k3d attaches GPUs only when a cluster is *created*, and only if
Docker can pass one through:

1. Once, as root: `sudo ./scripts/setup_gpu_containers.sh` — installs the NVIDIA Container
   Toolkit into Docker (native Linux with a working driver, or WSL2 with the Windows driver).
2. `make k3s-cluster` (`K3S_GPU=auto`, the default) now detects Docker's `nvidia` runtime and
   creates the node from `ai-circus/k3s-gpu` (infra/k3s-gpu/: k3s on Ubuntu + NVIDIA Container
   Toolkit + the NVIDIA device plugin v0.20.1, which supports WSL2) with `--gpus all`. An existing
   CPU-only cluster must be recreated: `k3d cluster delete ai-circus && make k3s-all-dl`.
3. `make k3s-gpu-smoke` runs `nvidia-smi` in a pod (`runtimeClassName: nvidia`, one
   `nvidia.com/gpu`) to prove it end to end.

Verified on WSL2 (RTX 4070 Laptop, driver 610.62, k3s v1.35.5): the device plugin logs
`Detected platform: wsl` and the node advertises `nvidia.com/gpu: 1`. Two things this needed:
GPU clusters are created with `--disable-cloud-controller` — on the Ubuntu-based GPU node the
embedded cloud-controller-manager otherwise loses a startup race for its RoleBinding and
restart-loops the whole server (k3s-io/k3s#7328; a single-node k3d cluster doesn't need it) —
and `setup_gpu_containers.sh` looks for `nvidia-smi` in `/usr/lib/wsl/lib` itself, since
`sudo`'s `secure_path` drops it from `PATH` on WSL.

With a GPU in the cluster, `make k3s-dl-build` builds the CUDA flavour of the dl-training image
(`DL_TRAINING_TORCH=auto`), and data-platform-manager requests `nvidia.com/gpu` + the `nvidia`
RuntimeClass for every training Job it starts; without one, Jobs use each scenario's CPU budget.

### Pausing vs. tearing down

Not working the demo but staying in WSL? `k3d cluster stop/start` stops the cluster's containers
(freeing CPU/RAM) without deleting any pod, volume, or Secret state — much cheaper than deleting
and redoing `k3s-all` later:

```bash
make k3s-pause         # stop the cluster's containers — state is kept
make k3s-resume        # start it back up — run `make k3s-wait` after to confirm pods are Ready
make k3s-resume-lite   # same, but without the heavy rarely-used pods (see below) + k3s-wait
make k3s-all-lite      # fresh cluster in lite mode: k3s-all with the skipped pods scaled to 0 right after k3s-up
```

**Lite mode — running on less RAM.** The full `k8s/base` set idles at ~7 GB of pod RSS, which is
what forces the 12 GB VM. `make k3s-lite` scales the Deployments in `K3S_LITE_SKIP` to 0 replicas
on the running cluster; `make k3s-resume-lite` is `k3s-resume` + `k3s-lite` + `k3s-wait` in one
go, and `make k3s-full` scales them back to 1. The default skip list is the heaviest pods the
day-to-day demo never touches — `mlflow` (~350 Mi, MLOps monitor) and `agui-voice` (~830 Mi,
only voice mode uses it) — roughly 1.2 GB less. Langfuse and its ClickHouse/Valkey dependencies
deliberately stay on so GenAI tracing keeps working, and so does `data-platform-manager` (it
backs the admin **Platform** health dashboard, the natural place to check on a lite cluster);
drop them too if you need to go lower:

```bash
make k3s-lite K3S_LITE_SKIP="mlflow agui-voice data-platform-manager langfuse-web langfuse-worker"
```

While lite, voice mode and `mlflow.localhost` are unavailable and the Platform health dashboard
shows those two as down (every other view is unaffected — `make k3s-verify` still passes). The scale
is cluster state, so it survives a plain `k3s-pause`/`k3s-resume`; `make k3s-up` (a fresh
`kubectl apply -k`) or `make k3s-full` restores every replica. `make k3s-wait` works unchanged in
either mode — `kubectl rollout status` reports a 0-replica Deployment as rolled out immediately.

Resuming brings pods back with the *exact* images they already had — if you also rebuild
(`make k3s-build k3s-import`) after resuming, those pods keep running their old container content
regardless, since `kubectl apply` only restarts a pod when the Deployment spec text itself
changes (the image tag string `ai-circus/<service>:local` never does). Explicitly
`kubectl -n ai-circus rollout restart deployment/<service>` for every service you rebuilt, or
nothing you test afterward reflects the new build (see the `k3s-deploy-verify` skill's Gotcha 5).

This is different from `make k3s-down` (deletes the applied k8s manifests, cluster keeps running)
and `k3d cluster delete ai-circus` (deletes the cluster itself, full wipe including volumes) —
see "Tear down" below.

**After a Docker daemon or host restart** (a WSL `--shutdown`, a reboot) the cluster's containers
come back on their own, but Docker may hand the server node and the load balancer each other's
IP — k3s then crash-loops (`failed to find interface with specified node ip`) and `kubectl` only
answers for seconds at a time. The `k3s-deploy-verify` skill's Gotcha 8 has the two-line
recovery (restart the two containers in the order that restores the server's old IP). To make
it impossible, create the cluster with a pinned subnet: `make k3s-cluster K3S_SUBNET=172.28.0.0/16`
— k3d then assigns static node IPs (its `--subnet` is marked experimental, so this is opt-in).

Open `http://aiopen.localhost` once `k3s-verify` passes — same login flow as the docker-compose
setup. `ui-react`'s bundled default for `VITE_PLATFORM_REGISTRY_URL` is `http://localhost:8010`
(matching docker-compose.yml's `127.0.0.1:8010` host-published port), so the browser needs a
standing port-forward to `platform-registry`'s loopback-only API — unlike `k3s-verify`'s own
port-forward, which only lives for that one command. `make k3s-wait` (and therefore `make
k3s-all`/`make k3s-resume` + `k3s-wait`) starts this automatically via `make k3s-portforward`,
tracking its PID in `/tmp/k3s-portforward-<cluster>.pid` so re-running it doesn't stack duplicate
forwards on the same port; `make k3s-pause`/`make k3s-down` stop it again. Run `make
k3s-portforward` yourself only if you need to restart it without a full `k3s-wait` (e.g. after it
died for some other reason).

If login still fails client-side with a generic `Failed to fetch` (the `/llm-settings/
active-model` call gets `ERR_CONNECTION_REFUSED`), check the browser devtools Network tab and
confirm the port-forward is actually running (`ss -tlnp | grep 8010` or check
`/tmp/k3s-portforward-<cluster>.log`) — `make k3s-verify` only exercises curl-reachable Traefik
routes and won't catch this class of failure.
Re-run `make k3s-secrets` any time `.env`/`infra/traefik/console.htpasswd`/
`infra/seaweedfs/s3.json` change; re-run `make k3s-build k3s-import` and
`kubectl -n ai-circus rollout restart deployment/<service>` after code changes.

To actually tear down (not just pause — see above): `make k3s-down` deletes the applied manifests
(StatefulSet PVCs are retained by k8s convention), or `k3d cluster delete ai-circus` for a full
wipe including all volumes.

## Verified

The full `k3s-cluster` -> `k3s-verify` -> `k3s-pipeline` sequence above, plus a real browser
session (login, an ML prediction with SHAP, and a RAG chat turn), all pass against this manifest
set on a local k3d cluster:

| Login | ML prediction (SHAP) | RAG chat |
| --- | --- | --- |
| ![Login](../docs/screenshots/k3s-login.png) | ![ML prediction](../docs/screenshots/k3s-ml-predictions.png) | ![RAG chat](../docs/screenshots/k3s-rag-chat.png) |

## Design notes

- **Secrets are never committed.** `make k3s-secrets` (`scripts/k3s_generate_secrets.sh`) creates
  them from local, gitignored files — the same rule `.env` itself follows. `.env` stays the single
  file you edit, but the script fans it out into one small Secret per workload (`postgres-
  credentials`, `prediction-secrets`, `rag-agent-secrets`, ...), each containing only the keys that
  workload's docker-compose.yml `environment:` block actually uses — not one `app-env` blob handed
  to every pod, so compromising one service doesn't leak every credential in the system. Each pod's
  `envFrom` points at its own Secret, plus a small number of explicit `env:` overrides for keys
  docker-compose.yml itself renames (e.g. `LLM_GATEWAY_API_KEY` from `.env`'s `LITELLM_MASTER_KEY`)
  or that must be a k8s-internal literal (e.g. `KEYCLOAK_JWKS_URL`).
- **`./scenarios` is a `hostPath` volume**, mounted at `/app/scenarios` in every pod that needs
  it — the k8s-native equivalent of docker-compose.yml's read-only bind mount, viable here because
  this is single-node local dev. `make k3s-cluster` bind-mounts the repo's `scenarios/` directory
  into every k3d node at `/scenarios` for this to resolve.
- **Traefik `IngressRoute`/`Middleware` CRDs**, not plain `Ingress` — k3s ships Traefik already,
  and its CRDs let the `Host(...)` rules and the `admin-basicauth` gate (on `admin.keycloak.localhost`
  and `console.objectstore.localhost`) carry over almost verbatim from docker-compose.yml's own
  Traefik labels.
- **`securityContext.runAsNonRoot: true` always needs `runAsUser: 1000` alongside it**, for every
  `services/*` Deployment/Job. Each of those Dockerfiles sets `USER app` (a name, not a UID), and
  the kubelet can't verify "non-root" from a name alone — it refuses to start the container with
  `Error: container has runAsNonRoot and image has non-numeric user (app), cannot verify user is
  non-root`, surfacing as `CreateContainerConfigError` (a *different* root cause than the missing-
  secret-key version of that same status — see the `k3s-deploy-verify` skill's gotchas). `1000` is
  the UID `useradd --create-home` assigns `app` in every one of those Dockerfiles (confirmed via
  `docker run --rm <image> id`); `postgres`/`keycloak`/`qdrant`/`seaweedfs`/`ui-react`/`agui-voice`
  deliberately skip `runAsNonRoot` instead (see their manifests' comments) since they either need
  root for entrypoint chown/bind logic or (`agui-voice`) have no non-root `USER` yet.
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
- **The observability stack (`langfuse.yaml`, `mlflow.yaml`) is in the base, always on, and is
  the only place besides `keycloak.yaml` that sets `resources.limits`** — Langfuse v4 needs
  web + worker + ClickHouse, and on a laptop-class node the way to keep that affordable is to reuse
  the existing Postgres (`langfuse`/`mlflow` databases), Valkey (`langfuse:` key prefix) and
  SeaweedFS (`langfuse`/`mlflow` buckets), cap ClickHouse at 1 GiB with a low-memory `config.d`,
  and cap the two Langfuse pods and MLflow too. Expect roughly 1.5–2 GB more resident memory than
  before; on WSL check `.wslconfig`'s memory (see `docs/windows-wsl.md`) before `make k3s-all`.
  Both manifests carry an `ensure-database` init container (idempotent `CREATE DATABASE`) because
  `postgres.yaml`'s init script only ever runs on a fresh volume. `data-platform-manager`'s Role
  additionally lists pods (read-only) so the admin Platform dashboard can show readiness/restarts —
  the same dashboard works on docker-compose, minus that pod detail. The MLflow image is built by
  `make k3s-build` from `infra/mlflow/Dockerfile` (not `services/*` — it isn't a cookiecutter
  service, just the official MLflow with a Postgres driver and boto3 added).
- **SeaweedFS runs with `-master.volumePreallocate=false`** (same in `docker-compose.yml`).
  Without it, `weed server` 3.97 `fallocate()`s 1 GiB per volume and grows 7 volumes per S3
  bucket — one bucket per scenario — so a fresh install "uses" ~70 GB of disk for ~15 MB of
  datasets and models, inside the k3d node's docker volume (and, on WSL, the Windows `.vhdx`).
  A PVC created before this flag still holds that preallocation — the `k3s-deploy-verify` skill's
  Gotcha 7 has the data-preserving reclaim procedure (stop the StatefulSet, shrink-truncate the
  `.dat` files, start it again).
- **`rag-agent`/`form-agent` readiness/liveness probes are deliberately loose**
  (`timeoutSeconds: 5`, `failureThreshold: 6`) — their FastAPI startup makes a live call to
  `llm-gateway` (embedding dimension probe), which queues behind every other scenario service
  doing the same thing during a cold `k3s-up` on a single-node cluster. The default 1s probe
  timeout flakes under that concurrent cold-start load and CrashLoopBackOffs the pod even though
  the app itself starts fine.
