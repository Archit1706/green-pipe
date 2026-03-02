"""
DistilBERT fine-tuning script for commit message urgency classification.

Run from the project root:

    python -m src.nlp.trainer \\
        --data data/commit_messages.csv \\
        --output models/urgency_classifier \\
        --epochs 5

The trained model is saved to --output and can be loaded directly by the
UrgencyClassifier inference wrapper.

References:
- DistilBERT: https://huggingface.co/distilbert/distilbert-base-uncased
- HF Trainer API: https://huggingface.co/docs/transformers/main_classes/trainer
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _compute_metrics(eval_pred: tuple) -> dict[str, float]:
    """Compute accuracy and per-class F1 for the Trainer."""
    from sklearn.metrics import accuracy_score, classification_report

    logits, label_ids = eval_pred
    predictions = np.argmax(logits, axis=-1)

    acc = accuracy_score(label_ids, predictions)
    report = classification_report(
        label_ids,
        predictions,
        target_names=["urgent", "normal", "deferrable"],
        output_dict=True,
        zero_division=0,
    )

    return {
        "accuracy": acc,
        "f1_urgent": report["urgent"]["f1-score"],
        "f1_normal": report["normal"]["f1-score"],
        "f1_deferrable": report["deferrable"]["f1-score"],
        "f1_macro": report["macro avg"]["f1-score"],
    }


def train(
    data_path: str,
    output_dir: str,
    base_model: str = "distilbert-base-uncased",
    num_epochs: int = 5,
    batch_size: int = 16,
    learning_rate: float = 2e-5,
    seed: int = 42,
) -> dict[str, float]:
    """
    Fine-tune DistilBERT on the commit message dataset.

    Returns the final evaluation metrics.
    """
    import torch
    from transformers import (
        DistilBertForSequenceClassification,
        DistilBertTokenizerFast,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    from src.nlp.dataset import ID2LABEL, LABEL2ID, CommitMessageDataset, load_csv, train_val_split

    set_seed(seed)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # ------------------------------------------------------------------
    # 1. Load and split data
    # ------------------------------------------------------------------
    logger.info("Loading dataset from %s", data_path)
    messages, labels = load_csv(data_path)
    train_msgs, train_labels, val_msgs, val_labels = train_val_split(
        messages, labels, val_fraction=0.15, seed=seed
    )

    # ------------------------------------------------------------------
    # 2. Tokenise
    # ------------------------------------------------------------------
    logger.info("Loading tokenizer: %s", base_model)
    tokenizer = DistilBertTokenizerFast.from_pretrained(base_model)

    train_dataset = CommitMessageDataset(train_msgs, train_labels, tokenizer)
    val_dataset = CommitMessageDataset(val_msgs, val_labels, tokenizer)

    # ------------------------------------------------------------------
    # 3. Load model
    # ------------------------------------------------------------------
    logger.info("Loading model: %s (num_labels=3)", base_model)
    model = DistilBertForSequenceClassification.from_pretrained(
        base_model,
        num_labels=3,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Training device: %s", device)

    # ------------------------------------------------------------------
    # 4. Training arguments
    # ------------------------------------------------------------------
    training_args = TrainingArguments(
        output_dir=str(output_path / "checkpoints"),
        num_train_epochs=num_epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=learning_rate,
        weight_decay=0.01,
        warmup_ratio=0.1,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="f1_macro",
        greater_is_better=True,
        logging_steps=10,
        seed=seed,
        report_to="none",    # disable wandb / mlflow
        fp16=torch.cuda.is_available(),
    )

    # ------------------------------------------------------------------
    # 5. Train
    # ------------------------------------------------------------------
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=_compute_metrics,
    )

    logger.info("Starting training (%d epochs, lr=%.2e)", num_epochs, learning_rate)
    trainer.train()

    # ------------------------------------------------------------------
    # 6. Evaluate and save
    # ------------------------------------------------------------------
    metrics = trainer.evaluate()
    logger.info("Final eval metrics: %s", metrics)

    logger.info("Saving model to %s", output_path)
    trainer.save_model(str(output_path))
    tokenizer.save_pretrained(str(output_path))

    # Save training metadata for documentation / submission
    meta = {
        "base_model": base_model,
        "num_epochs": num_epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "train_samples": len(train_dataset),
        "val_samples": len(val_dataset),
        "label_map": LABEL2ID,
        "final_metrics": {k: round(v, 4) for k, v in metrics.items()},
        "device": device,
    }
    meta_path = output_path / "training_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    logger.info("Training metadata saved to %s", meta_path)

    return metrics


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fine-tune DistilBERT for urgency classification")
    parser.add_argument("--data", default="data/commit_messages.csv")
    parser.add_argument("--output", default="models/urgency_classifier")
    parser.add_argument("--base-model", default="distilbert-base-uncased")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    metrics = train(
        data_path=args.data,
        output_dir=args.output,
        base_model=args.base_model,
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        seed=args.seed,
    )
    print("\nTraining complete. Final metrics:")
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
