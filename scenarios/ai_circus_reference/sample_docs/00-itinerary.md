# Learning Itinerary

A structured path through this repo's reference notes: general-purpose tooling first, then
data science / ML, then GenAI. The goal is to internalize the practices behind **AI Open Framework**
itself, since this repo is the source material for a future **cookiecutter** template — so
the stack, structure, and conventions described here should end up as reusable defaults, not
one-off notes.

## Path

| # | File | Covers | Level |
|---|------|--------|-------|
| 1 | [01-fundamentals.md](01-fundamentals.md) | Dev environment (WSL/VM/Dev Container), Python, Unix/shell, VS Code, Docker, uv, ruff, Makefile, pre-commit, cookiecutter | Basics — tool/language agnostic |
| 2 | [02-software-engineering.md](02-software-engineering.md) | SOLID/DRY/KISS principles, dev standards, CI/CD, IaC, secrets, code review | Core practices |
| 3 | [03-machine-learning.md](03-machine-learning.md) | ML/DS concepts, workflow, explainability (SHAP/LIME), DS Python libraries | ML |
| 4 | [04-genai.md](04-genai.md) | GenAI/RAG/agents best practices, LangChain, MCP, evaluation, courses & repos | GenAI |

## How to use this

1. Work top to bottom if you're ramping up from scratch — each file assumes the previous one.
2. Otherwise jump straight to the file matching what you're doing (e.g. touching Docker/CI →
   file 1–2; building a RAG/agent feature → file 4).
3. Treat file 4's "Learning Resources & Reference Repos" section as the reading list before extending
   any GenAI code in this repo — it points at LangChain Academy, curated DeepLearning.AI
   short courses, and two well-structured reference repos (`openai/openai-python`,
   `langchain-ai/langchain`) worth mirroring conventions from.
