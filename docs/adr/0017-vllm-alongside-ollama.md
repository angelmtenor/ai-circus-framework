# 0017 — vLLM alongside Ollama, both behind llm-gateway

**Context.** Ollama is the framework's free local fallback: no key, runs on a CPU, one request at a
time in practice. A team with a GPU wants real serving: batched requests, an OpenAI-compatible API
and tool calling, which is what vLLM is for. vLLM needs a GPU and is heavy: its image takes about 33 GB
of disk, and it holds the whole GPU while it runs.

**Decision.**
- **Alongside, not instead.** Ollama stays the no-GPU fallback, and CI keeps using it. vLLM is a second,
  optional backend for a machine with a GPU, off by default (`make k3s-vllm-up`, overlay
  `k8s/llm-serving/`; compose profile `vllm`).
- **One chokepoint.** vLLM is only a route in `litellm_config.yaml` (`vllm-qwen`), reached through
  llm-gateway like every model, so budgets, policy and tracing apply. It has no ingress and no agent
  calls it directly. It appears in Settings like any provider, and the active model switches live.
- **Defaults fit an 8 GB laptop GPU under WSL:** `Qwen/Qwen2.5-1.5B-Instruct` (3.1 GB, Apache-2.0) with an
  8k context, `--gpu-memory-utilization=0.55`, the `hermes` tool parser (the agents call tools) and
  `VLLM_USE_V2_MODEL_RUNNER=0`.
- **Other models:** change `--model` and, if needed, `--tool-call-parser` and the memory share in
  `k8s/llm-serving/vllm.yaml`. The served name `vllm-default`, and so the route, stays the same.

**Measured (2026-11-04, RTX 4070 Laptop 8 GB, WSL2, vLLM 0.33.0).**

| | Result |
|---|---|
| First start: image pull | 7 min, about 33 GB of disk |
| Later start, weights cached | 113–116 s, no restarts (2 of 2) |
| GPU memory at idle | 4,653 of 8,188 MiB |
| Pod memory | about 3.3 GiB |
| Windows commit charge held while it runs (16 GB RAM laptop) | about 5.6 GB |
| Generation | 73–74 tokens/s, first token 0.05–0.09 s |
| KV cache | 12,560 tokens (1.53 requests of 8k at once; further requests queue) |
| 6 concurrent requests of 7,000 tokens, all distinct | all succeeded in 11.6 s, no out-of-memory |
| Prompt over the 8k context | HTTP 400 `ContextWindowExceededError` |
| Tool calling, directly and through the `assistant` agent over AG-UI | works |

What the real run taught, none of it visible in a dry run:
- **Under WSL an allocation fails at about 5.5 of the 8 GiB,** always with about 2.5 GiB reported free,
  even for 2 MiB. `Qwen2.5-7B-Instruct-AWQ` (5.6 GB) cannot load: it fails before its weights download,
  with or without the V2 runner. On native Linux more may be usable; that was not tested.
- **The memory share has a narrow window:** at 0.5 no KV cache is left and vLLM refuses to start; at 0.6
  warm-up failed on some starts and retried. 0.55 started cleanly each time, with little to spare.
- **The V2 model runner must be off here:** without pinned memory, as under WSL, it keeps its transfer
  buffers on the GPU. With it on, the first real request failed after a healthy start.
- **`enableServiceLinks: false` is required:** a Service named `vllm` makes Kubernetes inject
  `VLLM_PORT=tcp://…`, which vLLM reads as its own setting and exits.
- **`progressDeadlineSeconds` is raised:** the image pull alone nearly uses the default 10 minutes.

**Consequences.** The GPU is exclusive: while vLLM runs, a GPU training Job (`dl-training`) waits, and
vice versa. A 1.5B model is far weaker than a hosted frontier model, and it may answer without calling
a tool even when asked to: it suits a local demo or private text, not the default. Its figures should be
checked against the source. `make k3s-vllm-down` keeps the weights' volume, and the image stays on the
node until k3s collects it. llm-gateway needs a restart to pick up a new `VLLM_API_BASE`, as for any
provider setting.

On a 16 GB laptop, leave room for it. With vLLM and the cluster running, Windows had about 1 GB of commit
free and new `wsl` sessions timed out (`WSAETIMEDOUT`) for about ten minutes while the cluster kept
answering. Low commit alone does not explain it, since a later attempt succeeded at the same level, so
the cause is not established. Stopping vLLM freed 5.6 GB. Pausing workloads it does not need beforehand is
not optional here, and k3s may garbage-collect their local images: re-import them before scaling back up.
