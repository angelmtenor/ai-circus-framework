---
name: k3s-deploy-verify
description: Deploy and verify ai-circus-framework on the local k3d/k3s cluster end-to-end (cluster up through a real browser check) — includes sandbox-specific setup and the known k3s-vs-compose gotchas found doing this the first time.
version: 1.2.0
---

# k3s Deploy & Verify

## Overview

`k8s/README.md` documents the `make k3s-*` workflow itself. This skill is the operational
runbook for actually driving that workflow end-to-end in an agent sandbox where `kubectl`/`k3d`
usually aren't preinstalled, plus five gotchas that look like real bugs but are really
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
   `k3s-secrets` -> `k3s-up` -> `k3s-wait` -> `k3s-verify` -> (optional) `k3s-pipeline`.
   `k3s-build`/`k3s-import` are the slow steps (image builds, then a full `docker save`/import
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
   `http://aiopen.localhost`. Log in with the bearer-token shortcut (not real Logto OIDC): the
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

## Key rules

- Diagnose with `kubectl -n ai-circus describe pod -l app=<service>` (Events section) and
  `kubectl -n ai-circus logs -l app=<service>` before assuming a crash-looping pod is an app bug —
  check the four gotchas above first.
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
