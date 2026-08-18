# 01 — Fundamentals & Toolchain (Language-Agnostic Basics)

The baseline tools every project in this repo is built on, independent of ML or GenAI
specifics. Get comfortable with these before moving on to
[02-software-engineering.md](02-software-engineering.md).

## 0. Development Environment (Do This First)

**Principle:** the development environment should mirror the production environment.
Production is Linux, so development should be too — this avoids the classic "works on my
machine" drift between teammates.

### VS Code (install first, on the host)

If you don't already have it, install [VS Code](https://code.visualstudio.com/download) on your
**host machine** (Windows/macOS) — not inside WSL or the remote VM. Then, depending on where your
Ubuntu environment lives, add the matching extension to connect to it:

* **WSL:** [Remote - WSL extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-wsl)
  — Command Palette → "WSL: Connect to WSL".
* **Remote VM:** [Remote - SSH extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh)
  — Command Palette → "Remote-SSH: Connect to Host...".

### Do you need a dedicated Linux environment?

* **macOS:** No — already Unix-based. Skip straight to [Python](#python) below.
* **Native Linux:** No — you're already there.
* **Windows:** Yes, strongly encouraged. Production environments won't be Windows, so working
  directly in Windows creates avoidable dev/prod skew. Use **WSL** (Windows Subsystem for
  Linux) — admin permissions are required to enable it. Official instructions:
  [Install WSL – Microsoft Learn](https://learn.microsoft.com/en-us/windows/wsl/install).

### Options to get an Ubuntu 26.04 (minimal) environment

Recommended base: **Ubuntu 26.04 minimal**, whichever way you provision it.

1. **WSL** (Windows only) — install per the link above, then treat it like a native Linux
   machine for everything below.
2. **Remote VM** (on-prem or cloud) — provision an Ubuntu 26.04 base VM and connect over SSH.
   Works the same on AWS, Azure, GCP, or on-prem infra.
3. **VS Code Dev Container** — open this repo's folder in VS Code and let it build
   `.devcontainer/Dockerfile` (Ubuntu 26.04-based) for you; see
   [VS Code Dev Containers](https://code.visualstudio.com/docs/devcontainers/containers).
   More decoupled from the host, but generally slower to start/rebuild than a directly
   provisioned machine — use it when isolation matters more than speed.

### Provisioning the machine (WSL / native Linux / remote VM)

Once you have an Ubuntu 26.04 (minimal) machine or session — WSL, bare metal, or a cloud/on-prem
VM reached via SSH — run this repo's setup scripts from `.devcontainer/`:

1. `sudo ./.devcontainer/setup_sudo.sh` — one-time, root-level system setup (packages, timezone,
   optional GPU/CUDA support).
2. `source .devcontainer/setup_user.sh` — per-user setup (git config, `uv`, Node via nvm, shell
   prompt). Must be *sourced*, not executed.
3. Install **Docker** — [Docker Install Guide (Ubuntu)](https://docs.docker.com/engine/install/ubuntu/)
   (not covered by the scripts above; see the [Docker](#docker) section below for the link again).

Both scripts are idempotent (safe to re-run) and have been tested on AWS, Azure, GCP, on-prem
servers, WSL, and native Linux — same result everywhere.

### Objective

A reproducible environment with no differences between team members, whether they're on WSL,
a native Linux box, a remote VM, or a Dev Container. Once the base OS is ready, proceed with
the Python project setup below (Docker, uv, ruff, etc.).

## Python

* [W3Schools Python Tutorial](https://www.w3schools.com/python/default.asp) — basic Python
  syntax/reference. W3Schools also covers most other mainstream languages, so it's worth
  bookmarking beyond Python too.
* **Package manager: uv** (installer/resolver) — [uv Documentation](https://docs.astral.sh/uv)
* **Linter & formatter: ruff** (fast, replaces black/flake8/isort) — [ruff Documentation](https://docs.astral.sh/ruff)

## Unix / Shell

* [TutorialsPoint UNIX Quick Guide](https://www.tutorialspoint.com/unix/unix-quick-guide.htm)

## VS Code

* [VS Code Documentation](https://code.visualstudio.com/docs)

## Docker

* [Docker Install Guide (Ubuntu)](https://docs.docker.com/engine/install/ubuntu/)

## Build Automation: Makefile

* [Creating a Python Makefile – Earthly Blog](https://earthly.dev/blog/python-makefile/) —
  covers targets, `.PHONY`, variables, and `venv`/test/lint/clean rules using Python examples
  (not C/C++).

## QA / Code Quality: pre-commit

* [pre-commit Documentation](https://pre-commit.com) — manages and runs hooks (ruff, formatting,
  secret detection, etc.) before every commit.

## Project Scaffolding: cookiecutter

* [cookiecutter Documentation](https://cookiecutter.readthedocs.io/)
* Use reproducible templates that bundle pre-commit, a Makefile, and unified tooling out of
  the box, so every new project starts standardized instead of copy-pasted.
* **This repo (AI Open Framework) is being groomed into exactly such a template** — see
  [00-itinerary.md](00-itinerary.md).
