"""
- Title:    Post-hoc probability calibration (temperature scaling)
- Author:   ai-circus-framework contributors

A fine-tuned network is usually right *and* overconfident: on an easy, clean dataset
its logits keep growing long after the predictions stop changing, so every probability
it reports is ~0% or ~100% — useless for a triage threshold or a reading-room priority.
Temperature scaling (Guo et al., "On Calibration of Modern Neural Networks", ICML 2017)
fixes that with one scalar T > 0 fitted on held-out validation logits by minimizing the
negative log-likelihood: probabilities become softmax(logits / T). The predicted class
never changes (argmax is invariant to T) — only how sure the model claims to be.

T is fitted on the *deployed* ONNX artifact's own validation logits and stored in the
manifest; dl-inference divides by it before every softmax/log-odds, and every metric in
the manifest (accuracy, ECE, curves) is computed from the calibrated probabilities.
"""

from __future__ import annotations

import numpy as np

T_MIN, T_MAX = 0.05, 50.0


def log_softmax(logits: np.ndarray) -> np.ndarray:
    """Row-wise log-probabilities in float64."""
    z = logits.astype(np.float64)
    z = z - z.max(axis=1, keepdims=True)
    return z - np.log(np.exp(z).sum(axis=1, keepdims=True))


def nll(logits: np.ndarray, labels: np.ndarray, temperature: float) -> float:
    """Mean negative log-likelihood of `labels` under softmax(logits / temperature)."""
    lp = log_softmax(logits / temperature)
    return float(-lp[np.arange(len(labels)), labels].mean())


def _nll_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    grid = np.exp(np.linspace(np.log(T_MIN), np.log(T_MAX), 241))
    losses = np.array([nll(logits, labels, t) for t in grid])
    best = int(losses.argmin())
    lo, hi = grid[max(best - 1, 0)], grid[min(best + 1, len(grid) - 1)]
    fine = np.exp(np.linspace(np.log(lo), np.log(hi), 101))
    return float(fine[int(np.argmin([nll(logits, labels, t) for t in fine]))])


def mean_confidence(logits: np.ndarray, temperature: float) -> float:
    """Average top-class probability under softmax(logits / temperature)."""
    return float(calibrated_probs(logits, temperature).max(axis=1).mean())


def _confidence_bound_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    """Smallest T whose mean confidence doesn't exceed the Laplace-smoothed accuracy
    (correct + 1) / (n + 2) — mean confidence falls monotonically as T grows, so bisect
    in log-space.
    """
    correct = int((logits.argmax(axis=1) == labels).sum())
    target = (correct + 1) / (len(labels) + 2)
    if mean_confidence(logits, T_MIN) <= target:
        return T_MIN
    lo, hi = np.log(T_MIN), np.log(T_MAX)
    for _ in range(60):
        mid = (lo + hi) / 2
        if mean_confidence(logits, float(np.exp(mid))) > target:
            lo = mid
        else:
            hi = mid
    return float(np.exp(hi))


def fit_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    """The calibration temperature: NLL-optimal on the calibration set (log-spaced grid +
    local refine; dependency-free), but never sharper than the data can justify.

    On a small calibration set the model classifies perfectly (85/85 validation messages
    here), the NLL keeps falling as T -> 0 and would re-inflate every probability to
    ~100%. So T is also bounded below by the temperature at which the mean confidence
    equals the Laplace-smoothed accuracy (correct + 1) / (n + 2): 85/85 correct only
    justifies ~98.9% average certainty, not 100%.
    """
    if len(labels) == 0:
        return 1.0
    return round(max(_nll_temperature(logits, labels), _confidence_bound_temperature(logits, labels)), 4)


def calibrated_probs(logits: np.ndarray, temperature: float) -> np.ndarray:
    """softmax(logits / temperature)."""
    return np.exp(log_softmax(logits / temperature))
