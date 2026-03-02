"""
Commit message dataset for urgency classification.

Loads the labeled CSV, tokenises with DistilBERT tokenizer, and wraps
in a PyTorch Dataset ready for the Hugging Face Trainer.

Label mapping:
    0 → urgent      (hotfix, security, critical incidents)
    1 → normal      (features, bug fixes, dependency updates)
    2 → deferrable  (docs, refactors, style, chore)
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import Dataset
from transformers import PreTrainedTokenizerBase

logger = logging.getLogger(__name__)

LABEL2ID: dict[str, int] = {"urgent": 0, "normal": 1, "deferrable": 2}
ID2LABEL: dict[int, str] = {v: k for k, v in LABEL2ID.items()}

# Max token length — commit messages are short; 128 covers >99% without truncation
MAX_LENGTH = 128


class CommitMessageDataset(Dataset):
    """
    PyTorch Dataset wrapping tokenised commit messages.

    Parameters
    ----------
    messages:   list of raw commit message strings
    labels:     list of integer labels (0/1/2); None during inference
    tokenizer:  a Hugging Face tokenizer
    """

    def __init__(
        self,
        messages: list[str],
        labels: list[int] | None,
        tokenizer: PreTrainedTokenizerBase,
        max_length: int = MAX_LENGTH,
    ) -> None:
        self.encodings = tokenizer(
            messages,
            truncation=True,
            padding="max_length",
            max_length=max_length,
            return_tensors="pt",
        )
        self.labels = labels

    def __len__(self) -> int:
        return self.encodings["input_ids"].shape[0]

    def __getitem__(self, idx: int) -> dict[str, Any]:
        item: dict[str, Any] = {
            key: val[idx] for key, val in self.encodings.items()
        }
        if self.labels is not None:
            item["labels"] = torch.tensor(self.labels[idx], dtype=torch.long)
        return item


def load_csv(csv_path: str | Path) -> tuple[list[str], list[int]]:
    """
    Load the labeled commit message CSV.

    Returns
    -------
    (messages, labels) where labels are integer IDs.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Dataset not found: {csv_path}")

    messages: list[str] = []
    labels: list[int] = []
    skipped = 0

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            msg = row.get("message", "").strip()
            lbl = row.get("label", "").strip().lower()
            if not msg or lbl not in LABEL2ID:
                skipped += 1
                continue
            messages.append(msg)
            labels.append(LABEL2ID[lbl])

    if skipped:
        logger.warning("Skipped %d malformed rows in dataset.", skipped)

    logger.info(
        "Loaded %d examples from %s: urgent=%d normal=%d deferrable=%d",
        len(messages),
        csv_path.name,
        labels.count(0),
        labels.count(1),
        labels.count(2),
    )
    return messages, labels


def train_val_split(
    messages: list[str],
    labels: list[int],
    val_fraction: float = 0.15,
    seed: int = 42,
) -> tuple[list[str], list[int], list[str], list[int]]:
    """
    Stratified train/validation split preserving class proportions.

    Returns (train_msgs, train_labels, val_msgs, val_labels).
    """
    import random
    rng = random.Random(seed)

    # Group by label
    by_label: dict[int, list[int]] = {0: [], 1: [], 2: []}
    for i, lbl in enumerate(labels):
        by_label[lbl].append(i)

    train_idx: list[int] = []
    val_idx: list[int] = []

    for lbl, indices in by_label.items():
        shuffled = indices[:]
        rng.shuffle(shuffled)
        n_val = max(1, int(len(shuffled) * val_fraction))
        val_idx.extend(shuffled[:n_val])
        train_idx.extend(shuffled[n_val:])

    def _gather(idxs: list[int]) -> tuple[list[str], list[int]]:
        return [messages[i] for i in idxs], [labels[i] for i in idxs]

    train_m, train_l = _gather(train_idx)
    val_m, val_l = _gather(val_idx)

    logger.info(
        "Split: train=%d val=%d (%.0f%% val)",
        len(train_m), len(val_m), val_fraction * 100,
    )
    return train_m, train_l, val_m, val_l
