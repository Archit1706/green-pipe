"""
Pydantic schemas for GreenPipe GitLab Duo Agent tool calls and webhook payloads.

Tool inputs / outputs follow the GitLab Duo Agent Platform convention:
  - Tools are invoked via POST to /agent/tools/<tool_name>
  - Webhooks are received via POST to /agent/webhooks/<event_type>
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tool input schemas  (what the agent / caller sends)
# ---------------------------------------------------------------------------


class AnalyzePipelineInput(BaseModel):
    """Input for the analyze_pipeline agent tool."""

    project_id: int = Field(description="GitLab project ID")
    pipeline_id: int = Field(description="GitLab pipeline ID to analyze")
    runner_location: str | None = Field(
        default=None,
        description="Runner region string (e.g. 'us-east1'). "
                    "Inferred from job data when omitted.",
    )


class GenerateSCIReportInput(BaseModel):
    """Input for the generate_sci_report agent tool."""

    project_id: int = Field(description="GitLab project ID")
    pipeline_id: int = Field(description="GitLab pipeline ID")
    post_as_comment: bool = Field(
        default=False,
        description="If True, post the report as an MR comment via GitLab API.",
    )
    mr_iid: int | None = Field(
        default=None,
        description="MR IID to post the comment on. "
                    "Auto-discovered from the pipeline ref when omitted.",
    )
    runner_location: str | None = None


class SuggestSchedulingInput(BaseModel):
    """Input for the suggest_scheduling agent tool."""

    location: str = Field(
        default="us-east1",
        description="Carbon Aware SDK location string (e.g. 'us-east1', 'westeurope')",
    )
    duration_minutes: int = Field(
        default=10,
        ge=1,
        le=480,
        description="Expected pipeline duration in minutes (for window sizing)",
    )
    horizon_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="How many hours ahead to search for a low-carbon window",
    )


class ClassifyUrgencyInput(BaseModel):
    """Input for the classify_urgency agent tool."""

    commit_messages: list[str] = Field(
        default_factory=list,
        description="Commit messages to classify",
    )
    pipeline_id: int | None = Field(
        default=None,
        description="Optional: if provided and commit_messages is empty, "
                    "fetches messages from GitLab API (requires GITLAB_TOKEN).",
    )
    project_id: int | None = None


# ---------------------------------------------------------------------------
# Tool output schemas  (what the agent / caller receives)
# ---------------------------------------------------------------------------


class AnalyzePipelineOutput(BaseModel):
    """Output of the analyze_pipeline agent tool."""

    pipeline_id: int | None
    db_record_id: int | None = None
    sci_score_gco2e: float
    total_energy_kwh: float
    carbon_intensity_gco2_kwh: float
    urgency_class: str          # "urgent" | "normal" | "deferrable"
    urgency_confidence: float
    can_defer: bool
    scheduling_message: str
    runner_location: str
    gsf_standards_used: list[str]
    markdown_summary: str       # Short 1-paragraph human-readable summary


class GenerateSCIReportOutput(BaseModel):
    """Output of the generate_sci_report agent tool."""

    markdown_report: str
    sci_score_gco2e: float
    comment_posted: bool = False
    mr_iid: int | None = None
    comment_url: str | None = None


class SuggestSchedulingOutput(BaseModel):
    """Output of the suggest_scheduling agent tool."""

    location: str
    current_intensity_gco2_kwh: float | None = None
    best_window: dict[str, Any] | None = None
    forecast_available: bool = False
    message: str


class ClassifyUrgencyOutput(BaseModel):
    """Output of the classify_urgency agent tool."""

    urgency_class: str          # "urgent" | "normal" | "deferrable"
    confidence: float
    source: str                 # "keyword" | "nlp_fp32" | "nlp_int8"
    can_defer: bool
    explanation: str


class AnalyzeCodeEfficiencyInput(BaseModel):
    """Input for the analyze_code_efficiency agent tool."""

    project_id: int | None = Field(
        default=None,
        description="GitLab project ID. When provided with mr_iid, the diff is "
                    "fetched automatically from GitLab.",
    )
    mr_iid: int | None = Field(
        default=None,
        description="Merge request IID. Used with project_id to fetch the diff.",
    )
    diff_text: str | None = Field(
        default=None,
        description="Raw diff text to analyse. Use this instead of project_id/mr_iid "
                    "for offline or manual analysis.",
    )


class CodeEfficiencySuggestion(BaseModel):
    """A single code efficiency suggestion from Claude."""

    file: str = Field(description="File path from the diff")
    line_range: str = Field(description="Affected line range (e.g. '47-53')")
    issue_type: str = Field(description="Category: n_plus_one_query, missing_cache, unbounded_loop, etc.")
    description: str = Field(description="Human-readable description of the inefficiency")
    estimated_energy_impact: str = Field(description="low | medium | high")
    suggested_fix: str = Field(description="Actionable fix suggestion")


class AnalyzeCodeEfficiencyOutput(BaseModel):
    """Output of the analyze_code_efficiency agent tool."""

    suggestions: list[CodeEfficiencySuggestion] = Field(default_factory=list)
    overall_assessment: str = ""
    estimated_energy_reduction: str = ""
    model_used: str = ""
    tokens_used: int = 0
    error: str | None = None
    diff_source: str = ""  # "gitlab_mr" | "manual" | "unavailable"


# ---------------------------------------------------------------------------
# GitLab webhook payload schemas
# ---------------------------------------------------------------------------


class GitLabPipelineEvent(BaseModel):
    """
    GitLab pipeline event webhook payload.

    Reference: https://docs.gitlab.com/ee/user/project/integrations/webhook_events.html#pipeline-events
    """

    object_kind: str = "pipeline"
    object_attributes: dict[str, Any] = Field(default_factory=dict)
    project: dict[str, Any] = Field(default_factory=dict)
    user: dict[str, Any] = Field(default_factory=dict)
    # Jobs list (present in full pipeline events, may be absent in system hooks)
    builds: list[dict[str, Any]] = Field(default_factory=list)
    # Merge request context, if available
    merge_request: dict[str, Any] | None = None

    @property
    def pipeline_id(self) -> int | None:
        return self.object_attributes.get("id")

    @property
    def project_id(self) -> int | None:
        return self.project.get("id")

    @property
    def status(self) -> str:
        return self.object_attributes.get("status", "")

    @property
    def ref(self) -> str:
        return self.object_attributes.get("ref", "")


class GitLabNoteEvent(BaseModel):
    """
    GitLab note (comment) event webhook payload.

    Posted when a user adds a comment on an issue, MR, commit, or snippet.
    Reference: https://docs.gitlab.com/ee/user/project/integrations/webhook_events.html#comment-events
    """

    object_kind: str = "note"
    user: dict[str, Any] = Field(default_factory=dict)
    project: dict[str, Any] = Field(default_factory=dict)
    object_attributes: dict[str, Any] = Field(default_factory=dict)
    merge_request: dict[str, Any] | None = None
    issue: dict[str, Any] | None = None

    @property
    def note_body(self) -> str:
        return self.object_attributes.get("note", "")

    @property
    def project_id(self) -> int | None:
        return self.project.get("id")

    @property
    def mr_iid(self) -> int | None:
        if self.merge_request:
            return self.merge_request.get("iid")
        return None

    @property
    def noteable_type(self) -> str:
        return self.object_attributes.get("noteable_type", "")


# ---------------------------------------------------------------------------
# Generic agent response envelope
# ---------------------------------------------------------------------------


class DeferralDecision(BaseModel):
    """Details of an auto-deferral decision embedded in webhook responses."""

    action: str = Field(
        description="Action taken: 'none' | 'recommended' | 'awaiting_approval' | 'deferred' | 'force_run'"
    )
    policy_mode: str = Field(description="Active deferral policy mode")
    reason: str = Field(description="Human-readable reason for the decision")
    pipeline_cancelled: bool = False
    schedule_id: int | None = None
    schedule_cron: str | None = None
    target_window: str | None = None
    predicted_savings_pct: float | None = None
    original_intensity_gco2_kwh: float | None = None
    target_intensity_gco2_kwh: float | None = None


class AgentWebhookResponse(BaseModel):
    """Standard envelope returned by all webhook handlers."""

    status: str                    # "accepted" | "skipped" | "error"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    deferral: DeferralDecision | None = None
