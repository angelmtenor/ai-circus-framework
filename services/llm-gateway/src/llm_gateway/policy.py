"""
- Title:    Policy decision points for llm-gateway (noop / static / OPA / HTTP)
- Author:   Angel Martinez-Tenor

A *decision point* answers one question — may this tenant's AI system call this model now? —
in the JSON contract published as docs/governance/schemas/policy-input.json and
policy-decision.json. The hook (policy_hook.py) caches and enforces the answer; this module only
produces it, from one of four interchangeable sources:

- `noop`   — allow everything (the default: the gateway behaves exactly as before)
- `static` — a YAML file of rules per tenant / AI system (no extra service)
- `opa`    — an Open Policy Agent server (`POST /v1/data/<package>/decision`)
- `http`   — any service speaking the same contract (what an external governance tool provides)

No product is special-cased: OPA is just a decision point that wraps the contract in
`{"input": …}` / `{"result": …}`. llm-gateway cannot import `ai_circus_shared` (litellm pins
fastapi), so the contract is re-implemented here with strict validation and a contract test
checks it against the published schemas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import yaml

_REASON = re.compile(r"^[a-z0-9_]{1,96}$")
Decision = Literal["allow", "deny", "degrade"]
Status = Literal["active", "suspended"]


class PolicyError(Exception):
    """The decision point could not produce a valid decision (unreachable, malformed, undefined)."""


@dataclass(frozen=True)
class PolicyInput:
    """What the decision point is asked about — no request content, ever."""

    tenant_id: str
    ai_system_id: str
    model: str
    call_type: str

    def to_json(self) -> dict[str, str]:
        """The wire form sent to a decision point."""
        return {
            "tenant_id": self.tenant_id,
            "ai_system_id": self.ai_system_id,
            "model": self.model,
            "call_type": self.call_type,
        }


@dataclass(frozen=True)
class Obligations:
    """What the gateway must enforce alongside the decision."""

    allowed_models: tuple[str, ...] | None = None
    max_tokens: int | None = None
    rpm: int | None = None
    monthly_budget: float | None = None
    status: Status = "active"


@dataclass(frozen=True)
class PolicyDecision:
    """A validated decision."""

    decision: Decision
    reason_code: str
    policy_version: str | None = None
    obligations: Obligations = field(default_factory=Obligations)

    def to_json(self) -> dict[str, Any]:
        """The wire form of the decision (what is cached and what a decision point returns)."""
        ob = self.obligations
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "obligations": {
                "allowed_models": list(ob.allowed_models) if ob.allowed_models is not None else None,
                "max_tokens": ob.max_tokens,
                "rpm": ob.rpm,
                "monthly_budget": ob.monthly_budget,
                "status": ob.status,
            },
        }


ALLOW = PolicyDecision("allow", "default_allow")


def _int(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PolicyError(f"obligations.{key} must be a positive integer")
    return value


def parse_decision(raw: object) -> PolicyDecision:
    """Strictly validate a decision-point answer; unknown fields or bad values raise PolicyError."""
    if not isinstance(raw, dict):
        raise PolicyError("decision must be a JSON object")
    unknown = set(raw) - {"decision", "reason_code", "policy_version", "obligations"}
    if unknown:
        raise PolicyError(f"unknown decision fields {sorted(unknown)}")
    decision = raw.get("decision")
    if decision not in ("allow", "deny", "degrade"):
        raise PolicyError("decision must be allow, deny or degrade")
    reason = raw.get("reason_code")
    if not isinstance(reason, str) or not _REASON.match(reason):
        raise PolicyError("reason_code must match ^[a-z0-9_]{1,96}$")
    version = raw.get("policy_version")
    if version is not None and (not isinstance(version, str) or len(version) > 64):
        raise PolicyError("policy_version must be a string of at most 64 characters")
    ob_raw = raw.get("obligations") or {}
    if not isinstance(ob_raw, dict):
        raise PolicyError("obligations must be an object")
    bad = set(ob_raw) - {"allowed_models", "max_tokens", "rpm", "monthly_budget", "status"}
    if bad:
        raise PolicyError(f"unknown obligations {sorted(bad)}")
    models = ob_raw.get("allowed_models")
    if models is not None and (
        not isinstance(models, list) or len(models) > 50 or not all(isinstance(m, str) and m for m in models)
    ):
        raise PolicyError("obligations.allowed_models must be a list of up to 50 model names")
    budget = ob_raw.get("monthly_budget")
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int | float) or budget < 0):
        raise PolicyError("obligations.monthly_budget must be a non-negative number")
    status = ob_raw.get("status", "active")
    if status not in ("active", "suspended"):
        raise PolicyError("obligations.status must be active or suspended")
    return PolicyDecision(
        decision=decision,
        reason_code=reason,
        policy_version=version,
        obligations=Obligations(
            allowed_models=tuple(models) if models is not None else None,
            max_tokens=_int(ob_raw, "max_tokens"),
            rpm=_int(ob_raw, "rpm"),
            monthly_budget=float(budget) if budget is not None else None,
            status=status,
        ),
    )


class PolicyDecider(Protocol):
    """Anything that can answer a policy question."""

    name: str

    async def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        """Answer the policy question; raise PolicyError if no valid decision can be produced."""


class NoopDecider:
    """Allow everything — the default, so an unconfigured gateway is behaviour-identical to before."""

    name = "noop"

    async def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        """Always allow."""
        return ALLOW


class StaticDecider:
    """Rules from a YAML file, re-read when it changes (mtime), first match wins.

    ```yaml
    version: "2026-10-29"
    rules:
      - tenant: org-1            # or "*"
        ai_system: churn         # or "*"
        decision: degrade        # allow (default) | deny | degrade
        reason_code: cost_control
        obligations: {allowed_models: [gpt-4o-mini], max_tokens: 400, rpm: 30, monthly_budget: 20, status: active}
    ```
    No matching rule = allow.
    """

    name = "static"

    def __init__(self, path: str | Path) -> None:
        """Point at a YAML rules file (read lazily, reloaded when it changes)."""
        self._path = Path(path)
        self._mtime: float | None = None
        self._version: str | None = None
        self._rules: list[dict[str, Any]] = []

    def _load(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except OSError as exc:
            raise PolicyError(f"cannot read policy file {self._path}: {type(exc).__name__}") from exc
        if mtime == self._mtime:
            return
        try:
            document = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise PolicyError(f"cannot parse policy file {self._path}: {type(exc).__name__}") from exc
        rules = document.get("rules") or []
        if not isinstance(rules, list):
            raise PolicyError("policy file: `rules` must be a list")
        version = document.get("version")
        parsed = []
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise PolicyError(f"policy file: rule {index} must be a mapping")
            # Validate each rule up front so a typo fails loudly at load, not on a customer's call.
            parse_decision({
                "decision": rule.get("decision", "allow"),
                "reason_code": rule.get("reason_code", "static_rule"),
                "obligations": rule.get("obligations"),
            })
            parsed.append(rule)
        self._rules, self._version, self._mtime = parsed, str(version) if version is not None else None, mtime

    async def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        """First matching rule wins; no rule means allow."""
        self._load()
        for rule in self._rules:
            if rule.get("tenant", "*") in ("*", policy_input.tenant_id) and rule.get("ai_system", "*") in (
                "*",
                policy_input.ai_system_id,
            ):
                return parse_decision({
                    "decision": rule.get("decision", "allow"),
                    "reason_code": rule.get("reason_code", "static_rule"),
                    "policy_version": self._version,
                    "obligations": rule.get("obligations"),
                })
        return PolicyDecision("allow", "no_matching_rule", self._version)


class HttpDecider:
    """POST the input to a URL and read the decision back (the contract, verbatim)."""

    name = "http"

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the endpoint, optional bearer token and per-request timeout."""
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    def _request_body(self, policy_input: PolicyInput) -> dict[str, Any]:
        return policy_input.to_json()

    def _unwrap(self, payload: object) -> object:
        return payload

    async def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        """POST the input and validate the answer; transport or contract failures raise PolicyError."""
        try:
            response = await self._client.post(self._url, json=self._request_body(policy_input), headers=self._headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PolicyError(f"decision point failed: {type(exc).__name__}") from exc
        return parse_decision(self._unwrap(payload))


class OpaDecider(HttpDecider):
    """An OPA server: `POST /v1/data/<package>/decision` with `{"input": …}` → `{"result": …}`.

    OPA answers `{}` (no `result`) when the rule is undefined for the input — an error here, so a
    policy gap surfaces through the configured fail mode instead of silently allowing.
    """

    name = "opa"

    def __init__(
        self,
        base_url: str,
        *,
        package: str = "aicircus/governance",
        timeout_seconds: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Point at an OPA server and the package whose `decision` rule answers."""
        super().__init__(
            f"{base_url.rstrip('/')}/v1/data/{package.strip('/')}/decision",
            timeout_seconds=timeout_seconds,
            client=client,
        )

    def _request_body(self, policy_input: PolicyInput) -> dict[str, Any]:
        return {"input": policy_input.to_json()}

    def _unwrap(self, payload: object) -> object:
        if not isinstance(payload, dict) or "result" not in payload:
            raise PolicyError("OPA returned no result (decision undefined for this input)")
        return payload["result"]


def build_decider(config: Any) -> PolicyDecider:
    """The decider named by POLICY_DECIDER; an unknown or incompletely configured one is an error
    at start-up (loud), never a silent downgrade to allow-all.
    """
    kind = (getattr(config, "POLICY_DECIDER", None) or "noop").strip().lower()
    timeout = int(getattr(config, "POLICY_TIMEOUT_MS", None) or 500) / 1000
    if kind == "noop":
        return NoopDecider()
    if kind == "static":
        path = getattr(config, "POLICY_STATIC_PATH", None)
        if not path:
            raise ValueError("POLICY_DECIDER=static requires POLICY_STATIC_PATH")
        return StaticDecider(path)
    if kind == "opa":
        url = getattr(config, "POLICY_OPA_URL", None)
        if not url:
            raise ValueError("POLICY_DECIDER=opa requires POLICY_OPA_URL")
        return OpaDecider(
            url, package=getattr(config, "POLICY_OPA_PACKAGE", None) or "aicircus/governance", timeout_seconds=timeout
        )
    if kind == "http":
        url = getattr(config, "POLICY_HTTP_URL", None)
        if not url:
            raise ValueError("POLICY_DECIDER=http requires POLICY_HTTP_URL")
        token = getattr(config, "POLICY_HTTP_TOKEN", None)
        token = token.get_secret_value() if hasattr(token, "get_secret_value") else token
        return HttpDecider(url, token=token or None, timeout_seconds=timeout)
    raise ValueError(f"Unknown POLICY_DECIDER={kind!r}; use noop, static, opa or http")
