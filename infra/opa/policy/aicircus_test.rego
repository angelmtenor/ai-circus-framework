# Tests for aicircus.rego — run with `make k3s-governance-opa-test` (or `opa test infra/opa -v`).
package aicircus.governance_test

import rego.v1

import data.aicircus.governance

config := {
	"version": "t1",
	"tenants": {"org-blocked": {"suspended": true}},
	"systems": {
		"churn": {"allowed_models": ["cheap"], "max_tokens": 200, "rpm": 10},
		"titanic": {"status": "suspended"},
		"open": {},
	},
}

req(tenant, system, model) := {"tenant_id": tenant, "ai_system_id": system, "model": model, "call_type": "completion"}

test_unknown_system_is_allowed_by_default if {
	d := governance.decision with input as req("org-1", "nobody-knows-me", "m") with data.aicircus.config as config
	d.decision == "allow"
	d.obligations.status == "active"
}

test_listed_model_is_allowed_and_carries_obligations if {
	d := governance.decision with input as req("org-1", "churn", "cheap") with data.aicircus.config as config
	d.decision == "allow"
	d.policy_version == "t1"
	d.obligations.max_tokens == 200
	d.obligations.rpm == 10
}

test_unlisted_model_is_degraded if {
	d := governance.decision with input as req("org-1", "churn", "expensive") with data.aicircus.config as config
	d.decision == "degrade"
	d.reason_code == "model_not_in_allowlist"
	d.obligations.allowed_models == ["cheap"]
}

test_suspended_system_is_denied if {
	d := governance.decision with input as req("org-1", "titanic", "m") with data.aicircus.config as config
	d.decision == "deny"
	d.reason_code == "ai_system_suspended"
	d.obligations.status == "suspended"
}

test_suspended_tenant_is_denied_everywhere if {
	d := governance.decision with input as req("org-blocked", "open", "m") with data.aicircus.config as config
	d.decision == "deny"
	d.reason_code == "tenant_suspended"
}

test_tenant_suspension_beats_everything_else if {
	d := governance.decision with input as req("org-blocked", "churn", "cheap") with data.aicircus.config as config
	d.reason_code == "tenant_suspended"
}

test_empty_config_allows if {
	d := governance.decision with input as req("org-1", "churn", "m") with data.aicircus.config as {}
	d.decision == "allow"
	d.policy_version == "unversioned"
}
