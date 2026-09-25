"""
- Title:    ONNX export (+ optional int8 dynamic quantization)
- Author:   ai-circus-framework contributors

dl-inference runs onnxruntime only — no torch in the serving image — which is what
keeps the deployed pod small. The TorchScript-based exporter (`dynamo=False`) is used
on purpose: it traces ModernBERT/ConvNeXt with sdpa attention into plain ONNX ops and
keeps dynamic batch/sequence axes, with fp32 outputs matching PyTorch to ~1e-6.
Quantization (text encoders) shrinks the model ~4x (600 MB -> 150 MB) and roughly
halves CPU latency; its accuracy cost is measured, not assumed — the pipeline always
evaluates the file this returns.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import onnxruntime as ort
import torch

from dl_training.core.data import Split
from dl_training.core.logger import get_logger
from dl_training.core.tasks import Task

logger = get_logger(__name__)

ONNX_OPSET = 18


def export_onnx(task: Task, example: Split, out_dir: Path, *, quantize: bool) -> Path:
    """Export `task.model` (already on CPU, eval mode) to ONNX; quantize if asked.

    Returns:
        Path of the model file to deploy.
    """
    wrapper, inputs, input_names, output_names, dynamic_axes = task.export_spec(example)
    wrapper.eval()
    fp32_path = out_dir / "model_fp32.onnx"
    with torch.no_grad(), warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the TorchScript exporter's deprecation/tracer warnings
        torch.onnx.export(
            wrapper,
            inputs,
            str(fp32_path),
            input_names=input_names,
            output_names=output_names,
            dynamic_axes=dynamic_axes,
            opset_version=ONNX_OPSET,
            dynamo=False,
        )
    logger.info("Exported ONNX model ({:.1f} MB)", fp32_path.stat().st_size / 1e6)
    if not quantize:
        return fp32_path

    from onnxruntime.quantization import QuantType, quantize_dynamic

    int8_path = out_dir / "model_int8.onnx"
    quantize_dynamic(str(fp32_path), str(int8_path), weight_type=QuantType.QInt8, per_channel=True)
    logger.info("Quantized ONNX model to int8 ({:.1f} MB)", int8_path.stat().st_size / 1e6)
    return int8_path


def open_session(path: Path) -> ort.InferenceSession:
    """A CPU session configured like dl-inference's (same numerics as production).

    At most half the cores: onnxruntime otherwise spins a thread per core, and a host run
    evaluating a large graph (the anomaly detector's kNN over its memory bank) starved
    the co-located k3d node until it went NotReady and restarted every pod.
    """
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
