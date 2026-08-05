"""Detect and configure SSL CA bundle for environments with SSL inspection.

Tests HTTPS connectivity to github.com. If SSL certificate verification fails
(common in networks with SSL inspection/proxy), attempts to:
  - On macOS: export trusted certificates from the system keychain
  - Write SSL_CERT_FILE=<path> into .env so Make exports it automatically

Usage:
    uv run python scripts/ssl_setup.py

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

TEST_URL = "https://github.com"
CA_BUNDLE_PATH = Path(".cache/ca-bundle.pem")
ENV_FILE = Path(".env")
ENV_KEY = "SSL_CERT_FILE"


def _ssl_ok() -> bool:
    """Return True if HTTPS to TEST_URL succeeds without SSL errors."""
    try:
        urllib.request.urlopen(TEST_URL, timeout=10)
        return True
    except urllib.error.URLError as exc:
        reason = str(exc)
        return "CERTIFICATE_VERIFY_FAILED" not in reason and "certificate verify" not in reason.lower()
    except Exception:
        return True


def _dump_macos_keychain(output: Path) -> bool:
    """Export all trusted certs from macOS system keychains to a PEM file."""
    keychains = [
        "/Library/Keychains/System.keychain",
        "/System/Library/Keychains/SystemRootCertificates.keychain",
    ]
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            ["security", "find-certificate", "-a", "-p", *keychains],  # ruff: ignore[start-process-with-partial-path]
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result.stdout)
            return True
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return False


def _write_env(key: str, value: str) -> None:
    """Insert or replace key=value in .env, preserving other content."""
    line = f"{key}={value}\n"
    if not ENV_FILE.exists():
        ENV_FILE.write_text(line)
        return

    content = ENV_FILE.read_text()
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(content):
        ENV_FILE.write_text(pattern.sub(line.rstrip("\n"), content))
    else:
        newline = "" if content.endswith("\n") else "\n"
        ENV_FILE.write_text(content + newline + line)


def main() -> int:
    """Detect SSL issues and configure CA bundle if needed."""
    if _ssl_ok():
        print(f"✓ SSL connectivity OK ({TEST_URL})")  # ruff: ignore[print]
        return 0

    print(f"⚠️  SSL certificate verification failed for {TEST_URL}.")  # ruff: ignore[print]
    print("   This is typical in networks with SSL inspection.")  # ruff: ignore[print]

    if sys.platform != "darwin":
        print(f"   Auto-fix supported on macOS only. Set {ENV_KEY} manually to your CA bundle path.")  # ruff: ignore[print]
        return 1

    print("   Exporting system keychain certificates...")  # ruff: ignore[print]
    if not _dump_macos_keychain(CA_BUNDLE_PATH):
        print(f"   ❌ Failed to export keychain. Set {ENV_KEY} manually.")  # ruff: ignore[print]
        return 1

    abs_path = CA_BUNDLE_PATH.resolve()
    _write_env(ENV_KEY, str(abs_path))
    print(f"   ✓ CA bundle written to {abs_path}")  # ruff: ignore[print]
    print(f"   ✓ {ENV_KEY}={abs_path} added to .env")  # ruff: ignore[print]
    print("   Re-run the previous command — SSL will be configured automatically.")  # ruff: ignore[print]
    return 0


if __name__ == "__main__":
    sys.exit(main())
