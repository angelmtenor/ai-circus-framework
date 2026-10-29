# Reference policy for llm-gateway's `opa` decision point (POLICY_DECIDER=opa).
# Author: Angel Martinez-Tenor
#
# Contract (docs/governance/schemas/policy-input.json / policy-decision.json): the gateway POSTs
# {"input": {tenant_id, ai_system_id, model, call_type}} to /v1/data/aicircus/governance/decision
# and enforces the object this rule returns. This file is only an example of one policy — the
# gateway talks to any OPA package (POLICY_OPA_PACKAGE) or any HTTP service speaking the same
# contract. The rules here read their facts from data/config.json (OPA's `data` document):
#
#   tenants.<id>.suspended          deny every call of that tenant
#   systems.<id>.status             "suspended" denies every call of that AI system
#   systems.<id>.allowed_models     a call to another model is *degraded* to the first listed one
#   systems.<id>.max_tokens / rpm / monthly_budget   obligations the gateway enforces
#
# Anything not mentioned is allowed. OPA answers `{}` (undefined) only if this package is missing
# — the gateway then treats it as an error and applies POLICY_FAIL_MODE.
package aicircus.governance

import rego.v1

version := object.get(data.aicircus.config, "version", "unversioned")

tenant := object.get(object.get(data.aicircus.config, "tenants", {}), input.tenant_id, {})

system := object.get(object.get(data.aicircus.config, "systems", {}), input.ai_system_id, {})

allowed_models := object.get(system, "allowed_models", [])

# The obligations the gateway enforces on every non-denied call.
base_obligations := object.filter(system, ["allowed_models", "max_tokens", "rpm", "monthly_budget"])

default decision := {
	"decision": "allow",
	"reason_code": "default_allow",
	"policy_version": "unversioned",
	"obligations": {"status": "active"},
}

decision := {
	"decision": "deny",
	"reason_code": "tenant_suspended",
	"policy_version": version,
	"obligations": {"status": "suspended"},
} if {
	tenant.suspended == true
} else := {
	"decision": "deny",
	"reason_code": "ai_system_suspended",
	"policy_version": version,
	"obligations": {"status": "suspended"},
} if {
	system.status == "suspended"
} else := {
	"decision": "degrade",
	"reason_code": "model_not_in_allowlist",
	"policy_version": version,
	"obligations": object.union(base_obligations, {"status": "active"}),
} if {
	count(allowed_models) > 0
	not input.model in allowed_models
} else := {
	"decision": "allow",
	"reason_code": "policy_allow",
	"policy_version": version,
	"obligations": object.union(base_obligations, {"status": "active"}),
}
