"""
Tests for the NLP urgency classifier module.

These tests run without a trained model — they validate the keyword
fallback path and the dataset loading utilities.
"""

import csv
import tempfile
from pathlib import Path

import pytest

from src.nlp.classifier import ClassificationResult, _keyword_classify, classify_urgency
from src.nlp.dataset import LABEL2ID, load_csv, train_val_split


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------


class TestKeywordFallback:
    def test_urgent_keywords(self):
        for msg in [
            "hotfix: fix prod crash",
            "critical: null pointer in auth",
            "security patch for CVE-2024",
            "emergency rollback needed",
            "incident: service down",
        ]:
            result = _keyword_classify([msg])
            assert result.urgency_class == "urgent", f"Expected urgent for: {msg!r}"
            assert result.source == "keyword"

    def test_deferrable_keywords(self):
        for msg in [
            "docs: update README",
            "chore: clean up unused imports",
            "refactor: extract helper functions",
            "style: apply Black formatting",
            "wip: spike on new architecture",
        ]:
            result = _keyword_classify([msg])
            assert result.urgency_class == "deferrable", f"Expected deferrable for: {msg!r}"

    def test_normal_default(self):
        result = _keyword_classify(["feat: add user profile page"])
        assert result.urgency_class == "normal"
        assert result.confidence == pytest.approx(0.65)

    def test_empty_messages_is_normal(self):
        result = _keyword_classify([])
        assert result.urgency_class == "normal"

    def test_punctuation_stripped(self):
        # 'hotfix:' with colon must still match 'hotfix'
        result = _keyword_classify(["hotfix: critical auth bypass"])
        assert result.urgency_class == "urgent"

    def test_urgent_overrides_deferrable(self):
        # If both urgent and deferrable keywords present, urgent wins
        result = _keyword_classify(["hotfix: refactor payment module"])
        assert result.urgency_class == "urgent"

    def test_multiple_messages_combined(self):
        # Urgency signal in any message should dominate
        result = _keyword_classify(["docs: update readme", "hotfix: fix crash"])
        assert result.urgency_class == "urgent"


# ---------------------------------------------------------------------------
# classify_urgency (NLP path disabled, falls back to keyword)
# ---------------------------------------------------------------------------


class TestClassifyUrgency:
    def test_returns_classification_result(self):
        result = classify_urgency(["feat: add dark mode"])
        assert isinstance(result, ClassificationResult)
        assert result.urgency_class in {"urgent", "normal", "deferrable"}
        assert 0.0 <= result.confidence <= 1.0
        assert result.source in {"keyword", "nlp_fp32", "nlp_int8"}

    def test_nonexistent_model_uses_fallback(self, tmp_path):
        result = classify_urgency(["hotfix: fix"], model_dir=str(tmp_path / "no_model"))
        # Should still return a valid result via keyword fallback
        assert result.urgency_class == "urgent"
        assert result.source == "keyword"


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


class TestDatasetLoader:
    def _make_csv(self, rows: list[tuple[str, str]], path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["message", "label"])
            writer.writeheader()
            for msg, lbl in rows:
                writer.writerow({"message": msg, "label": lbl})

    def test_load_csv_basic(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        self._make_csv([
            ("hotfix: crash fix", "urgent"),
            ("feat: add feature", "normal"),
            ("docs: update", "deferrable"),
        ], csv_path)
        messages, labels = load_csv(csv_path)
        assert len(messages) == 3
        assert labels == [LABEL2ID["urgent"], LABEL2ID["normal"], LABEL2ID["deferrable"]]

    def test_load_csv_skips_invalid_labels(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        self._make_csv([
            ("valid message", "urgent"),
            ("bad row", "unknown_label"),
            ("another valid", "normal"),
        ], csv_path)
        messages, labels = load_csv(csv_path)
        assert len(messages) == 2

    def test_load_csv_skips_empty_messages(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        self._make_csv([
            ("", "urgent"),
            ("valid message", "normal"),
        ], csv_path)
        messages, labels = load_csv(csv_path)
        assert len(messages) == 1

    def test_load_real_dataset(self):
        """Verify the actual dataset file is clean."""
        messages, labels = load_csv("data/commit_messages.csv")
        assert len(messages) >= 200
        assert set(labels).issubset({0, 1, 2})
        # All three classes present
        assert 0 in labels
        assert 1 in labels
        assert 2 in labels

    def test_train_val_split_preserves_size(self):
        messages, labels = load_csv("data/commit_messages.csv")
        train_m, train_l, val_m, val_l = train_val_split(messages, labels, val_fraction=0.15)
        assert len(train_m) + len(val_m) == len(messages)
        assert len(train_l) + len(val_l) == len(labels)

    def test_train_val_split_all_classes_in_val(self):
        """Stratified split must include all 3 classes in validation set."""
        messages, labels = load_csv("data/commit_messages.csv")
        _, _, _, val_labels = train_val_split(messages, labels, val_fraction=0.15)
        assert set(val_labels) == {0, 1, 2}
