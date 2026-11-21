# 0017 — vLLM alongside Ollama, both behind llm-gateway

Source: docs/adr/0017-vllm-alongside-ollama.md (this repository)

On a 16 GB laptop, leave room for it. With vLLM and the cluster running, Windows had about 1 GB of commit
free and new `wsl` sessions timed out (`WSAETIMEDOUT`) for about ten minutes while the cluster kept
answering. Low commit alone does not explain it, since a later attempt succeeded at the same level, so
the cause is not established. Stopping vLLM freed 5.6 GB. Pausing workloads it does not need beforehand is
not optional here, and k3s may garbage-collect their local images: re-import them before scaling back up.
