"""
check_full_env.py

Enhanced environment verification utility with beautiful, Makefile-aligned output.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
import shutil
import sys
import tomllib
from argparse import ArgumentParser
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import yaml
from dotenv import dotenv_values, load_dotenv
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

# ── Printer protocol ──────────────────────────────────────────────────────────


class Printer(Protocol):
    """Callable printer compatible with both rich and fallback implementations."""

    def __call__(self, *objects: Any, sep: str = " ", end: str = "\n", **kwargs: Any) -> None:
        """Print objects to the output stream."""
        ...


# ── Rich imports with graceful fallback ───────────────────────────────────────

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    printer: Printer = console.print

except ImportError:
    console = None  # type: ignore[assignment]
    _builtin_print = __builtins__["print"] if isinstance(__builtins__, dict) else __builtins__.print  # type: ignore[index]

    _RICH_KWARGS = {
        "style",
        "justify",
        "overflow",
        "no_wrap",
        "emoji",
        "markup",
        "highlight",
        "width",
        "height",
        "crop",
        "soft_wrap",
        "new_line_start",
    }

    def fallback_printer(*objects: Any, sep: str = " ", end: str = "\n", **kwargs: Any) -> None:
        """Fallback printer that strips rich-only kwargs before delegating to builtins."""
        for k in _RICH_KWARGS:
            kwargs.pop(k, None)
        _builtin_print(*objects, sep=sep, end=end, **kwargs)

    printer = fallback_printer

    def _fallback_panel(content: Any, title: str | None = None, **_kwargs: Any) -> str:
        """Fallback panel: plain bordered text block."""
        text = str(content)
        width = max(len(text), len(title or "")) + 6
        bar = "═" * width
        header = f" {title} ".center(width) if title else ""
        return f"\n{bar}\n{header}\n{bar}\n  {text}\n{bar}\n"

    Panel = _fallback_panel  # type: ignore[misc]

    class Table:  # type: ignore[no-redef]
        """Minimal ASCII table fallback."""

        def __init__(self) -> None:
            """Initialize empty headers, rows, and show_header flag."""
            self.headers: list[str] = []
            self.rows: list[tuple[str, ...]] = []
            self.show_header: bool = True

        def add_column(self, header: str, **_: Any) -> None:
            """Append a column header."""
            self.headers.append(header)

        def add_row(self, *cells: str, **_: Any) -> None:
            """Append a row of cells."""
            self.rows.append(cells)

        def __str__(self) -> str:
            if not self.rows:
                return ""
            all_rows = ([self.headers] if self.headers else []) + list(self.rows)
            widths = [max(len(str(c)) for c in col) for col in zip(*all_rows, strict=False)]

            def row_str(row: tuple[str, ...]) -> str:
                return " │ ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=False))

            lines = []
            if self.headers:
                lines += [row_str(tuple(self.headers)), "─┼─".join("─" * w for w in widths)]
            lines += [row_str(r) for r in self.rows]
            return "\n".join(lines)

        @property
        def row_count(self) -> int:
            """The number of data rows."""
            return len(self.rows)

# ── Logging helpers ───────────────────────────────────────────────────────────


class Checker:
    """Runs environment checks and accumulates issues."""

    def __init__(self, verbose: bool = False) -> None:
        """Initialize the checker with zero issues and optional verbose output."""
        self.issues: int = 0
        self.verbose = verbose

    def ok(self, msg: str) -> None:
        """Print a success message."""
        printer(f"[bold green]✅ {msg}[/bold green]")

    def warn(self, msg: str) -> None:
        """Print a warning and increment the issue counter."""
        self.issues += 1
        printer(f"[bold yellow]⚠️  {msg}[/bold yellow]")

    def error(self, msg: str) -> None:
        """Print an error and increment the issue counter."""
        self.issues += 1
        printer(f"[bold red]❌ {msg}[/bold red]")

    def info(self, msg: str) -> None:
        """Print an informational message."""
        printer(f"[cyan]🔍 {msg}[/cyan]")

    @staticmethod
    def section(title: str) -> None:
        """Print a titled section header panel."""
        printer(Panel(title, style="bold magenta", padding=(1, 2)))

    def _new_table(self, *headers: str) -> Table:
        t = Table()
        if console:
            for h in headers:
                t.add_column(h)
        else:
            t.headers = list(headers)
        return t

    def _print_table(self, table: Table) -> None:
        has_rows = (table.row_count > 0) if console else bool(table.rows)  # type: ignore[union-attr]
        if has_rows:
            printer(table)

    # ── Checks ────────────────────────────────────────────────────────────────

    def check_virtual_environment(self, expected: str = ".venv") -> None:
        """Verify the correct virtual environment is active and uv is available."""
        self.section("Virtual Environment")

        in_venv = hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
        current = Path(sys.prefix).resolve()
        expected_path = Path(expected).resolve()

        printer(f"Active venv: [dim]{current}[/dim]")

        if not in_venv:
            self.error("No virtual environment is activated")
            printer("   Run: [bold]source .venv/bin/activate[/bold] (macOS/Linux)")
            printer("   Or:  [bold].venv\\Scripts\\activate[/bold] (Windows)")
            return

        if current != expected_path:
            self.error(f"Wrong venv!\n   Expected: {expected_path}\n   Active:   {current}")
        else:
            self.ok("Correct virtual environment is active")

        if shutil.which("uv"):
            self.ok("uv is available in PATH")
        else:
            self.warn("uv not found in PATH — install: https://docs.astral.sh/uv/getting-started/installation/")

    def check_environment_variables(
        self,
        example_env_path: Path | str = ".env.example",
        settings_path: Path | str = "settings.yaml",
    ) -> None:
        """Validate environment variables against .env.example, flagging missing or placeholder values."""
        path = Path(example_env_path)
        if not path.exists():
            self.warn(f".env.example not found at {path}")
            return

        self.section("Environment Variables")

        example_vars = dotenv_values(path)
        required_keys = _mandatory_secret_keys(settings_path)
        secret_keys = _secret_keys(settings_path)

        table = self._new_table("Key", "Value", "Status")
        for key in sorted(example_vars):
            current = os.getenv(key)
            summary = _redact(current) if key in secret_keys else (current or "[dim]<not set>[/dim]")
            placeholder = (example_vars.get(key) or "").strip("\"'")

            if key in required_keys:
                if current is None:
                    self.error(f"{key} is required but not set")
                    status = "[red]Missing (required)[/red]"
                elif current == placeholder:
                    self.warn(f"{key} still has placeholder value")
                    status = "[yellow]Placeholder[/yellow]"
                else:
                    status = "[green]Set[/green]"
            else:
                status = "[green]Set[/green]" if current else "[dim]Optional[/dim]"

            table.add_row(key, summary, status)

        self._print_table(table)

    def check_python_packages(self, pyproject_path: str = "pyproject.toml") -> None:
        """Check that all packages declared in pyproject.toml are installed and version-compatible."""
        p = Path(pyproject_path)
        if not p.exists():
            self.error(f"{pyproject_path} not found")
            return

        data = tomllib.loads(p.read_text(encoding="utf-8"))
        project = data.get("project", {})
        requires_python = project.get("requires-python", ">=3.11")
        dependencies: list[str] = project.get("dependencies", [])

        self.section("Python Packages")

        current_ver = Version(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        py_ok = current_ver in SpecifierSet(requires_python)
        status_str = "[green]OK[/green]" if py_ok else "[red]FAIL[/red]"
        printer(f"Python {current_ver} — requires-python: {requires_python} — {status_str}")

        if not dependencies:
            self.info("No dependencies declared in pyproject.toml")
            return

        table = self._new_table("Package", "Required", "Installed", "Status")
        problems = 0

        for dep_str in dependencies:
            try:
                req = Requirement(dep_str)
                name, specifier = req.name, str(req.specifier) or "(any)"
            except InvalidRequirement:
                name, specifier = (dep_str.split()[0] if dep_str else "unknown"), "(invalid)"

            try:
                installed = metadata.version(name)
                if specifier in {"(any)", "(invalid)"} or Version(installed) in SpecifierSet(specifier):
                    row_status = "[green]OK[/green]"
                else:
                    row_status = "[yellow]Version mismatch[/yellow]"
                    problems += 1
            except metadata.PackageNotFoundError:
                installed = "[dim]Not installed[/dim]"
                row_status = "[red]Missing[/red]"
                problems += 1

            if self.verbose or row_status != "[green]OK[/green]":
                table.add_row(name, specifier, installed, row_status)

        self._print_table(table)

        if problems == 0:
            self.ok("All required packages are installed and compatible")
        else:
            self.warn(f"{problems} package issue(s) found")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _redact(value: str | None) -> str:
    """Return a redacted summary of a secret value."""
    if not value:
        return "[dim]<not set>[/dim]"
    if value.lower() in {"true", "false"}:
        return value.lower()
    return "****" + value[-4:] if len(value) >= 4 else "****"


def _mandatory_secret_keys(settings_path: Path | str) -> set[str]:
    """Return names of secret env vars marked mandatory in settings.yaml (the source of truth)."""
    path = Path(settings_path)
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        var["name"] for var in data.get("env_variables", []) if var.get("secret", False) and var.get("mandatory", False)
    }


def _secret_keys(settings_path: Path | str) -> set[str]:
    """Return names of all env vars marked secret in settings.yaml, regardless of mandatory."""
    path = Path(settings_path)
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {var["name"] for var in data.get("env_variables", []) if var.get("secret", False)}


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Parse CLI args, run all environment checks, and exit 1 if any issues are found."""
    parser = ArgumentParser(description="Check full project environment setup")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all package details")
    args = parser.parse_args()

    printer(Panel("rag-agent Environment Check", style="bold blue", padding=(1, 3)))

    checker = Checker(verbose=args.verbose)
    checker.check_virtual_environment()
    load_dotenv()
    checker.check_environment_variables()
    checker.check_python_packages()

    printer("\n" + "═" * 60)
    if checker.issues == 0:
        printer(Panel("[bold green]✅ All checks passed! Environment is ready.[/bold green]", style="green"))
    else:
        printer(
            Panel(
                f"[bold yellow]⚠️  {checker.issues} issue(s) found. Review warnings above.[/bold yellow]", style="yellow"
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
