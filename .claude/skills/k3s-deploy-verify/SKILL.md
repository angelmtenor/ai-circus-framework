---
name: k3s-deploy-verify
description: Deploy and verify ai-circus-framework on the local k3d/k3s cluster end-to-end (cluster up through a real browser check) — includes sandbox-specific setup and the known k3s-vs-compose gotchas found doing this the first time.
version: 1.3.0
---

# k3s Deploy & Verify

## Overview

`k8s/README.md` documents the `make k3s-*` workflow itself. This skill is the operational
runbook for actually driving that workflow end-to-end in an agent sandbox where `kubectl`/`k3d`
usually aren't preinstalled, plus nine gotchas that look like real bugs but are really
compose-vs-k3s environment gaps — found and fixed once already; check here before re-diagnosing
them from scratch.

## When to use

- Asked to deploy/test the platform on k3s/k3d/Kubernetes instead of (or in addition to)
  docker compose.
- A `make k3s-*` step fails in a way that looks like an app bug but might be one of the gotchas
  below.
- Verifying a change actually works by driving the real UI against a k3s deployment (predictions,
  chat, login) — pairs with `playwright-headless-verify` for the browser part.

## Setup: kubectl/k3d without sudo

Sandboxes running this repo often have Docker but not `kubectl`/`k3d`. Both install to
`~/.local/bin` (already on `PATH` in this repo's dev environments) with no root needed:

```bash
KVER=$(curl -sSL https://dl.k8s.io/release/stable.txt)
curl -sSL -o ~/.local/bin/kubectl "https://dl.k8s.io/release/${KVER}/bin/linux/amd64/kubectl"
chmod +x ~/.local/bin/kubectl

curl -sSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | \
  USE_SUDO=false K3D_INSTALL_DIR=~/.local/bin bash
```

## Workflow

1. Stop any docker-compose stack first (`make down`) — it and k3d's Traefik both want host port
   80.
2. Run the `k8s/README.md` sequence in order: `k3s-cluster` -> `k3s-build` -> `k3s-import` ->
   `k3s-secrets` -> `k3s-up` -> `k3s-wait` -> `k3s-verify` -> `k3s-pipeline`. **`k3s-pipeline` is
   required, not optional** — see Gotcha 9 below; a fresh/recreated cluster has zero trained
   models and every `tabular_ml` scenario 503s on its first real prediction until it runs, even
   though `k3s-verify` passes either way. `k3s-build`/`k3s-import` are the slow steps (image builds, then a full `docker save`/import
   cycle per image) — run them with a long timeout or in the background. If `k3s-cluster` found an
   *existing* cluster (paused or already running — check `k3d cluster list`'s `SERVERS` column, and
   see `k8s/README.md`'s pause/resume section), its pods were NOT freshly created by `k3s-up` and
   will keep running whatever image content they already had — see Gotcha 5. After
   `k3s-build`/`k3s-import` in that case, explicitly `kubectl -n ai-circus rollout restart
   deployment/<service>` for every service you rebuilt before trusting anything you test against
   the cluster.
3. `make k3s-wait` now auto-starts a standing port-forward to `platform-registry` (via `make
   k3s-portforward`, PID-tracked so it doesn't stack duplicates) — see Gotcha 2 below. If a real
   browser check ever hits `Failed to fetch` anyway, confirm it's actually running
   (`ss -tlnp | grep 8010`) and re-run `make k3s-portforward` if not, rather than assuming an app
   bug.
4. **Do the real-browser check — `k3s-verify` passing is not sufficient to call this done.**
   `k3s-verify`'s curl checks structurally cannot see client-side-only failures (a missing
   port-forward, CORS, JS console errors) — see Gotcha 2. This was actually missed once already:
   an agent ran only `k3s-verify`, reported success, and the user had to ask "did you check with
   playwright?" before the (real) `Failed to fetch` from Gotcha 2 surfaced. Don't repeat that —
   treat step 4 as mandatory, not an optional nice-to-have, any time the task is "verify this
   works," not just "does the curl smoke test pass."
   Use the `playwright-headless-verify` skill to drive a real headless browser against
   `http://aiopen.localhost`. Log in with the bearer-token shortcut (not real Keycloak OIDC): the
   `User` dropdown already defaults to `admin`; fill the password field
   (`input[type="password"]`) with the `ADMIN_API_KEY` value from `.env` (see that skill for how
   to do this without ever printing `.env`'s content), then click the button matching
   `button:has-text("Log in")`. A successful login lands on the scenario dashboard with no failed
   requests; from there, open a scenario card (e.g. `text=Customer Churn Prediction`) and confirm
   its data/charts actually render — a `Loading dataset…` state that clears within ~10s is normal
   render time for a 5,000-row sample, not a bug.

## Gotchas (compose-vs-k3s environment gaps, not app bugs)

1. **Optional env vars with a compose shell-default aren't optional in k8s.** Anything referenced
   in `docker-compose.yml` as `${VAR:-default}` has no equivalent fallback when a k8s manifest
   pulls it via `secretKeyRef` — the key must actually exist in `.env`, or the pod fails
   `CreateContainerConfigError` with `couldn't find key <VAR> in Secret app-env`. Fix: copy the
   missing default line(s) from `.env.example` into `.env` (never print/inspect `.env` itself —
   existence-check with `grep -q "^KEY=" .env`, append blind), then re-run `make k3s-secrets` and
   restart the pod.
2. **`platform-registry`'s browser-facing port isn't published in k3s the way it is in compose.**
   `ui-react` calls `http://localhost:8010` directly for one endpoint
   (`VITE_PLATFORM_REGISTRY_URL`'s default) — compose satisfies this via
   `127.0.0.1:8010:8000` on the host; k3d has no equivalent, and `make k3s-verify`'s own
   port-forward only lives for that one command. `make k3s-wait` now starts a standing one
   automatically (`make k3s-portforward`, PID file at `/tmp/k3s-portforward-<cluster>.pid`,
   stopped again by `k3s-pause`/`k3s-down`) — this used to require a manual `kubectl -n ai-circus
   port-forward svc/platform-registry 8010:8000 &` before every browser session; if you ever land
   in an environment/version of this repo without that automation, that manual command is the
   fallback. Without it, login fails client-side with a generic `Failed to fetch` even though
   every Traefik-routed service (and `k3s-verify`'s curl checks) are fine — check the browser
   devtools Network tab and `ss -tlnp | grep 8010`, not just `k3s-verify`, to catch this class of
   failure. This is also a real portability gap for an actual remote/cloud/OpenShift target
   (`localhost` there means the viewer's own machine) — flag it rather than silently working
   around it if the task is about deploying somewhere other than local k3d.
3. **A `securityContext.runAsNonRoot: true` container fails with a *different* flavor of
   `CreateContainerConfigError` than Gotcha 1's.** `kubectl describe pod` shows `Error: container
   has runAsNonRoot and image has non-numeric user (app), cannot verify user is non-root` — the
   Dockerfile's `USER app` is a name, not a UID, so the kubelet can't verify it's non-root at all
   and refuses to start. Fix: add `runAsUser: 1000` next to `runAsNonRoot: true` (1000 is the UID
   `useradd --create-home` assigns `app` in every `services/*/Dockerfile` — confirmed via
   `docker run --rm <image> id`; see `k8s/README.md`'s Design notes). This is unrelated to Gotcha
   1 even though both surface as `CreateContainerConfigError` — check the Events message to tell
   them apart before assuming it's the missing-secret-key case.
4. **Tight default health-probe timing can crash-loop a service that isn't actually broken**, if
   its startup makes a live network call (e.g. an embedding "dimension probe" to `llm-gateway`)
   that queues behind every other scenario service doing the same thing during a cold `k3s-up` on
   a single-node cluster. `rag-agent`/`form-agent` already carry a fixed `timeoutSeconds: 5,
   failureThreshold: 6` for this reason (see their manifests) — if a *new* service shows the same
   symptom (`kubectl describe pod` showing repeated `Liveness probe failed` / `connection refused`
   right after a clean `Application startup complete` log line), it's the same class of issue, not
   a fresh bug.
5. **`make k3s-build k3s-import` alone never updates an already-running pod**, even after a
   successful import — `kubectl apply` only triggers a rollout when the Deployment's *spec* text
   changes (it never does, since the image tag string `ai-circus/<service>:local` stays constant
   across rebuilds), so a pod that was already running keeps its original container/image content
   indefinitely. This bites hardest exactly on the `k3s-pause`/`k3s-resume` path (see below): the
   cluster's pods survive a pause/resume with their *original* images, so rebuilding+reimporting
   after resuming does nothing to them by itself. Confirmed root cause of a real bug once: a stale
   pre-existing `ui-react` pod (running code from days earlier, before a conversation-history
   feature existed) talked to a freshly-rebuilt `assistant`/`rag-agent`/`form-agent` backend that
   now required a real persisted conversation id first — surfaced in the browser as `HTTP 404:
   {"detail":"Conversation not found."}` on every chat send, which looks exactly like a backend
   bug but had nothing to do with the backend. Diagnose with `kubectl -n ai-circus get pods -o
   wide` — a pod whose `AGE` predates your `k3s-build` is running stale content regardless of what
   `k3s-import` just loaded. Fix: `kubectl -n ai-circus rollout restart deployment/<service>` for
   every service you rebuilt (or, blunter but reliable when several might be stale: restart all of
   them) — don't try to compare `docker inspect ai-circus/<service>:local --format '{{.Id}}'`
   against the pod's `imageID` to check staleness, they're different digest types (Docker image
   config digest vs. containerd manifest digest) and will never string-match even for identical
   content; pod age vs. build time is the reliable signal.

6. **A hard VM kill mid-`k3s-build` (WSL `--shutdown`/restart, host power loss) can leave
   *truncated files* in BuildKit's shared `uv-cache` mount — and every later `uv sync` copies
   them into its venv as if they were fine.** Symptom: a pod dies with exit code **135 (SIGBUS)**
   within seconds and *no log output* (buffered stdout is lost in the crash); `PYTHONFAULTHANDLER=1`
   shows the top frame inside `dlopen()` — a `.so` being mmapped past its truncated end. Seen for
   real once: `libllvmlite.so` at exactly 44 MiB instead of 178 MB in both `prediction` and
   `training` (shap → numba → llvmlite), after two WSL restarts during builds. ext4 had persisted
   the rename of the extracted wheel but not its data. Diagnose definitively — don't guess which
   images are affected — with `verify_records.py` next to this skill (checks every installed
   file's size/sha256 against its package `RECORD`):
   ```bash
   for svc in <every K3S_IMAGES entry>; do printf '%-24s' "$svc"; docker run --rm --entrypoint sh \
     -v "$PWD/.claude/skills/k3s-deploy-verify/verify_records.py:/verify.py:ro" "ai-circus/$svc:local" \
     -c 'cd /app/services/*/ && .venv/bin/python /verify.py' | head -3; done
   ```
   Fix: `docker builder prune -f --filter type=exec.cachemount` (plain `--no-cache` does NOT
   clear cache mounts — the corrupt wheel would be reused), then `docker build --no-cache` only
   the affected images, re-verify, `k3d image import` them, and `rollout restart` their
   deployments (Gotcha 5). Prevention: never `wsl --shutdown` / restart the VM while a build is
   running — the doc (`docs/windows-wsl.md`) says so; a clean `systemctl restart docker` only
   costs the in-flight build, not the cache.

7. **SeaweedFS silently pre-allocates ~70 GB.** `weed server` 3.97 `fallocate()`s **1 GiB per
   volume** (the master's grow log says `"preallocate":1073741824` even though
   `-master.volumePreallocate` reads as off) and grows **7 volumes per collection = per S3 bucket =
   per scenario**. Observed for real: 70 `.dat` files, 70 GB allocated, 14.6 MB of actual data —
   inside the k3d node's docker volume, so `df` inside WSL balloons and the Windows `.vhdx` with it.
   `k8s/base/seaweedfs.yaml` and `docker-compose.yml` now pass `-master.volumePreallocate=false`
   (tested: 0 bytes allocated on volume grow). A cluster/volume created *before* that flag still
   holds the preallocation; reclaim it without losing data — the space is entirely beyond each
   file's EOF, so a shrink-truncate frees it (ext4 ignores punch-hole past EOF; truncating to the
   same size is a no-op; extend by 1 byte then shrink back is what releases the blocks):
   ```bash
   kubectl -n ai-circus scale statefulset/seaweedfs --replicas=0 && kubectl -n ai-circus wait --for=delete pod/seaweedfs-0 --timeout=90s
   V=$(docker volume ls -q | while read v; do docker run --rm -v "$v:/v:ro" alpine sh -c 'ls /v/storage 2>/dev/null | grep -q seaweedfs && echo ok' | grep -q ok && echo "$v"; done)
   docker run --rm -v "$V:/v" alpine sh -c 'apk add -q coreutils; cd /v/storage/pvc-*seaweedfs*/ && for f in *.dat; do sz=$(stat -c %s "$f"); truncate -s $((sz+1)) "$f" && truncate -s "$sz" "$f"; done; stat -c %b *.dat | awk "{s+=\$1} END {printf \"allocated now: %.1f MB\n\", s*512/1024/1024}"'
   kubectl -n ai-circus scale statefulset/seaweedfs --replicas=1 && kubectl -n ai-circus rollout status statefulset/seaweedfs --timeout=120s
   ```
   Verify afterwards with `kubectl -n ai-circus rollout restart deployment/prediction` (forces a
   model reload from S3) and a real prediction.
   **This is not a WSL thing** — any machine (native Ubuntu included) that ran the platform before
   the flag landed has the same preallocation. On the **docker-compose** path the files sit at the
   root of the named volume `ai-circus-framework_seaweedfs-data`, so the same trick is:
   ```bash
   docker compose stop seaweedfs
   docker run --rm -v ai-circus-framework_seaweedfs-data:/v alpine sh -c 'apk add -q coreutils; cd /v && for f in *.dat; do sz=$(stat -c %s "$f"); truncate -s $((sz+1)) "$f" && truncate -s "$sz" "$f"; done; stat -c %b *.dat | awk "{s+=\$1} END {printf \"allocated now: %.1f MB\n\", s*512/1024/1024}"'
   docker compose up -d seaweedfs
   ```
   (`docker compose down -v` + `make pipeline` is the blunt alternative — everything in that
   volume is regenerated by the pipelines, but you lose any uploaded documents.) Diagnosis path that found it: `df -h /` vs
   `docker system df -v` → the one huge volume → `du` inside it via `alpine` → `stat -c %b` vs
   `%s` on a `.dat` (1 GiB of blocks for 688 bytes) → `filefrag -v` showing `unwritten,eof`
   extents → the master log's `volume grow … preallocate`.

8. **After a Docker/host restart, k3s can crash-loop with `failed to start networking: unable to
   initialize network policy controller: error getting node subnet: failed to find interface with
   specified node ip`.** Docker re-assigns IPs on the `k3d-<cluster>` network in whatever order
   the containers come up, so the server node and the load balancer can swap addresses
   (`172.19.0.3` ↔ `172.19.0.2` seen here after a `wsl --shutdown`). k3s persists the old node IP
   on the Node object (`k3s.io/internal-ip` and friends); its netpol controller looks for an
   interface with *that* IP, fails, and k3s exits — the k3d entrypoint restarts it forever.
   Symptoms: `kubectl` answers for a few seconds then `connection refused`; `docker inspect
   k3d-<cluster>-server-0` shows a climbing `RestartCount`; `make k3s-resume` dies with `node …
   is running=true in status=restarting`; every pod ends up `Pending`. Confirm with
   `docker logs k3d-<cluster>-server-0 2>&1 | grep -E "Shutdown request|NodeIPs changed"` — the
   `NodeIPs changed … oldNodeIPs=[…]` line tells you the IP the server *used to* have.
   Fix (no data loss): stop both containers and start them in the order that gives the server
   its old IP back — Docker hands out the lowest free address to whichever starts first:
   ```bash
   docker stop k3d-ai-circus-server-0 k3d-ai-circus-serverlb
   docker start k3d-ai-circus-serverlb && docker start k3d-ai-circus-server-0   # LB first → server gets .3 again
   docker inspect k3d-ai-circus-server-0 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
   ```
   Then `make k3s-portforward` (the standing port-forward died with the VM) and `make k3s-verify`.
   `make k3s-pause`/`k3s-resume` (`k3d cluster stop/start`) does **not** fix it — it starts the
   server first and reproduces the swap. Prevention: create the cluster with a pinned subnet so
   k3d assigns static node IPs — `make k3s-cluster K3S_SUBNET=172.28.0.0/16` (k3d marks
   `--subnet` experimental, hence opt-in; requires recreating the cluster). If this is bad enough
   to warrant `k3d cluster delete` + recreate rather than the two-container reorder, that recreate
   wipes every trained model along with the rest of the cluster's state — go straight to Gotcha 9,
   don't stop at `k3s-verify` passing.

9. **A freshly created (or freshly recreated) cluster silently has zero trained models — every
   `tabular_ml` scenario 503s on its first real prediction, and nothing before that point catches
   it.** `k3s-cluster` -> `k3s-build` -> `k3s-import` -> `k3s-secrets` -> `k3s-up` -> `k3s-wait` ->
   `k3s-verify` can all pass clean; `k3s-verify` only curl-checks that `prediction` is reachable
   and authenticated, never that any scenario actually has a trained model behind it (same blind
   spot as Gotcha 2, different cause). The failure shows up client-side instead, as a 503 from the
   prediction call itself:
   ```
   {"detail":"No trained model artifacts for scenario='<slug>' (org='<org>', fallback org='demo'
   also has none — has `training` run for it?)."}
   ```
   Seen for real right after recovering from Gotcha 8 by deleting and recreating the cluster —
   `k3s-verify` and a full browser login/navigation all passed, and the very next scenario
   prediction 503'd. Fix: `make k3s-pipeline` (trains **every** `tabular_ml` scenario in
   `SCENARIOS`, empty/unset = all — not just churn, despite older references to a "churn
   pipeline"; ~1 minute for the seeded sample datasets). For a `conversational_rag`/
   `assisted_form` scenario's document catalog (Qdrant), there's no `make k3s-*` wrapper yet —
   apply the Job directly:
   ```bash
   kubectl -n ai-circus delete job etl-vectorize --ignore-not-found
   kubectl apply -f k8s/jobs/etl-vectorize-job.yaml
   kubectl -n ai-circus wait --for=condition=complete job/etl-vectorize --timeout=300s
   ```
   Treat `k3s-pipeline` as a mandatory step of "the cluster is ready," not an optional extra —
   run it every time right after `k3s-verify`, especially after any cluster recreate, not just the
   first time you bring one up.

## Operational notes (things that had to be done on the machine, not in the repo)

- **Run the slow steps detached, not just backgrounded.** `k3s-build` (10–20 min cold),
  `k3s-wait`, `k3s-pipeline` were each killed at least once by a session ending or a WSL restart.
  `nohup setsid bash -c 'make k3s-build; echo "exit code: $?"' > ~/.cache/ai-circus-k3s-build.log 2>&1 < /dev/null &`
  survives the agent session (not a `wsl --shutdown`); tail the log for progress. Log under
  `~/.cache`, not `/tmp` — WSL wipes `/tmp` on restart.
- **A clean `systemctl restart docker` mid-build** (e.g. applying `/etc/docker/daemon.json`)
  fails the in-flight `docker build` with `failed to solve: Unavailable: error reading from
  server: EOF` — harmless, cache intact, just re-run. A WSL restart mid-build is *not* harmless
  (Gotcha 6).
- **`apt-get` lock held on a fresh Ubuntu boot** (`Could not get lock /var/lib/apt/lists/lock …
  held by process N (apt-get)`) — the first-boot `apt-daily`/unattended-upgrades timers. Don't
  kill it; `apt-get -o DPkg::Lock::Timeout=600` waits (`scripts/setup_sudo.sh` does this).
- **`wsl.exe`/`cmd.exe` from inside the distro suddenly fail with `cannot execute binary file:
  Exec format error`** — the `WSLInterop` binfmt registration dropped (seen right after starting/
  stopping *another* distro via `wsl.exe -d …`). Only affects calling Windows binaries from Linux;
  Docker/k3d unaffected. Re-register without a restart:
  `sudo sh -c 'echo ":WSLInterop:M::MZ::/init:PF" > /proc/sys/fs/binfmt_misc/register'`.
- **Silent crash with exit code 135 and empty logs → get a traceback first.** Copy the Job
  manifest to a scratch dir, add `env: [{name: PYTHONFAULTHANDLER, value: "1"}, {name:
  PYTHONUNBUFFERED, value: "1"}]`, rename it (`training-diag`), apply, read `kubectl logs` — the
  fault handler prints the Python and C stacks at the signal (this is how Gotcha 6 was pinned to
  `dlopen()` of `libllvmlite.so` in ~2 minutes). Delete the diag job afterwards.
- **`kubectl`/`k3d` in `~/.local/bin`** (see Setup above) is fine for `make k3s-*` — the Makefile
  only needs them on `PATH`; the README's `/usr/local/bin` install is the sudo-having equivalent.

## Key rules

- Diagnose with `kubectl -n ai-circus describe pod -l app=<service>` (Events section) and
  `kubectl -n ai-circus logs -l app=<service>` before assuming a crash-looping pod is an app bug —
  check the gotchas above first.
- Exit code 135 + empty logs is Gotcha 6 (truncated `.so` from a killed build), not an app
  bug — verify with `verify_records.py` before rebuilding blindly.
- `df` inside WSL jumping by tens of GB right after `k3s-up`/the pipeline is Gotcha 7 (SeaweedFS
  preallocation), not the images.
- `kubectl` flapping between answering and `connection refused` right after a host/Docker restart
  is Gotcha 8 (k3d node IP swap) — check `RestartCount` on the server container before anything else.
- A `tabular_ml` scenario 503ing with "No trained model artifacts" — on a cluster that otherwise
  looks healthy and passed `k3s-verify` — is Gotcha 9 (pipeline never ran, most often after a
  fresh/recreated cluster), not an app bug. Run `make k3s-pipeline` before trusting any prediction
  request against a cluster you just brought up or recreated.
- Never read/print `.env` content (root `AGENTS.md` §1) — use presence-only checks
  (`grep -q "^KEY=" .env`) when diagnosing or patching missing keys.
- A real browser check (via `playwright-headless-verify`) catches failures `k3s-verify`'s curl
  checks structurally cannot — CORS, client-side-only fetch targets, JS console errors. Treat it
  as a required step of "verify this works," not an optional extra (see Workflow step 4).

## References

- `k8s/README.md` — the manifests, `make k3s-*` command reference, and "Design notes" (which also
  documents gotchas 2 and 3 above in-place).
- `playwright-headless-verify` skill — the sandbox's real-browser verification workaround.
- Root `CLAUDE.md` — "Debugging 'Failed to fetch'" (the compose-side version of the same class of
  issue).
