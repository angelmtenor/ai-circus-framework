# 0028 — Masked logs and one trace through the MCP hop

Builds on ADR 0015 (the observability contract), 0023 (observability of the MCP path) and 0027 (the MCP lite
profile, whose `OBS=1` made both gaps visible).

**Context.** Two gaps remained in what the observability bundle receives.

- **Personal data in logs.** The framework's own services never log prompts or personal data (ADR 0015), but the
  collector stores every line every pod writes, and the third-party services it runs follow their own rules:
  LibreChat logs the signed-in user's email at sign-in ("login success … email: …"). Nothing stopped those lines.
- **The MCP hop is not one trace.** ADR 0023 left the hop into an MCP server correlated through audit lines, not
  traced. Reading LiteLLM 1.103.10 settled why: it reads a client's trace context from the MCP request's `_meta`
  (SEP-414) and keeps it only as a span *link*, and it injects its own context into pass-through endpoints only,
  never into MCP calls. Nothing reaches the MCP server. LiteLLM does forward the request headers a route lists in
  `extra_headers`, and LibreChat now sends a W3C `traceparent` header on its MCP requests (its OpenTelemetry
  instrumentation, ADR 0027).

**Decision.**
- **The collector masks before it stores.** A transform step on the logs pipeline replaces every JWT
  (`header.payload[.signature]`, base64url parts starting `eyJ`) with `<token>` and every email address with
  `<email>`, in the line and in every attribute value, whoever wrote it. The email pattern needs a domain ending in
  letters, so `react@18.3.1` stays. The masks are searchable: a leak upstream still shows up as a count. A test keeps
  the step in the pipeline, before batching, and runs its patterns against real lines.
- **The gateway forwards the caller's trace context.** The catalog generator gives every route `extra_headers:
  [traceparent, tracestate]` (after any headers the server declares). An MCP server built with the kit, whose
  FastAPI tracing continues an incoming `traceparent`, then joins the chat UI's trace, and its token exchange and API
  call follow as children: one trace from the chat UI to the API. Never `baggage`: it is caller-controlled and would
  let a client spoof attribution, the reason LiteLLM ignores it too. LiteLLM's own span stays in its own trace,
  linked, as LiteLLM designs it. A test pins the forwarded headers.
- **The lite check proves both.** With `OBS=1`, `make k3s-mcp-lite-verify` sends a `traceparent` it chose through
  the gateway and finds that trace in Tempo with the kit server's spans in it, and finds no stored line carrying a
  token or an email address (and reports how many the collector masked).

**What a caller controls.** The client now picks the trace id of the MCP server's spans. A trace is a diagnostic,
not an authorisation; the spans stay content-free (RedactingSpanExporter), so the worst a client can do is file its
own spans under a chosen id.

**Verified** on a fresh lite cluster with `OBS=1` and LibreChat. LibreChat's pod log held two sign-in lines with the
user's email; Loki stored them as `email: <email> | username: <email>`, and the check found no stored token or email
(2 masked). The trace id the check sent through the gateway came back from Tempo with crm-mcp's and crm-api's spans.
A real LibreChat question was one trace of 26 s (mostly the model) holding the tool call's 129 ms: crm-mcp's live
check of the user's token (16 ms), the tool (105 ms) with the token exchange for crm-api (42 ms), the mutual-TLS call
to crm-api (40 ms) and crm-api's answer (32 ms) with its own live token check (19 ms). The masking rules also run in
a throwaway collector against a sample line, and a test runs their patterns against real lines.

**Consequences.** The blueprint's observability test ("a search for tokens or personal data in all signals finds
nothing") holds for every log line, not only the framework's. Open WebUI's MCP client sends no trace context, so its
MCP hop stays correlated through audit lines; LibreChat's is one trace. A platform that merges this collector
snippet into its own collector gets the masking too.
