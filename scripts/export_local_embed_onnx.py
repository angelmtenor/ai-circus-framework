# /// script
# requires-python = ">=3.11"
# dependencies = ["torch", "transformers>=4.56", "sentence-transformers", "onnx", "onnxruntime", "numpy", "tokenizers"]
# [tool.uv.sources]
# torch = [{ index = "pytorch-cpu" }]
# [[tool.uv.index]]
# name = "pytorch-cpu"
# url = "https://download.pytorch.org/whl/cpu"
# explicit = true
# ///
"""One-off, offline: export the `local-embed` model (voyageai/voyage-4-nano) to ONNX for llm-gateway.

llm-gateway serves `local-embed` with ONNX Runtime + `tokenizers` instead of in-process torch (the
biggest memory saving in the plan). The graph is the Qwen3 backbone exactly as llm-gateway serves it
today (see `Encoder`); mean pooling and L2 normalisation stay in numpy (llm_gateway/custom_handler.py). This script needs torch,
so it runs on the host in a throwaway `uv` environment — never in the gateway image:

    uv run scripts/export_local_embed_onnx.py [OUT_DIR]        # default ~/.cache/ai-circus/onnx/voyage-4-nano

It writes `model.onnx` (+ external data) and `tokenizer.json`, then checks the ONNX vectors against
sentence-transformers' own on a few texts (cosine must be >= 0.9999) and refuses to finish otherwise.
`make k3s-embed-onnx` copies the result into the gateway's model volume.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import gc
import shutil
import sys
from pathlib import Path

import numpy as np
import torch

MODEL_ID = "voyageai/voyage-4-nano"
CHECK_TEXTS = [
    "How do I reset my password?",
    "Invoice overdue by 30 days, escalate to the account manager.",
    "El pedido llegó tarde y el producto estaba dañado.",
    "x",
]
MIN_COSINE = 0.9999


class Encoder(torch.nn.Module):
    """input_ids + padding mask -> per-token states, exactly as sentence-transformers' first module.

    NOTE what is being exported: llm-gateway loads `SentenceTransformer("voyageai/voyage-4-nano")` WITHOUT
    `trust_remote_code`, so it serves the plain Qwen3 backbone (1024-d, causal attention, no linear head) —
    not the 2048-d bidirectional model the model card describes. Every vector already in Qdrant, and every
    text challenger trained on the gateway's embeddings, lives in that space, so the export reproduces it.
    """

    def __init__(self, inner: torch.nn.Module) -> None:
        super().__init__()
        self.inner = inner

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        return self.inner(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state


def main() -> None:
    from sentence_transformers import SentenceTransformer

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "~/.cache/ai-circus/onnx/voyage-4-nano").expanduser()
    out.mkdir(parents=True, exist_ok=True)

    reference = SentenceTransformer(MODEL_ID, model_kwargs={"torch_dtype": torch.float32})  # as llm-gateway loads it
    inner = reference[0].auto_model.eval().float()
    tokenizer = reference.tokenizer
    encoder = Encoder(inner).eval()

    expected = reference.encode(CHECK_TEXTS, normalize_embeddings=True)
    batch = tokenizer(CHECK_TEXTS[:2], padding=True, return_tensors="pt")
    with torch.no_grad():
        torch.onnx.export(
            encoder,
            (batch["input_ids"], batch["attention_mask"]),
            str(out / "model.onnx"),
            input_names=["input_ids", "attention_mask"],
            output_names=["token_states"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "token_states": {0: "batch", 1: "seq"},
            },
            opset_version=17,
            dynamo=False,
        )
    tokenizer.save_pretrained(out)
    if not (out / "tokenizer.json").exists():
        raise SystemExit("tokenizer.json was not written")
    del encoder, inner, reference  # the checks below need the RAM, not the torch model
    gc.collect()

    import onnxruntime as ort
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(out / "model.onnx", out / "model.int8.onnx", weight_type=QuantType.QInt8, per_channel=True)

    def worst_cosine(path: Path) -> float:
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        worst = 1.0
        for i, text in enumerate(CHECK_TEXTS):
            enc = tokenizer([text], return_tensors="np", padding=True)
            feed = {"input_ids": enc["input_ids"].astype(np.int64), "attention_mask": enc["attention_mask"].astype(np.int64)}
            vector = session.run(None, feed)[0][0].mean(axis=0)
            vector /= np.linalg.norm(vector)
            worst = min(worst, float(vector @ expected[i]))
        # One padded batch too: right-padding must not change any real token's state.
        enc = tokenizer(CHECK_TEXTS, return_tensors="np", padding=True)
        feed = {"input_ids": enc["input_ids"].astype(np.int64), "attention_mask": enc["attention_mask"].astype(np.int64)}
        states = session.run(None, feed)[0]
        mask = enc["attention_mask"][:, :, None].astype(states.dtype)
        pooled = (states * mask).sum(axis=1) / mask.sum(axis=1)
        pooled /= np.linalg.norm(pooled, axis=1, keepdims=True)
        for i in range(len(CHECK_TEXTS)):
            worst = min(worst, float(pooled[i] @ expected[i]))
        return worst

    fp32 = worst_cosine(out / "model.onnx")
    int8 = worst_cosine(out / "model.int8.onnx")
    print(f"worst cosine vs sentence-transformers over {len(CHECK_TEXTS)} texts: fp32 {fp32:.6f}, int8 {int8:.6f}")
    for name in ("model.onnx", "model.int8.onnx"):
        size = sum(p.stat().st_size for p in out.glob(name.replace(".onnx", "*")))
        print(f"{name}: {size / 1e6:.0f} MB on disk")
    if fp32 < MIN_COSINE:
        shutil.rmtree(out)
        raise SystemExit(f"ONNX export drifts from the torch model (cosine {fp32:.6f} < {MIN_COSINE}) — removed {out}")
    print(f"exported to {out} (int8 is the smaller, lossy one: use it only if its cosine is acceptable)")


if __name__ == "__main__":
    main()
