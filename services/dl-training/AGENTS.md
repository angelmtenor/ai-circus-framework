# AGENTS.md - AI Agent Development & Verification Guide

This document defines the foundational mandates and operational workflows for AI coding agents
(Claude Code, GitHub Copilot, Gemini CLI, Codex, Cursor, or any other agent operating on this
repository). It is the single source of truth — tool-specific files (`CLAUDE.md`, `GEMINI.md`,
`.github/copilot-instructions.md`) are thin pointers back to this file. Adherence to these rules
is STRICTLY MANDATORY to ensure repository integrity, security, and quality.

## 🚨 1. Security & System Integrity (Priority Zero)
- **Credential Protection:** NEVER log, print, or commit secrets, API keys, or sensitive credentials.
- **Ignore Rules:** Files matching `.env*` (except `.env.example`) and the `backups/` directory MUST remain ignored in `.gitignore`. Do not modify `.gitignore` to allow these under any circumstances.
- **AI Context Exclusions:** AI agents MUST NEVER read, analyze, or include the contents of files matching: `.env`, `.env.*` (except `.env.example`), `*.pem`, `*.key`, `**/credentials*`, `**/secrets*`, or anything in `.cache/`. This applies even if the agent has filesystem access that bypasses `.gitignore`.
- **Pre-Commit Audit:** Before proposing a commit, you must verify that no sensitive data or temporary `.env` files are in the staged changes.

## 📚 2. Context Discovery Requirement
- **Domain Knowledge Router:** This file contains only workflows and guardrails. If your task involves writing, modifying, or reviewing code, tests, or Makefiles, **you MUST first read `SKILLS.md`** to understand the project's architecture, import rules, and configurations before generating your response.

## 👥 3. Human-in-the-Loop Protocol
- **Inspection Required:** Never commit code until the human operator has inspected the proposed changes and provided explicit validation.
- **No Pushing:** You are STRICTLY PROHIBITED from running `git push`. All updates to remote repositories must be performed manually by the user.
- **Confirmation Loop:** For every destructive or significant operation, explain the intent first, stop, and wait for approval.
- **Hand-off Phrase:** When you finish a code-generation or modification task, end your response with: *"Task complete. Please inspect the changes. Awaiting your approval to proceed."*

## ✅ 4. Verification is Mandatory
- **Definition of Done:** A task is NOT complete until `make check` (QA + Test) and `make run` (App Smoke Test) pass successfully.
- **End-to-End Pipeline:** For significant refactors or initial setups, run `make all` to verify the entire lifecycle: `clean` -> `setup` -> `check` -> `run`.
- **Test-Driven:** Every bug fix or feature implementation must include corresponding unit tests in the `tests/` directory. Use mocks (via `pytest.MonkeyPatch` or equivalent) to ensure tests are fast and deterministic.
