"""
Green Code Profiler — Anthropic Claude-powered code efficiency analysis.

Sends MR diffs to Claude for energy-efficiency review, returning structured
suggestions aligned with GSF principles: reduce computation, minimise I/O,
avoid redundant work.

Architecture:
    GreenPipe uses a **hybrid AI approach**:
    - A tiny INT8 DistilBERT handles frequent, low-latency urgency classification
    - Claude handles on-demand, deep code quality analysis
    This ensures the agent itself practices sustainable design (small model for
    high-frequency tasks, large model only when needed).

Usage:
    analyzer = CodeAnalyzer()
    suggestions = await analyzer.analyze_diff(diff_text)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.config import settings

# Lazy import — anthropic is only required when CodeAnalyzer is instantiated.
try:
    import anthropic

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes for structured output
# ---------------------------------------------------------------------------


@dataclass
class EfficiencySuggestion:
    """A single code efficiency suggestion returned by Claude."""

    file: str
    line_range: str
    issue_type: str
    description: str
    estimated_energy_impact: str  # "low" | "medium" | "high"
    suggested_fix: str


@dataclass
class CodeAnalysisResult:
    """Complete result of a code efficiency analysis."""

    suggestions: list[EfficiencySuggestion] = field(default_factory=list)
    overall_assessment: str = ""
    estimated_energy_reduction: str = ""
    model_used: str = ""
    tokens_used: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a green software engineering assistant integrated into GreenPipe, a \
GSF-compliant carbon-aware CI/CD agent. Your task is to analyse code diffs \
for energy efficiency issues.

Focus on:
1. **N+1 queries** — repeated database calls that could be batched or eager-loaded
2. **Missing caching** — repeated expensive computations or API calls
3. **Unbounded loops** — loops without limits that could process excessive data
4. **Synchronous I/O** — blocking calls where async would reduce idle CPU time
5. **Over-computation** — calculating values that are never used or already available
6. **Redundant work** — duplicate processing, unnecessary re-renders, repeated parsing
7. **Memory waste** — loading entire datasets when only a subset is needed
8. **Inefficient algorithms** — O(n²) where O(n log n) or O(n) is possible

Return your analysis as a JSON object with this exact structure:
{
  "suggestions": [
    {
      "file": "path/to/file.py",
      "line_range": "47-53",
      "issue_type": "n_plus_one_query",
      "description": "Clear description of the inefficiency",
      "estimated_energy_impact": "low|medium|high",
      "suggested_fix": "Specific actionable fix"
    }
  ],
  "overall_assessment": "Brief summary of the diff's energy efficiency",
  "estimated_energy_reduction": "Estimated improvement if all suggestions applied (e.g. '10-30%')"
}

Rules:
- Only report genuine inefficiencies, not style or formatting issues
- Be specific about line numbers (from the diff) and file paths
- Rate energy impact: "high" = reduces CPU/IO substantially, "medium" = moderate \
savings, "low" = minor optimisation
- If the diff looks efficient, return an empty suggestions list with a positive assessment
- Return ONLY the JSON object, no markdown fences or extra text
"""

# Maximum diff size sent to Claude (characters). Larger diffs are truncated
# to control cost and stay within context limits.
_MAX_DIFF_CHARS = 30_000


# ---------------------------------------------------------------------------
# Code analyzer
# ---------------------------------------------------------------------------


class CodeAnalyzer:
    """
    Thin wrapper around the Anthropic Claude API for green code profiling.

    Gracefully disabled when ``ANTHROPIC_API_KEY`` is blank or the anthropic
    package is not installed — returns an error result instead of raising.
    """

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None
        self._available = False

        if not settings.anthropic_api_key:
            logger.info(
                "CodeAnalyzer disabled: ANTHROPIC_API_KEY not configured."
            )
            return

        if not _ANTHROPIC_AVAILABLE:
            logger.warning(
                "CodeAnalyzer disabled: anthropic package not installed. "
                "Install with: pip install anthropic"
            )
            return

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._available = True
        logger.info(
            "CodeAnalyzer initialised with model=%s", settings.anthropic_model
        )

    @property
    def is_available(self) -> bool:
        """Whether the analyzer is ready to process requests."""
        return self._available

    async def analyze_diff(self, diff_text: str) -> CodeAnalysisResult:
        """
        Send a git diff to Claude for energy-efficiency analysis.

        Args:
            diff_text: Combined diff text from a merge request.

        Returns:
            CodeAnalysisResult with structured suggestions. If the service
            is unavailable or the API call fails, the ``error`` field is set
            and ``suggestions`` is empty.
        """
        if not self._available or self._client is None:
            return CodeAnalysisResult(
                error="Code analyzer unavailable: ANTHROPIC_API_KEY not configured "
                      "or anthropic package not installed.",
            )

        if not diff_text or not diff_text.strip():
            return CodeAnalysisResult(
                overall_assessment="No diff content provided.",
                error="Empty diff text.",
            )

        # Truncate very large diffs to control cost
        truncated = False
        if len(diff_text) > _MAX_DIFF_CHARS:
            diff_text = diff_text[:_MAX_DIFF_CHARS]
            truncated = True

        model = settings.anthropic_model or "claude-sonnet-4-6"

        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Analyse this git diff for energy efficiency:\n\n{diff_text}"
                            + (
                                "\n\n(Note: diff was truncated to 30k characters)"
                                if truncated
                                else ""
                            )
                        ),
                    }
                ],
            )
        except Exception as exc:
            logger.error("Claude API call failed: %s", exc, exc_info=True)
            return CodeAnalysisResult(
                error=f"Claude API error: {exc}",
                model_used=model,
            )

        # Extract usage
        tokens_used = 0
        if hasattr(response, "usage") and response.usage:
            tokens_used = (
                getattr(response.usage, "input_tokens", 0)
                + getattr(response.usage, "output_tokens", 0)
            )

        # Parse the response
        raw_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw_text += block.text

        return self._parse_response(raw_text, model, tokens_used)

    def _parse_response(
        self, raw_text: str, model: str, tokens_used: int
    ) -> CodeAnalysisResult:
        """Parse Claude's JSON response into a CodeAnalysisResult."""
        # Strip markdown fences if present (Claude sometimes adds them)
        text = raw_text.strip()
        if text.startswith("```"):
            # Remove opening fence (```json or ```)
            first_newline = text.index("\n") if "\n" in text else len(text)
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Could not parse Claude response as JSON: %s", exc)
            return CodeAnalysisResult(
                overall_assessment=raw_text[:500],
                error=f"Failed to parse structured response: {exc}",
                model_used=model,
                tokens_used=tokens_used,
            )

        suggestions = []
        for s in data.get("suggestions", []):
            suggestions.append(
                EfficiencySuggestion(
                    file=s.get("file", "unknown"),
                    line_range=s.get("line_range", ""),
                    issue_type=s.get("issue_type", "unknown"),
                    description=s.get("description", ""),
                    estimated_energy_impact=s.get(
                        "estimated_energy_impact", "low"
                    ),
                    suggested_fix=s.get("suggested_fix", ""),
                )
            )

        return CodeAnalysisResult(
            suggestions=suggestions,
            overall_assessment=data.get("overall_assessment", ""),
            estimated_energy_reduction=data.get(
                "estimated_energy_reduction", ""
            ),
            model_used=model,
            tokens_used=tokens_used,
        )
