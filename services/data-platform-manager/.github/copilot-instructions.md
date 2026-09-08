# GitHub Copilot Project Instructions

This project uses a split-context architecture for AI agents. Load the appropriate context files based on the task:

1. **Mandatory Rules (`AGENTS.md`)**: Read and adhere to `AGENTS.md` for every single task. It contains strict security, workflow, and Git mandates (human-in-the-loop, no `git push`, verification via `make check`/`make run`).
2. **Coding Standards (`SKILLS.md`)**: If the task involves writing, refactoring, or reviewing Python code, Makefile targets, or tests, read `SKILLS.md` before generating a response.
