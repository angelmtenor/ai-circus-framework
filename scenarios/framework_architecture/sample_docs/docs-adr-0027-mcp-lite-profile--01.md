# 0027 — The MCP lite profile: the MCP Lite Stack alone, inside the framework

Source: docs/adr/0027-mcp-lite-profile.md (this repository)

Builds on ADR 0020 (the MCP gateway), 0021 and 0026 (the chat UIs), 0022 (the kit), 0024 (the catalog) and 0025
(Level 3). Revises the MCP Lite Stack blueprint's last step.

**Context.** The blueprint's placement was "prove it in the framework, ship it as a standalone repository": generate
a separate repository (catalog, generator, kit, servers, deploy files) from the framework's sources. With every step
of the build plan done (gateway, identity, kit, observability, catalog and portal, Level 3, both chat UIs), the open
question was whether that repository is worth its cost now. It needs an export script, a drift test between the two
trees and a second set of deploy files, and nobody is receiving it yet. What it would prove, that the MCP path works
with nothing else of the framework running, can be proved here. The only coupling found is about 330 lines of
telemetry helpers the example pair takes from `libs/shared`.
