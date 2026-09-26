---
name: service-check
description: Definition of Done for any change in ai-circus-framework — per-service `make check`, `make check-all` for shared/cross-service changes, the ui-react build, then a real rebuild + redeploy + verify on the local k3s cluster (unit tests alone never count as done). Use before reporting a change as finished or proposing a commit.
version: 2.0.0
---

# Service Check (Definition of Done)

Root `AGENTS.md` §4 defines Done; this is the checklist. Work through it before saying a change
"works", and before proposing a commit (which still needs the human's inspection — §3).

## 1. Static + unit checks

| Changed | Run |
|---|---|
| `services/<name>/` | `cd services/<name> && make check` |
| `libs/shared/` | `make sync-shared`, then `make check-all` (it lints + tests `libs/shared` first, then every service) |
| `ui-react/` | `cd ui-react && npm run build && npm run lint` (the build is the typecheck) |
| `k8s/` | `kubectl apply --dry-run=server -k k8s/base` (and `-k k8s/deep-learning` if touched) |
| `docker-compose.yml` | `docker compose --env-file .env.example --profile '*' config -q` — and for a refactor, diff the rendered config before/after |

`make check` = pre-commit (ruff, ruff-format, pyrefly, gitleaks, checkmake), settings.yaml ↔
`data_model.py` drift, `uv audit`, then pytest with the service's coverage floor. All of it must
pass. A real finding (pyrefly error, CVE, coverage drop) gets a root-cause fix — never an ignore
rule or a lowered threshold. The one accepted exception pattern is a CVE with **no fixed release**,
suppressed with `uv audit --ignore-until-fixed <ID>` plus a comment explaining why the affected
code path isn't reachable (see `services/agui-voice/Makefile`'s `AUDIT_ACCEPTED`).

The pre-commit hooks may also normalize files you didn't touch (line endings, `noqa` comment
style) — that's expected; keep those changes, `make check` can't pass cleanly without them.

Every bug fix needs a regression test (service `AGENTS.md` §4).

## 2. Real deployment on k3s — not optional

Unit tests can't see wiring problems (a probe that never passes, a missing secret key, a
blocking call that stalls an event loop, CORS). With the cluster up (`k3d cluster list`; else
`make k3s-all-lite`, or `make k3s-resume-lite` if paused):

```bash
# Detached — a session end or WSL restart must not kill it (see k3s-deploy-verify, Gotcha 6)
nohup setsid bash -c 'make k3s-build; echo "exit $?"' > ~/.cache/ai-circus-k3s-build.log 2>&1 < /dev/null &
make k3s-import
kubectl -n ai-circus rollout restart deployment/<each changed service>   # same-tag imports never restart pods
kubectl -n ai-circus rollout status deployment/<svc> --timeout=180s
make k3s-verify
```

- Changed only one image? `docker build -f services/<svc>/Dockerfile -t ai-circus/<svc>:local .`
  + `k3d image import ai-circus/<svc>:local -c ai-circus` is much faster than `k3s-build`.
- Changed `k8s/` manifests: `make k3s-up` (applies `k8s/base`) — changed pod specs roll out by
  themselves.
- Changed `training`/`etl-*` or anything a model depends on: `make k3s-pipeline`.
- Check the pods, not just the curl: `kubectl -n ai-circus get pods` (no restarts climbing),
  `kubectl -n ai-circus logs deploy/<svc>` (no tracebacks), `kubectl top pods` (memory within
  its limit, if you touched memory).
- Anything UI-facing, or "does it work end to end": a real headless-browser pass with the
  `playwright-headless-verify` skill (login → scenario → the feature, golden path + one edge case).

## Key rules

- `make check` passing is necessary, not sufficient.
- Never claim a feature works from type-checks/unit tests alone if it wasn't exercised live.
- Report failures faithfully with their output; say explicitly which steps you skipped.
