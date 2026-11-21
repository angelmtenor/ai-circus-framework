# 0020 — One MCP gateway in front of the doors, open-source features only

Source: docs/adr/0020-mcp-gateway-without-enterprise.md (this repository)

**Verified on k3s** (admin key, the same key viewing data-api as the S012 store manager, the
engineering-demo key, a forged token) through `/mcp/ai_circus_reference,surf_retail`: 9 tools for the
admin, 8 for the store manager (no `run_sql`) with its store filter in every answer, none for the
engineering-demo tenant (entitled to neither scenario) or the forged token, and only the document door's
3 tools when data-api refuses an unknown View-as role.

**Consequences.** One URL for every door, with no new pod and no new memory. The doors' security is
unchanged, because they still decide. A door whose tier is not deployed fails only its own calls.
LiteLLM logs a warning per door at its first request, because the doors publish no OAuth
protected-resource metadata yet (that arrives with ADR 0019's step 1). The gateway now depends on
LiteLLM's experimental MCP module, pinned and contract-tested.
