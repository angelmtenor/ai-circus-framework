"""
- Title:    Guard: every model call goes through llm-gateway
- Author:   Angel Martinez-Tenor

Governance (budgets, policy decisions, tracing, per-tenant attribution) is enforced in one
place, llm-gateway. That only holds if no other service can reach an LLM provider on its own,
so this test fails when code outside llm-gateway:

- imports a provider SDK or litellm,
- names a provider's API host, or
- builds an OpenAI-compatible client (`ChatOpenAI`, `OpenAI`, …) without an explicit gateway
  `base_url`, which would silently default to api.openai.com.

Speech vendors (agui-voice's Deepgram / ElevenLabs / Cartesia STT-TTS) are not LLM providers
and are out of scope here — see docs/governance/policy.md "What the chokepoint covers".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_DIRS = [*sorted((_ROOT / "services").glob("*/src")), _ROOT / "libs" / "shared" / "src"]
_GATEWAY_SRC = _ROOT / "services" / "llm-gateway" / "src"

_FORBIDDEN_IMPORTS = {
    "openai",
    "anthropic",
    "litellm",
    "cohere",
    "mistralai",
    "groq",
    "voyageai",
    "google.genai",
    "google.generativeai",
    "vertexai",
    "ollama",
}
_PROVIDER_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.groq.com",
    "api.deepseek.com",
    "openrouter.ai",
    "api.voyageai.com",
    "api.cohere.com",
    "api.mistral.ai",
)
# Clients that default to a provider host unless told otherwise.
_CLIENT_CONSTRUCTORS = {"ChatOpenAI", "OpenAI", "AsyncOpenAI", "AzureOpenAI", "OpenAIEmbeddings"}
_BASE_URL_KEYWORDS = {"base_url", "openai_api_base", "api_base"}


def _python_files() -> list[Path]:
    return [
        path
        for source_dir in _SOURCE_DIRS
        if source_dir != _GATEWAY_SRC
        for path in sorted(source_dir.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _rel(path: Path) -> str:
    return str(path.relative_to(_ROOT))


def test_the_scan_actually_covers_the_code_it_claims_to() -> None:
    files = {_rel(p) for p in _python_files()}
    assert any(f.startswith("services/assistant/") for f in files)
    assert any(f.startswith("libs/shared/") for f in files)
    assert not any(f.startswith("services/llm-gateway/") for f in files)


def test_no_service_imports_a_provider_sdk() -> None:
    offenders = []
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            offenders += [
                f"{_rel(path)}:{node.lineno} imports {name}"
                for name in names
                if any(name == bad or name.startswith(bad + ".") for bad in _FORBIDDEN_IMPORTS)
            ]
    assert offenders == [], "model calls must go through llm-gateway: " + "; ".join(offenders)


def test_no_service_names_a_provider_api_host() -> None:
    offenders = [
        f"{_rel(path)} mentions {host}"
        for path in _python_files()
        for host in _PROVIDER_HOSTS
        if host in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], "model calls must go through llm-gateway: " + "; ".join(offenders)


def test_openai_compatible_clients_always_point_at_an_explicit_base_url() -> None:
    offenders = []
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in _CLIENT_CONSTRUCTORS and not any(kw.arg in _BASE_URL_KEYWORDS for kw in node.keywords):
                offenders.append(f"{_rel(path)}:{node.lineno} {name}(...) has no base_url")
    assert offenders == [], "an OpenAI-style client without base_url defaults to the provider: " + "; ".join(offenders)


@pytest.mark.parametrize("snippet", ["import openai", "from anthropic import Anthropic", "import litellm.router"])
def test_the_import_check_would_catch_a_violation(snippet: str) -> None:
    node = ast.parse(snippet).body[0]
    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]  # type: ignore[union-attr]
    assert any(any(n == bad or n.startswith(bad + ".") for bad in _FORBIDDEN_IMPORTS) for n in names)
