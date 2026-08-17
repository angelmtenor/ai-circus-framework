# 04 — Generative AI

Builds on all prior files. The courses/repos worth studying before extending this repo's
GenAI code, followed by GenAI/agent/RAG best practices and the surrounding tool landscape.

## Learning Resources & Reference Repos

Read/skim these before building or extending agent/RAG features here — they're the basis for
whatever conventions get baked into the eventual cookiecutter template.

### Courses

* [LangChain Academy](https://academy.langchain.com/) — start with the **foundations** and
  **quickstart** courses.
* [DeepLearning.AI Short Courses](https://www.deeplearning.ai/short-courses/) — optional;
  pick recent (not legacy) ones focused on agents, MCP, and LLM app patterns rather than
  older prompt-engineering-only courses.

### Coding Assistants

* **GitHub Copilot:** https://docs.github.com/en/copilot — official docs; course/learning path
  at [GitHub Skills](https://skills.github.com/).
* **Claude Code / Anthropic Academy:** https://anthropic.com/learn — free official courses
  (e.g. "Claude Code 101", "Claude Code in Action"), hosted on Skilljar.
* **graphify:** https://graphify.net/ — turns a codebase (or docs/papers) into a queryable
  knowledge graph; useful for onboarding and architecture exploration alongside a coding assistant.

### Reference Repos (well-structured, all Python)

* [openai/openai-python](https://github.com/openai/openai-python) — official OpenAI SDK.
* [langchain-ai/langchain](https://github.com/langchain-ai/langchain) — LangChain core.
* **ai-circus** (this repo) — the working example these notes describe; it's the source
  material for the planned cookiecutter template (see [00-itinerary.md](00-itinerary.md)).

### Guides

* [RAG Guide](https://www.promptingguide.ai/research/rag)

---

## GenAI / Chatbot Best Practices

### Quality & Consistency

* Use state-of-the-art GenAI models
* Ensure deterministic responses for similar inputs
* Accurate retrieval (vector search)

### Deployment & Integration

* Support cloud or on-premises deployment
* Integrate vector storage (Milvus, FAISS, Qdrant, Pinecone)
* Provide custom endpoints & APIs

### Security & Compliance

* Data encryption & anonymization
* PII detection
* Ethical & sentiment checks

### Monitoring & Evaluation

* Real-time system monitoring
* Ground-truth evaluation
* User feedback loops

### Automation & Workflow

* Context-aware bots
* Custom workflows & dynamic ingestion

### Common Pitfalls

* Non-intent-based chatbots
* Overly complex Python backends
* Custom authentication instead of managed services
* Skipping structured outputs → unreliable tool calling

---

## GenAI Tool Landscape

### Newsletters & Leaderboards

* **The Batch (DeepLearning.AI):** https://www.deeplearning.ai/the-batch/
* **Model Leaderboard:** https://artificialanalysis.ai/leaderboards/models

### Agent Frameworks

* **OpenAI SDK:** https://github.com/openai/openai-agents-python
* **LangChain:** https://python.langchain.com/docs/

### Agent-UI / Demos

* **CopilotKit:** https://www.copilotkit.ai/ — essential for quickly wiring an agent to a
  usable UI for demos; implements the **AG-UI protocol** (agent-to-UI) for streaming agent
  state, tool calls, and generative UI into a frontend.

### Model Context Protocol (MCP)

* **MCP Definition:** https://modelcontextprotocol.com
* **FastMCP 2.0:** https://github.com/jlowin/fastmcp
* **LangChain MCP Integration:** https://docs.langchain.com/oss/python/langchain/mcp

### Agentic Connectivity / AI Gateway

* **Agentgateway:** https://github.com/agentgateway/agentgateway
* **LiteLLM:** https://github.com/BerriAI/litellm — unified proxy/SDK for 100+ LLM APIs (OpenAI-compatible), with load balancing, fallback routing, spend tracking, and gateway-style key management.

### Evaluation & Testing

* **Opik:** https://github.com/comet-ml/opik
* **Giskard:** https://github.com/Giskard-AI/giskard-oss

### Voice & Multimodal Agents

* **Pipecat — Real-Time Voice & Multimodal AI Agents:** https://github.com/pipecat-ai/pipecat
  — open-source framework for building voice/video/multimodal conversational agents
  (streaming STT/TTS, turn-taking, pluggable LLM/telephony backends).

### Local / Open-Source LLM Tools

* **Ollama:** https://ollama.com/
* **llmfit:** https://github.com/AlexsJones/llmfit — checks whether a given LLM will fit in
  available (V)RAM before you try to run it locally.
* **Open WebUI:** https://github.com/open-webui/open-webui
* **Chainlit:** https://github.com/Chainlit/chainlit
* **OpenRouter:** https://openrouter.ai/
* **Perplexity:** https://www.perplexity.ai/

### Vector Stores / Embedding Databases

* **OpenSearch:** https://opensearch.org/
* **Milvus, Qdrant, Faiss, Pinecone, Weaviate**

### AWS AgentCore

* **Runtime:** https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html
* **Workshop:** https://catalog.workshops.aws/agentcore-deep-dive/en-US
* **Repo:** https://github.com/awslabs/amazon-bedrock-agentcore-samples
