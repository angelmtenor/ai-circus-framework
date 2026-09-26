---
name: k3s-deploy-verify
description: Deploy and verify ai-circus-framework on the local k3d/k3s cluster end-to-end (cluster up through a real browser check), plus the known k3s gotchas that look like app bugs but aren't (CreateContainerConfigError, stale pods after a same-tag import, exit 135, SeaweedFS preallocation, k3d node-IP swap, untrained models). Use for any deploy/verify on k3s or when a pod misbehaves.
version: 2.0.0
---

# k3s Deploy & Verify

`k8s/README.md` documents the `make k3s-*` workflow; this is the operational runbook for driving
it end-to-end, plus nine gotchas already diagnosed once — check here before re-diagnosing.

## Setup (only if `kubectl`/`k3d` are missing — both install without sudo)

```bash
KVER=$(curl -sSL https://dl.k8s.io/release/stable.txt)
curl -sSL -o ~/.local/bin/kubectl "https://dl.k8s.io/release/${KVER}/bin/linux/amd64/kubectl" && chmod +x ~/.local/bin/kubectl
curl -sSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | USE_SUDO=false K3D_INSTALL_DIR=~/.local/bin bash
```

## Workflow

1. `make down` any docker-compose stack first — it and k3d's Traefik both want host port 80.
2. Fresh cluster: `make k3s-all` (or `k3s-all-lite`) — cluster → build → import → secrets → up →
   wait. Existing cluster: `k3d cluster list`; paused → `make k3s-resume-lite`.
3. **Run the slow steps detached** — `k3s-build` (10–20 min cold), `k3s-pipeline` — a session end
   or WSL restart kills a foreground/background job (and a WSL restart mid-build corrupts the
   cache, Gotcha 6):
   `nohup setsid bash -c 'make k3s-build; echo "exit $?"' > ~/.cache/ai-circus-k3s-build.log 2>&1 < /dev/null &`
   (log under `~/.cache` — WSL wipes `/tmp`).
4. After any rebuild on an existing cluster: `make k3s-import`, then
   `kubectl -n ai-circus rollout restart deployment/<each rebuilt service>` (Gotcha 5).
5. `make k3s-pipeline` — **required on every fresh/recreated cluster** (Gotcha 9). For
   `conversational_rag`/`assisted_form` document catalogs (Qdrant):
   `kubectl -n ai-circus delete job etl-vectorize --ignore-not-found && kubectl apply -f k8s/jobs/etl-vectorize-job.yaml && kubectl -n ai-circus wait --for=condition=complete job/etl-vectorize --timeout=300s`
6. `make k3s-verify` (curl checks: admin + engineering-demo tenants, Traefik hosts, Langfuse,
   MLflow). `make k3s-wait` also starts a standing port-forward to platform-registry on
   `localhost:8010`, which the browser needs (Gotcha 2).
7. **A real browser check is mandatory** for "verify this works" — `k3s-verify` can't see
   CORS, client-side fetch targets or JS errors (this was missed once; the user had to ask).
   Use `playwright-headless-verify`: log in with `User=admin` + the `ADMIN_API_KEY` as password
   (never print `.env`), open a scenario, exercise the feature. `Loading dataset…` clearing within
   ~10s is normal.

Healthy-cluster signals: `kubectl -n ai-circus get pods` (all Ready, restart counts not
climbing), `kubectl top pods -n ai-circus` (each pod under its `resources.limits.memory`),
no tracebacks in `kubectl -n ai-circus logs deploy/<svc>`. App pods wait for Postgres/SeaweedFS/
llm-gateway at start-up (`ai_circus_shared.startup.wait_for`, logged as
`Waiting for <dep> … retrying`) instead of crash-looping — a few of those lines on a cold boot
are normal; a pod still waiting after ~2 min means that dependency is really down.

## Gotchas (k3s environment gaps, not app bugs)

1. **`CreateContainerConfigError: couldn't find key <VAR> in Secret <svc>-secrets`** — an
   env var compose gives a `${VAR:-default}` has no fallback in k8s. Copy the line from
   `.env.example` into `.env` (presence-check with `grep -q '^VAR=' .env`, never print `.env`),
   `make k3s-secrets`, restart the pod.
2. **Login "Failed to fetch" while every curl passes** — ui-react calls platform-registry
   directly at `http://localhost:8010`, which k3d only serves via the standing port-forward
   (`make k3s-portforward` → `scripts/k3s_portforward.sh`: the systemd user service
   `ai-circus-portforward-<cluster>`, enabled at boot + auto-restarted after a pod restart; only
   without user systemd a PID-file background process that dies with the VM). Stopped by
   `k3s-pause`/`k3s-down`. Check `ss -tlnp | grep 8010` and
   `journalctl --user -u ai-circus-portforward-ai-circus`. It's also a real portability gap for
   any non-local cluster — flag it rather than paper over it.
3. **`CreateContainerConfigError: container has runAsNonRoot and image has non-numeric user`** —
   a manifest with `runAsNonRoot: true` but no numeric `runAsUser`. Every service image's `app`
   user is UID 1000 (explicit `--uid 1000`), ui-react's nginx is 101, Keycloak's is 1000.
4. **A pod that dies during start-up.** Probes are a `startupProbe` (2s × 90 = 180s) gating
   readiness/liveness, so a slow boot is never liveness-killed; if a pod still restarts at boot,
   read `kubectl logs --previous` — an exception *after* the `Waiting for …` lines is a real bug
   or misconfiguration (wrong credentials are raised immediately, not retried). Don't loosen
   probes to hide it.
5. **`make k3s-build k3s-import` never updates an already-running pod** — the image tag
   (`ai-circus/<svc>:local`) doesn't change, so neither does the pod spec. `kubectl -n ai-circus
   get pods` AGE older than your build = stale content (once surfaced as a bogus
   `404 Conversation not found` from a days-old ui-react pod). Fix: `rollout restart`. Don't
   compare `docker inspect` IDs with the pod's `imageID` — different digest types, never equal.
6. **Exit code 135 (SIGBUS), no logs** — a VM kill mid-build left truncated files in BuildKit's
   uv cache mount, copied into the venv (seen: `libllvmlite.so` at 44 MiB of 178 MB). Diagnose
   every image with `verify_records.py` next to this skill:
   ```bash
   for svc in <K3S_IMAGES…>; do printf '%-24s' "$svc"; docker run --rm --entrypoint sh \
     -v "$PWD/.claude/skills/k3s-deploy-verify/verify_records.py:/verify.py:ro" "ai-circus/$svc:local" \
     -c 'cd /app/services/*/ && .venv/bin/python /verify.py' | head -3; done
   ```
   Fix: `docker builder prune -f --filter type=exec.cachemount` (`--no-cache` does NOT clear cache
   mounts), rebuild only the affected images with `--no-cache`, re-verify, import, restart. For a
   traceback in such a silent crash, run a copy of the Job/Deployment with
   `PYTHONFAULTHANDLER=1` (PYTHONUNBUFFERED is already set in every image).
7. **Disk usage jumps ~70 GB** — SeaweedFS 3.97 `fallocate()`s 1 GiB per volume, 7 volumes per
   bucket (= per scenario). Fixed by `-master.volumePreallocate=false` in both deploy paths; a
   PVC created before that still holds it. Reclaim without data loss (the space is past each
   file's EOF — extend by one byte then shrink back):
   ```bash
   kubectl -n ai-circus scale statefulset/seaweedfs --replicas=0 && kubectl -n ai-circus wait --for=delete pod/seaweedfs-0 --timeout=90s
   V=$(docker volume ls -q | while read v; do docker run --rm -v "$v:/v:ro" alpine sh -c 'ls /v/storage 2>/dev/null | grep -q seaweedfs && echo ok' | grep -q ok && echo "$v"; done)
   docker run --rm -v "$V:/v" alpine sh -c 'apk add -q coreutils; cd /v/storage/pvc-*seaweedfs*/ && for f in *.dat; do sz=$(stat -c %s "$f"); truncate -s $((sz+1)) "$f" && truncate -s "$sz" "$f"; done'
   kubectl -n ai-circus scale statefulset/seaweedfs --replicas=1 && kubectl -n ai-circus rollout status statefulset/seaweedfs --timeout=120s
   ```
   Compose path: same `truncate` loop on the root of volume `ai-circus-framework_seaweedfs-data`
   with `docker compose stop seaweedfs` around it. Not WSL-specific.
8. **After a Docker/WSL restart, k3s crash-loops (`failed to find interface with specified node
   ip`) and `kubectl` flaps** — Docker re-assigned the server and load-balancer IPs in swapped
   order. Confirm: `docker logs k3d-ai-circus-server-0 2>&1 | grep -E "NodeIPs changed"`. Fix
   (no data loss) — start the LB first so the server gets its old IP back:
   `docker stop k3d-ai-circus-server-0 k3d-ai-circus-serverlb && docker start k3d-ai-circus-serverlb && docker start k3d-ai-circus-server-0`,
   then `make k3s-portforward`. `k3s-pause`/`k3s-resume` reproduces the swap. Prevention: create
   the cluster with `K3S_SUBNET=172.28.0.0/16` (recreate = wipe; then Gotcha 9).
9. **A fresh or recreated cluster has zero trained models** — every `tabular_ml` scenario 503s
   ("No trained model artifacts … has `training` run for it?") though `k3s-verify` and login
   pass. `make k3s-pipeline` (~1 min, every tabular_ml scenario).

## One-off migrations already handled in the manifests

- **agui-voice** used to run as root; its `voice-model-cache` PVC holds root-owned weights. The
  `cache-ownership` init container `chown`s it to UID 1000 (a no-op afterwards). Compose uses a
  new volume `voice-model-cache-app` instead (the old one can be `docker volume rm`'d).
- **Keycloak** runs a pre-built image (`ai-circus/keycloak:local`, `infra/keycloak/Dockerfile`,
  realm baked in). A version bump migrates its Postgres schema forward on first boot — one-way.
  An orphaned `keycloak-realm-import` ConfigMap from before can be deleted.
- **ui-react** listens on 8080 (nginx-unprivileged); its Service still exposes port 80.

## Machine-level notes

- A clean `systemctl restart docker` mid-build just fails that build (`Unavailable: error
  reading from server: EOF`) — re-run. A WSL restart mid-build is Gotcha 6.
- `apt-get` lock held on a fresh Ubuntu boot = unattended-upgrades; wait with
  `-o DPkg::Lock::Timeout=600`, don't kill it.
- `wsl.exe`/`cmd.exe` → `Exec format error` = dropped WSLInterop binfmt:
  `sudo sh -c 'echo ":WSLInterop:M::MZ::/init:PF" > /proc/sys/fs/binfmt_misc/register'`.

## References

`k8s/README.md` (workflow + design notes), `playwright-headless-verify` (browser step),
`service-check` (Definition of Done), `docs/windows-wsl.md` (WSL memory/disk/Docker).
