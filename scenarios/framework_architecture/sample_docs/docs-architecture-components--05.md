# Components and request flows - Infrastructure

Source: docs/architecture/components.md (this repository)

The cluster everything runs on: Kubernetes (k3s, or k3d on a laptop), its volumes, and an optional GPU node for local models.

| Component | What it does | Technology | How to switch it on | ADR |
|---|---|---|---|---|
| **Kubernetes (k3s)** | Runs every workload as a pod in the ai-circus namespace, with requests, limits, probes and non-root containers. | k3s · k3d on Docker for a laptop | always on | — |
| **GPU node** | Lends the host GPU to deep-learning training, local models (vLLM) and GPU telemetry. | NVIDIA device plugin, host GPU | make k3s-cluster K3S_GPU=1 · make k3s-gpu-telemetry | — |
| **vLLM** | Serves an open-weights LLM on the GPU behind llm-gateway, so a model can run without a cloud provider. | vLLM, OpenAI-compatible | make k3s-vllm-up | [0017](../adr/0017-vllm-alongside-ollama.md) |
| **LLM providers** | The models themselves, outside the cluster. Only llm-gateway ever calls them. | OpenAI · Anthropic · Google · Groq · DeepSeek · Ollama | external | — |
