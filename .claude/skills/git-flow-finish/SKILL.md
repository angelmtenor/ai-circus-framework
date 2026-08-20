---
name: git-flow-finish
description: Prepare the correct git-flow finish commands (feature/release/hotfix) for ai-circus-framework — always surfaced for human approval, never run unprompted.
version: 1.0.0
---

# Git-flow Finish

## Overview

ai-circus-framework uses git-flow (AVH edition): `main` (production, tagged releases) and
`develop` (integration) are permanent; `feature/*`, `release/*`, `hotfix/*` are ephemeral. This
skill codifies the exact finish commands from root `AGENTS.md` §5 so they're prepared correctly
and consistently — but preparing the command is as far as this skill goes on its own.

## When to use

- A feature/release/hotfix branch is ready to merge back.
- The user asks to "finish" a branch, cut a release, or land a hotfix.

## Hard rule — read this first

Root `AGENTS.md` §3 (Human-in-the-Loop Protocol) is STRICTLY MANDATORY and applies directly here:
**never run `git push`, `git flow feature finish`, `git flow release finish`, or
`git flow hotfix finish` unprompted.** This skill's job is to prepare the exact command (including
the version/tag for a release) and present it — the human operator runs it or explicitly approves
running it. Do not treat a prior approval of one finish command as blanket approval for future
ones.

## Workflow

1. Check `git flow` is actually available before assuming the commands below work — it is used by
   convention in this repo but **nothing installs it automatically** (no Dockerfile/devcontainer/
   README step provisions it):
   ```bash
   git flow version
   ```
   If it's missing, fall back to the plain-git equivalents in "Fallback without git-flow" below
   rather than telling the human operator to install a tool they may not want.

2. Confirm the branch type and current state (`git status`, `git branch --show-current`) before
   proposing a command — pick the matching case:

   **Feature** (branch from `develop` as `feature/<name>`, merges back into `develop` only —
   never straight into `main`):
   ```bash
   git flow feature finish <name>
   ```

   **Release** (cut from `develop`, merges into both `main` and `develop`, tags it — versioning
   follows SemVer):
   ```bash
   git flow release start vX.Y.Z   # if not already started
   git flow release finish vX.Y.Z
   ```

   **Hotfix** (branch from `main` for a production-only fix, merges into both `main` and
   `develop`, tags it):
   ```bash
   git flow hotfix finish <name>
   ```

3. Present the exact command(s) to the human operator and wait for explicit approval before
   running. If a release/hotfix, also state the resulting tag so they can confirm the version is
   correct before it's cut.

## Fallback without git-flow (if step 1 shows it's not installed)

- Feature: `git checkout develop && git merge --no-ff feature/<name> && git branch -d feature/<name>`
- Release: `git checkout main && git merge --no-ff release/vX.Y.Z && git tag vX.Y.Z && git checkout develop && git merge --no-ff release/vX.Y.Z && git branch -d release/vX.Y.Z`
- Hotfix: `git checkout main && git merge --no-ff hotfix/<name> && git tag vX.Y.Z && git checkout develop && git merge --no-ff hotfix/<name> && git branch -d hotfix/<name>`

These are still subject to the same approval gate — never run them unprompted either.

## Key rules

- Always surface, never auto-run, the finish/push commands.
- Feature branches merge to `develop` only. Release/hotfix branches merge to both `main` and
  `develop` and produce a tag.
- Don't assume `git flow` is installed — verify with `git flow version` first.

## References

- Root `AGENTS.md` §3 (Human-in-the-Loop) and §5 (Branching Strategy).
- `styleguide.md` — Conventional Commits format used for commit messages on these branches.
