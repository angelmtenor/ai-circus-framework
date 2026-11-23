# Components and request flows - Consumption: AI, BI and GenAI

Source: docs/architecture/components.md (this repository)

What people use: the web app, the model and chat services one instance per scenario kind, dashboards and chat UIs.

| Component | What it does | Technology | How to switch it on | ADR |
|---|---|---|---|---|
| **ui-react** | The web app: the scenario gallery, generic tabs driven by each scenario.yaml, the showpiece tabs, chat, voice, Settings and the admin Platform page. | React · Vite · CopilotKit · AG-UI | always on | — |
| **prediction** | Serves every tabular model: predictions with SHAP, forecasts and what-ifs, recommendations, the dataset sample and the model card, per tenant. | FastAPI · scikit-learn, LightGBM, SHAP | always on | — |
| **assistant** | The chat copilot of tabular and data scenarios: answers from tools (model, data, documents, analysis toolsets), never invents a figure, and serves a scenario's model to MCP clients. | FastAPI · LangChain · AG-UI · MCP | always on | [0031](../adr/0031-mcp-doors-on-the-assistant.md) |
| **rag-agent** | Answers questions grounded in a scenario's documents (RAG and GraphRAG), citing every passage, and serves them to MCP clients. | FastAPI · LangChain · Qdrant · MCP | always on | [0019](../adr/0019-mcp-doors-and-tool-policy.md) |
| **form-agent** | Fills a form through conversation, validates it, prints official PDFs and tracks each submission per user. | FastAPI · LangChain · ReportLab | always on | — |
| **agui-voice** | Dictation and voice mode: speech to text and text to speech around the same agents. | FastAPI · Pipecat · Whisper, Piper | On with make k3s-all; the lite profile scales it to zero (make k3s-full brings it back) | — |
| **dl-inference** | Serves the deep-learning models (images, text, camera frames) on CPU with ONNX Runtime. | FastAPI · ONNX Runtime | make k3s-dl-up | — |
| **Superset** | BI dashboards over a data product's marts, imported from the scenario's YAML. | Apache Superset, dashboards as code | make k3s-data-bi-up | [0009](../adr/0009-bi-superset.md) |
| **Open WebUI · LibreChat** | Off-the-shelf chat apps with Keycloak sign-in that reach the platform's tools only through the MCP gateway, with each user's token. | Open WebUI · LibreChat (MongoDB) | make k3s-chat-ui-up · make k3s-librechat-up | [0021](../adr/0021-chat-ui-with-real-keycloak-sign-in.md), [0026](../adr/0026-librechat-second-chat-ui.md) |
