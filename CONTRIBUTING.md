# Contributing

Thanks for helping. This file is the short version; the rules each change is held to live in
[`AGENTS.md`](AGENTS.md) (security, workflow, verification) and [`styleguide.md`](styleguide.md)
(commit messages). Each `services/<name>/` and `ui-react/` also has its own `AGENTS.md`/`SKILLS.md`.

## Workflow

- **Branches** follow git-flow: `main` and `develop` are permanent, work happens on `feature/*`
  (from `develop`), `release/*` and `hotfix/*`. Nothing is pushed directly to `main` or `develop`;
  changes land through a pull request.
- **Commits** use [Conventional Commits](https://www.conventionalcommits.org/) with the types in
  `commitlint.config.mjs`, and the header must be **72 characters or fewer** (CI enforces it).
- **Never commit secrets.** `.env` is gitignored; `.env.example` holds placeholders only.

## Before you open a pull request

```bash
make check-all                      # libs/shared lint + tests, then `make check` in every service
cd ui-react && npm run build && npm run lint
```

After editing `libs/shared`, run `make sync-shared` so each service's environment uses the new copy.

Every bug fix comes with a test that fails without the fix. A change is not finished until it also
works on the local cluster — see the k3s workflow in [`k8s/README.md`](k8s/README.md)
(`make k3s-all`, `make k3s-pipeline`, `make k3s-verify`, then a look in the browser).

## Where things go

- **A new scenario** is a `scenarios/<slug>/scenario.yaml`, not new UI or service code.
- **A new service** is scaffolded with `make new-service NAME=<name>`; never hand-write its
  `pyproject.toml`, `Dockerfile` or `settings.yaml`. It also needs its k8s manifest, compose entry,
  secret spec and a row in the platform health dashboard (see `CLAUDE.md`).
- **Tenant data** is always scoped by `org_id`, and the entitlement check happens in the backend,
  never only in the UI.

## Reporting problems

Bugs and ideas: open an issue. Security problems: follow [`SECURITY.md`](SECURITY.md) instead.
