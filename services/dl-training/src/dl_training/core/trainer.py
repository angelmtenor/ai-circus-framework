"""
- Title:    Modality-agnostic fine-tuning loop
- Author:   ai-circus-framework contributors

Plain PyTorch (no HF Trainer/accelerate — two fewer heavy dependencies): AdamW over the
trainable parameters, linear warm-up then linear decay, gradient clipping, bf16
autocast on CUDA, optional inverse-frequency class weights, and best-epoch selection by
validation macro-F1 (restored at the end, so a late over-fit epoch is never exported).
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
import torch
from ai_circus_shared.scenario_schema import DlTrainBudget
from sklearn.metrics import f1_score
from torch import nn

from dl_training.core.data import Split
from dl_training.core.device import DeviceInfo
from dl_training.core.logger import get_logger
from dl_training.core.tasks import Task

logger = get_logger(__name__)

WARMUP_FRACTION = 0.1
MAX_GRAD_NORM = 1.0


@dataclass(frozen=True)
class EpochRecord:
    """One epoch's learning curve point (rendered by the UI's training-curve chart)."""

    epoch: int
    train_loss: float
    val_loss: float
    val_accuracy: float
    val_macro_f1: float
    seconds: float


def class_weights(labels: np.ndarray, n_classes: int) -> torch.Tensor:
    """Inverse-frequency weights, normalized to mean 1 (absent classes get weight 1)."""
    counts = np.bincount(labels, minlength=n_classes).astype(np.float64)
    weights = np.where(counts > 0, counts.sum() / (n_classes * np.maximum(counts, 1)), 1.0)
    return torch.as_tensor(weights / weights.mean(), dtype=torch.float32)


def _autocast(device: DeviceInfo) -> torch.autocast:
    return torch.autocast(device_type=device.kind, dtype=torch.bfloat16, enabled=device.kind == "cuda")


def _move(inputs: dict[str, torch.Tensor], device: DeviceInfo) -> dict[str, torch.Tensor]:
    return {k: v.to(device.torch_device, non_blocking=True) for k, v in inputs.items()}


def evaluate(
    task: Task, split: Split, batch_size: int, device: DeviceInfo, loss_fn: nn.Module
) -> tuple[float, float, float]:
    """(mean loss, accuracy, macro-F1) of the PyTorch model on `split`."""
    task.model.eval()
    losses, predictions = [], []
    with torch.no_grad():
        for inputs, labels in task.batches(split, batch_size, shuffle=False, augment=False, seed=0):
            with _autocast(device):
                logits = task.model(**_move(inputs, device)).logits
            labels = labels.to(device.torch_device)
            losses.append(float(loss_fn(logits.float(), labels)) * len(labels))
            predictions.append(logits.argmax(dim=-1).cpu().numpy())
    predicted = np.concatenate(predictions)
    accuracy = float((predicted == split.labels).mean())
    macro_f1 = float(f1_score(split.labels, predicted, average="macro", zero_division=0))
    return sum(losses) / len(split), accuracy, macro_f1


def fine_tune(
    task: Task,
    train: Split,
    val: Split,
    budget: DlTrainBudget,
    device: DeviceInfo,
    *,
    n_classes: int,
    weighted_loss: bool,
    seed: int,
    label_smoothing: float = 0.0,
    on_epoch: Callable[[EpochRecord], None] | None = None,
) -> list[EpochRecord]:
    """Fine-tune `task.model` in place; leaves it holding the best-validation weights.

    Returns:
        One EpochRecord per epoch.
    """
    torch.manual_seed(seed)
    model = task.model.to(device.torch_device)
    weights = class_weights(train.labels, n_classes).to(device.torch_device) if weighted_loss else None
    loss_fn = nn.CrossEntropyLoss(weight=weights, label_smoothing=label_smoothing)
    eval_loss_fn = nn.CrossEntropyLoss()
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=budget.learning_rate, weight_decay=0.01)
    steps_per_epoch = -(-len(train) // budget.batch_size)
    total_steps = steps_per_epoch * budget.epochs
    warmup = max(1, int(total_steps * WARMUP_FRACTION))
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lambda step: (step + 1) / warmup if step < warmup else max(0.0, (total_steps - step) / (total_steps - warmup)),
    )

    history: list[EpochRecord] = []
    best_f1, best_state = -1.0, None
    for epoch in range(1, budget.epochs + 1):
        started = time.monotonic()
        model.train()
        running, seen = 0.0, 0
        for inputs, labels in task.batches(train, budget.batch_size, shuffle=True, augment=True, seed=seed + epoch):
            labels = labels.to(device.torch_device)
            with _autocast(device):
                logits = model(**_move(inputs, device)).logits
            loss = loss_fn(logits.float(), labels)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, MAX_GRAD_NORM)
            optimizer.step()
            scheduler.step()
            running += loss.item() * len(labels)
            seen += len(labels)
        val_loss, val_acc, val_f1 = evaluate(task, val, budget.batch_size, device, eval_loss_fn)
        record = EpochRecord(
            epoch=epoch,
            train_loss=round(running / max(seen, 1), 5),
            val_loss=round(val_loss, 5),
            val_accuracy=round(val_acc, 5),
            val_macro_f1=round(val_f1, 5),
            seconds=round(time.monotonic() - started, 2),
        )
        history.append(record)
        logger.info("epoch {}/{} {}", epoch, budget.epochs, asdict(record))
        if on_epoch is not None:
            on_epoch(record)
        if val_f1 > best_f1:
            best_f1 = val_f1
            best_state = copy.deepcopy({k: v.detach().cpu() for k, v in model.state_dict().items()})

    if best_state is not None:
        model.load_state_dict(best_state)
    model.to("cpu").eval()
    return history
