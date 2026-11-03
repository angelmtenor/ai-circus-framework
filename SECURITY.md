# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting: on the repository page choose **Security → Advisories →
Report a vulnerability**. Include what you found, how to reproduce it (a failing request, a
scenario file, a commit), and the impact you expect. Reports are handled on a best-effort basis
by the maintainer; please give a reasonable time to respond and fix before disclosing publicly.

## Supported versions

Security fixes land on `develop` and are released from there (git-flow, see
[`CONTRIBUTING.md`](CONTRIBUTING.md)). Only the latest release and `develop` are supported.

## What is in scope

The platform services under `services/`, the shared library in `libs/shared`, the `ui-react`
frontend and the manifests in `k8s/`, `docker-compose.yml` and `infra/`. Of particular interest:

- tenant isolation — anything that lets one `org_id` read or write another's scenario data,
  model artifacts, vectors, cache entries or data-product rows;
- entitlement and authentication bypasses;
- role restrictions inside a tenant (data-product row filters and hidden columns);
- code execution through model artifacts, uploaded files or scenario content.

## Defaults you must change before exposing anything

The repository ships **demo credentials on purpose** so that `make k3s-all` works out of the
box on a laptop. They are public and must never face a network you do not control:

- `ADMIN_API_KEY`, `ENGINEERING_DEMO_API_KEY`, the Keycloak admin/owner passwords and every
  `change-me` value in `.env.example`;
- `infra/traefik/console.htpasswd.example` (the admin consoles' basic-auth);
- `AUTH_DISABLED` must stay `false`.

Set `DEPLOYMENT_TARGET=public` in your own `.env` before a public deployment: services then
refuse to start while any of the above still has its shipped value. Optionally set
`MODEL_SIGNING_KEY` (see `.env.example`) so `prediction` only loads models that `training`
signed, and review the Keycloak realm (`sslRequired`, redirect URIs) for your hostnames.

## Scope of the claims

The framework provides building blocks (tenant scoping, audit trail, governance events). It is a
reference platform, not a certified product, and nothing here is a statement of legal compliance.
