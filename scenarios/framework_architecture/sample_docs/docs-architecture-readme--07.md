# Architecture guide - What runs by default, and what you switch on

Source: docs/architecture/README.md (this repository)

`make k3s-all` starts the core: the gateways, the services of every scenario kind, the stores and the monitors.
Everything else is a switch, so a 16 GB laptop can run the part a demo needs: deep learning (`make k3s-dl-up`), the
data platform's tiers (`make k3s-data-core-up` and the others), the chat apps (`make k3s-chat-ui-up`,
`make k3s-librechat-up`), the MCP examples (`make k3s-mcp-examples-up`), vLLM (`make k3s-vllm-up`) and the
observability bundle (`make k3s-obs-up`). [components.md](components.md) gives the command for each component.
