# SKILLS.md - etl_vectorize Architecture & Coding Standards

This document outlines the specific domain knowledge, coding conventions, and architectural
patterns required for the `etl_vectorize` Python project. Agents must adhere to
these standards when writing or refactoring code.

## 1. Configuration & State Management
**Single Source of Truth:** All application settings MUST be defined in `settings.yaml`.
- **Synchronization Workflow:** When you need to add or change a configuration setting:
  1. Update `settings.yaml`.
  2. Run `make generate-data-model` to sync `src/etl_vectorize/data_model.py` and `.env.example`.
- **Prohibition:** NEVER define redundant `Settings` classes or load `.env` files manually using `dotenv` for core app logic.
  - **✅ Good:** `config = etl_vectorize.get_env_config()`
  - **❌ Bad:** `load_dotenv(); os.getenv("SOME_VAR")`

## 2. Simplified Public API & Architecture
- **Package Root Imports:** Always prefer importing core functions directly from the package root.
  - **✅ Good:** `from etl_vectorize import get_env_config, get_logger`
  - **❌ Bad:** `from etl_vectorize.core.logger import get_logger`
- **Core vs. Tools:** Infrastructure (logging, system info, the settings generator) lives in `core/`.
  Example CLI utilities live in `tools/` and are registered under `[project.scripts]` in
  `pyproject.toml`.

## 3. Scripting, Makefile, & Portability
- **Makefile Constraints:** Target bodies in the `Makefile` must be kept short to satisfy the `checkmake` linter. Delegate any complex logic to dedicated Python scripts in a `scripts/` directory.
- **Safe Redirection:** Never use `sed -i` as it is non-portable between GNU and macOS/BSD.
  - **✅ Good:** `sed '...' file > file.tmp && mv file.tmp file`
- **Preservation:** The `make clean` target must preserve dated `.env` backups in the `backups/` folder.

## 4. Git Workflow & Hygiene
- **Branching Conventions:** Use descriptive branch names grouped by intent:
  - `feature/short-description`
  - `fix/issue-description`
  - `docs/update-description`
- **Commit Standards:** Adhere to the Git commit message style defined in `styleguide.md` (Conventional Commits).

## 5. Dependency Management
- **Tooling:** This project uses `uv` for fast, reliable Python package management.
- **Workflow:**
  - ALWAYS use `uv add <package>` or `uv remove <package>` to modify dependencies.
  - NEVER use `pip install` directly.
  - After changing dependencies, run `make setup` to ensure the local environment and `pyproject.toml` are in sync.

## 6. Ruff / Python Version Notes
- **Unparenthesized `except`:** On Python 3.14+ (PEP 758), `ruff format` may rewrite
  `except (A, B):` to `except A, B:`. This is valid syntax (an unparenthesized exception
  tuple), not a Python 2 leftover — do not "fix" it by adding `as` or reintroducing parens
  by hand; let the formatter own this.

## 7. Documentation Responsibility
- **Docstring Accuracy:** Keep docstrings updated. Every new file must include the standard header: `Author: ai-circus-framework contributors`.
- **Module Exports:** Ensure `src/etl_vectorize/__init__.py` properly exports all new public components via `__all__ = [...]`.
- **README Updates:** If the onboarding workflow, CLI tools, or `Makefile` targets change, you must update the "Quick Start" or "Common Workflows" sections in `README.md`.
