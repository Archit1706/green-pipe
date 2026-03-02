"""
Urgency classifier for GreenPipe.

Loads the fine-tuned DistilBERT model (full-precision or INT8-quantized)
and classifies commit messages as 'urgent', 'normal', or 'deferrable'.

Falls back to the keyword classifier when the model is not available so
the application remains functional before/without training.

Usage::

    clf = UrgencyClassifier.load("models/urgency_classifier")
    result = clf.classify(["hotfix: fix prod crash", "docs: update readme"])
    # result.urgency_class → 'urgent'
    # result.confidence    → 0.94
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transformers import DistilBertForSequenceClassification, DistilBertTokenizerFast

logger = logging.getLogger(__name__)

LABEL2ID: dict[str, int] = {"urgent": 0, "normal": 1, "deferrable": 2}
ID2LABEL: dict[int, str] = {v: k for k, v in LABEL2ID.items()}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """Result of classifying one or more commit messages."""

    urgency_class: str          # 'urgent' | 'normal' | 'deferrable'
    confidence: float           # probability of the predicted class (0–1)
    source: str                 # 'nlp_fp32' | 'nlp_int8' | 'keyword'
    probabilities: dict[str, float] | None = None  # full softmax distribution


# ---------------------------------------------------------------------------
# Keyword fallback (same logic as pipeline_analyzer stub)
# ---------------------------------------------------------------------------

_URGENT_KEYWORDS = frozenset(
    {"hotfix", "critical", "security", "emergency", "urgent", "incident", "fix!"}
)
_DEFERRABLE_KEYWORDS = frozenset(
    {"docs", "readme", "chore", "refactor", "style", "lint", "typo", "cleanup", "wip"}
)


def _keyword_classify(messages: list[str]) -> ClassificationResult:
    text = " ".join(messages).lower()
    tokens = set(re.split(r"[\s\W]+", text))
    if _URGENT_KEYWORDS & tokens:
        return ClassificationResult("urgent", 0.80, "keyword")
    if _DEFERRABLE_KEYWORDS & tokens:
        return ClassificationResult("deferrable", 0.75, "keyword")
    return ClassificationResult("normal", 0.65, "keyword")


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------


class UrgencyClassifier:
    """
    Wraps the fine-tuned DistilBERT model for commit-message urgency classification.

    Instantiate via UrgencyClassifier.load(model_dir) or use the module-level
    get_classifier() function which manages a singleton.
    """

    def __init__(
        self,
        model: DistilBertForSequenceClassification,
        tokenizer: DistilBertTokenizerFast,
        is_quantized: bool = False,
    ) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self._source = "nlp_int8" if is_quantized else "nlp_fp32"
        self._model.eval()

    @classmethod
    def load(cls, model_dir: str | Path) -> UrgencyClassifier:
        """
        Load the fine-tuned model from a local directory.

        The directory must contain the files saved by the training script:
        config.json, pytorch_model.bin (or model.safetensors), tokenizer files.

        Tries to load the INT8 quantized model first (model_quantized.pt);
        falls back to the standard safetensors/bin checkpoint.
        """
        import torch
        from transformers import DistilBertForSequenceClassification, DistilBertTokenizerFast

        model_dir = Path(model_dir)
        if not model_dir.exists():
            raise FileNotFoundError(f"Model directory not found: {model_dir}")

        tokenizer = DistilBertTokenizerFast.from_pretrained(str(model_dir))

        # Try quantized model first
        quantized_path = model_dir / "model_quantized.pt"
        if quantized_path.exists():
            logger.info("Loading INT8 quantized model from %s", quantized_path)
            model = torch.load(str(quantized_path), map_location="cpu", weights_only=False)
            return cls(model, tokenizer, is_quantized=True)

        # Full-precision checkpoint
        logger.info("Loading FP32 model from %s", model_dir)
        model = DistilBertForSequenceClassification.from_pretrained(str(model_dir))
        return cls(model, tokenizer, is_quantized=False)

    def classify(self, messages: list[str]) -> ClassificationResult:
        """
        Classify a list of commit messages as a single pipeline's urgency.

        Messages are concatenated (with space) before tokenisation.
        This captures cross-message signals (e.g. "security" in any message
        should dominate the classification).

        Returns
        -------
        ClassificationResult with urgency_class, confidence, and probabilities.
        """
        import torch
        import torch.nn.functional as F

        if not messages:
            return ClassificationResult("normal", 0.5, self._source)

        # Join messages; truncate to 512 chars to stay within token budget
        text = " [SEP] ".join(messages)[:512]

        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=128,
            padding="max_length",
        )

        with torch.no_grad():
            outputs = self._model(**inputs)
            probs = F.softmax(outputs.logits, dim=-1).squeeze()

        predicted_id = int(probs.argmax())
        confidence = float(probs[predicted_id])
        urgency_class = ID2LABEL[predicted_id]

        return ClassificationResult(
            urgency_class=urgency_class,
            confidence=confidence,
            source=self._source,
            probabilities={
                ID2LABEL[i]: round(float(p), 4)
                for i, p in enumerate(probs)
            },
        )


# ---------------------------------------------------------------------------
# Module-level singleton with lazy loading
# ---------------------------------------------------------------------------

_classifier_instance: UrgencyClassifier | None = None
_fallback_warned = False


def get_classifier(model_dir: str | Path = "models/urgency_classifier") -> UrgencyClassifier | None:
    """
    Return the module-level UrgencyClassifier singleton, loading it on first call.

    Returns None if the model directory does not exist (caller should use
    keyword fallback in that case).
    """
    global _classifier_instance, _fallback_warned

    if _classifier_instance is not None:
        return _classifier_instance

    model_path = Path(model_dir)
    if not model_path.exists():
        if not _fallback_warned:
            logger.warning(
                "NLP model not found at '%s'. Using keyword classifier fallback. "
                "Run 'python -m src.nlp.trainer' to train the model.",
                model_dir,
            )
            _fallback_warned = True
        return None

    try:
        _classifier_instance = UrgencyClassifier.load(model_path)
        logger.info("NLP urgency classifier loaded (%s).", _classifier_instance._source)
        return _classifier_instance
    except Exception as exc:
        logger.error("Failed to load NLP classifier: %s. Using keyword fallback.", exc)
        return None


def classify_urgency(
    messages: list[str],
    model_dir: str | Path = "models/urgency_classifier",
) -> ClassificationResult:
    """
    Convenience function: classify urgency using NLP model or keyword fallback.

    This is the single call site used by PipelineAnalyzer.
    """
    clf = get_classifier(model_dir)
    if clf is not None:
        return clf.classify(messages)
    return _keyword_classify(messages)
