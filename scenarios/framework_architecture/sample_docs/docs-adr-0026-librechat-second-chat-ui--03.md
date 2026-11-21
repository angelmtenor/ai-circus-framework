# 0026 — LibreChat as the second chat UI

Source: docs/adr/0026-librechat-second-chat-ui.md (this repository)

**Consequences.** The framework offers two chat UIs over one MCP path: the catalog decides both UIs' servers, the
gateway and the servers decide access, and either UI can be left out. A second optional database (MongoDB, SSPL,
used unmodified as LibreChat's store). One refused-then-retried call per token change in LibreChat, observable as
`owner mismatch` warnings in the gateway's log. Per-group visibility of servers in LibreChat is not generated:
every user sees every server, and the door or the server's policy decides.
