"""
- Title:    Held-out evaluation of the deployed (ONNX) model
- Author:   ai-circus-framework contributors

Everything the UI's "Model insights" tab and the admin model card show: headline
metrics, per-class breakdown, confusion matrix, calibration (ECE + reliability bins),
the selective-prediction curve (accuracy vs. coverage as the confidence threshold rises
— what the triage board's "send to human review" slider trades off) and, for binary
tasks, the ROC curve (the reading room's sensitivity/specificity threshold).
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, precision_recall_fscore_support, roc_auc_score, roc_curve

CALIBRATION_BINS = 10
MAX_ROC_POINTS = 60


def _r(value: float) -> float:
    return round(float(value), 4)


def coverage_curve(probs: np.ndarray, labels: np.ndarray) -> list[dict[str, float]]:
    """Accuracy of the predictions the model keeps at each confidence threshold."""
    confidence = probs.max(axis=1)
    correct = probs.argmax(axis=1) == labels
    points = []
    for threshold in np.round(np.arange(0.0, 1.0, 0.05), 2):
        kept = confidence >= threshold
        points.append({
            "threshold": float(threshold),
            "coverage": _r(kept.mean()),
            "accuracy": _r(correct[kept].mean()) if kept.any() else 1.0,
        })
    return points


def calibration(probs: np.ndarray, labels: np.ndarray) -> tuple[float, list[dict[str, float]]]:
    """Expected calibration error and reliability-diagram bins (top-class confidence)."""
    confidence = probs.max(axis=1)
    correct = (probs.argmax(axis=1) == labels).astype(np.float64)
    edges = np.linspace(0.0, 1.0, CALIBRATION_BINS + 1)
    bins, ece = [], 0.0
    for low, high in pairwise(edges):
        in_bin = (confidence > low) & (confidence <= high)
        if not in_bin.any():
            continue
        acc, conf = correct[in_bin].mean(), confidence[in_bin].mean()
        ece += in_bin.mean() * abs(acc - conf)
        bins.append({"confidence": _r(conf), "accuracy": _r(acc), "count": int(in_bin.sum())})
    return _r(ece), bins


def evaluate_predictions(probs: np.ndarray, labels: np.ndarray, label_keys: list[str]) -> dict[str, Any]:
    """Full evaluation block for metadata.json."""
    n_classes = len(label_keys)
    predicted = probs.argmax(axis=1)
    precision, recall, f1, support = precision_recall_fscore_support(
        labels, predicted, labels=list(range(n_classes)), zero_division=0
    )
    ece, reliability = calibration(probs, labels)
    result: dict[str, Any] = {
        "n": len(labels),
        "metrics": {
            "accuracy": _r((predicted == labels).mean()),
            "macro_f1": _r(f1_score(labels, predicted, average="macro", zero_division=0)),
            "ece": ece,
        },
        "per_class": [
            {"key": key, "precision": _r(p), "recall": _r(r), "f1": _r(f), "support": int(s)}
            for key, p, r, f, s in zip(label_keys, precision, recall, f1, support, strict=True)
        ],
        "confusion_matrix": confusion_matrix(labels, predicted, labels=list(range(n_classes))).tolist(),
        "reliability": reliability,
        "coverage_curve": coverage_curve(probs, labels),
    }
    present = np.unique(labels)
    if n_classes == 2 and len(present) == 2:
        result["metrics"]["auroc"] = _r(roc_auc_score(labels, probs[:, 1]))
        fpr, tpr, thresholds = roc_curve(labels, probs[:, 1])
        keep = np.unique(np.linspace(0, len(fpr) - 1, min(len(fpr), MAX_ROC_POINTS)).astype(int))
        result["roc_curve"] = [
            {"fpr": _r(fpr[i]), "tpr": _r(tpr[i]), "threshold": _r(min(thresholds[i], 1.0))} for i in keep
        ]
    elif len(present) == n_classes:
        result["metrics"]["auroc"] = _r(roc_auc_score(labels, probs, multi_class="ovr", average="macro"))
    return result
