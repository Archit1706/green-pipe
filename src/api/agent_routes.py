"""
GitLab Duo Agent routes for GreenPipe.

Exposes two FastAPI routers:

  agent_tools_router   — /agent/tools/*
    Tools called by the GitLab Duo Agent when the user asks GreenPipe to do
    something.  Each tool wraps the existing service layer and returns
    structured JSON.

  webhook_router       — /agent/webhooks/*
    Receives GitLab system-hook / project-webhook events.  Two triggers are
    supported:
      - Pipeline completion  →  auto-analyse and optionally comment on the MR
      - @greenpipe mention   →  parse the command and reply in the MR thread

Security:
  Both webhook endpoints verify the X-Gitlab-Token header against
  GITLAB_WEBHOOK_SECRET when that setting is non-empty.  Tool endpoints are
  currently unauthenticated (auth middleware TODO in main.py).
"""

from __future__ import annotations

import fnmatch
import hmac
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from src.api.agent_schemas import (
    AgentWebhookResponse,
    AnalyzeCodeEfficiencyInput,
    AnalyzeCodeEfficiencyOutput,
    AnalyzePipelineInput,
    AnalyzePipelineOutput,
    ClassifyUrgencyInput,
    ClassifyUrgencyOutput,
    CodeEfficiencySuggestion,
    DeferralDecision,
    GenerateSCIReportInput,
    GenerateSCIReportOutput,
    GitLabNoteEvent,
    GitLabPipelineEvent,
    SuggestSchedulingInput,
    SuggestSchedulingOutput,
)
from src.api.report_formatter import (
    format_code_efficiency_comment,
    format_deferral_comment,
    format_help_comment,
    format_mr_comment,
)
from src.config import settings
from src.nlp.classifier import classify_urgency
from src.services.carbon_service import CarbonService
from src.services.code_analyzer import CodeAnalyzer
from src.services.pipeline_analyzer import PipelineAnalyzer, PipelineAnalysisReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared service singletons
# ---------------------------------------------------------------------------

_carbon_service = CarbonService()
_analyzer = PipelineAnalyzer(carbon_service=_carbon_service)
_code_analyzer = CodeAnalyzer()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = {"success", "failed", "canceled"}


def _verify_webhook_token(x_gitlab_token: str | None) -> None:
    """
    Reject the request if the webhook secret is configured but the header
    does not match.  A blank GITLAB_WEBHOOK_SECRET disables verification
    (suitable for local development).
    """
    secret = settings.gitlab_webhook_secret
    if not secret:
        return  # verification disabled
    if not hmac.compare_digest(x_gitlab_token or "", secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Gitlab-Token.",
        )


def _build_markdown_summary(report: Any) -> str:
    """One-paragraph human-readable summary of an analysis report."""
    defer_text = (
        "Consider deferring to a lower-carbon window."
        if report.can_defer
        else "Proceed as scheduled."
    )
    return (
        f"Pipeline analysed: **{report.total_energy_kwh:.5f} kWh** consumed, "
        f"**{report.sci.sci_score:.3f} gCO₂e** SCI score "
        f"(carbon intensity: {report.carbon_intensity_gco2_kwh:.1f} gCO₂e/kWh "
        f"@ {report.runner_location}). "
        f"Urgency: **{report.urgency_class}** "
        f"({report.urgency_confidence:.0%} confidence). "
        f"{defer_text}"
    )


# ---------------------------------------------------------------------------
# Auto-deferral engine
# ---------------------------------------------------------------------------

_VALID_DEFER_MODES = {"recommend-only", "approval-required", "auto-execute"}


def _is_protected_ref(ref: str) -> bool:
    """
    Check if a branch/tag ref matches any pattern in GREENPIPE_PROTECTED_BRANCHES.

    Supports trailing ``*`` glob (e.g. ``release*`` matches ``release/1.0``).
    """
    patterns = [
        p.strip()
        for p in settings.greenpipe_protected_branches.split(",")
        if p.strip()
    ]
    for pattern in patterns:
        if fnmatch.fnmatch(ref, pattern):
            return True
    return False


def _datetime_to_cron(dt: datetime) -> str:
    """Convert a datetime to a cron expression ``M H * * *``."""
    return f"{dt.minute} {dt.hour} * * *"


def _parse_iso_window(ts: str | None) -> datetime | None:
    """Best-effort parse of a scheduling window timestamp string."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


async def _evaluate_deferral(
    report: PipelineAnalysisReport,
    pipeline_id: int,
    project_id: int,
    ref: str,
) -> DeferralDecision:
    """
    Core deferral decision engine.

    Evaluates whether a pipeline should be deferred based on:
    - Urgency classification
    - Carbon savings vs. policy threshold
    - Protected branch/environment list
    - Active deferral mode

    Returns a ``DeferralDecision`` describing the action taken (or recommended).
    This function **never raises**; errors are captured in the decision reason.
    """
    mode = settings.greenpipe_defer_mode
    if mode not in _VALID_DEFER_MODES:
        mode = "recommend-only"

    base = DeferralDecision(
        action="none",
        policy_mode=mode,
        reason="",
    )

    # Gate: only deferrable pipelines are candidates
    if not report.can_defer:
        base.action = "none"
        base.reason = (
            f"Pipeline urgency is '{report.urgency_class}' — not a deferral candidate."
        )
        return base

    # Gate: protected branches
    if ref and _is_protected_ref(ref):
        base.action = "none"
        base.reason = f"Branch '{ref}' is protected — deferral blocked by policy."
        return base

    # Gate: need a scheduling window with sufficient savings
    window = report.scheduling_window
    if window is None:
        base.action = "none"
        base.reason = "No lower-carbon window found in forecast — deferral not beneficial."
        return base

    savings = window.savings_percent
    min_savings = settings.greenpipe_min_savings_pct
    if savings < min_savings:
        base.action = "none"
        base.reason = (
            f"Predicted savings ({savings:.1f}%) below policy threshold "
            f"({min_savings:.1f}%) — deferral not triggered."
        )
        return base

    # Populate carbon context
    base.original_intensity_gco2_kwh = round(report.carbon_intensity_gco2_kwh, 2)
    base.target_intensity_gco2_kwh = round(window.intensity_gco2_kwh, 2)
    base.target_window = window.timestamp
    base.predicted_savings_pct = round(savings, 1)

    # --- recommend-only: advise but take no action ---
    if mode == "recommend-only":
        base.action = "recommended"
        base.reason = (
            f"Deferral recommended: {savings:.1f}% carbon savings available "
            f"at {window.timestamp}. Set GREENPIPE_DEFER_MODE=auto-execute to "
            f"enable automatic deferral."
        )
        return base

    # --- approval-required: post prompt, wait for @greenpipe confirm-defer ---
    if mode == "approval-required":
        base.action = "awaiting_approval"
        base.reason = (
            f"Deferral pending approval: {savings:.1f}% carbon savings at "
            f"{window.timestamp}. Reply `@greenpipe confirm-defer` to approve "
            f"or `@greenpipe run-now` to skip."
        )
        return base

    # --- auto-execute: cancel pipeline + create schedule ---
    target_dt = _parse_iso_window(window.timestamp)
    if target_dt is None:
        base.action = "recommended"
        base.reason = (
            f"Deferral recommended but could not parse target window timestamp "
            f"'{window.timestamp}' — manual action required."
        )
        return base

    # Check max delay policy
    now = datetime.now(timezone.utc)
    delay_hours = (target_dt - now).total_seconds() / 3600
    if delay_hours > settings.greenpipe_max_delay_hours:
        base.action = "recommended"
        base.reason = (
            f"Best window is {delay_hours:.1f}h away, exceeding max delay of "
            f"{settings.greenpipe_max_delay_hours}h — recommending only."
        )
        return base

    # Execute: cancel + schedule
    cron = _datetime_to_cron(target_dt)
    cancelled = False
    schedule_id: int | None = None

    if _analyzer._gitlab is not None:
        cancelled = _analyzer._gitlab.cancel_pipeline(project_id, pipeline_id)
        schedule_id = _analyzer._gitlab.create_pipeline_schedule(
            project_id=project_id,
            ref=ref,
            cron=cron,
            description=(
                f"GreenPipe auto-deferred: {savings:.0f}% carbon savings "
                f"({report.carbon_intensity_gco2_kwh:.0f} → "
                f"{window.intensity_gco2_kwh:.0f} gCO₂e/kWh)"
            ),
        )

    base.action = "deferred"
    base.pipeline_cancelled = cancelled
    base.schedule_id = schedule_id
    base.schedule_cron = cron
    base.reason = (
        f"Pipeline auto-deferred: cancelled pipeline {pipeline_id} and "
        f"scheduled re-run at {window.timestamp} (cron: {cron}). "
        f"Predicted carbon savings: {savings:.1f}%."
    )
    return base


# ---------------------------------------------------------------------------
# Agent tools router
# ---------------------------------------------------------------------------

agent_tools_router = APIRouter(prefix="/agent/tools", tags=["Agent Tools"])


@agent_tools_router.post(
    "/analyze_pipeline",
    response_model=AnalyzePipelineOutput,
    summary="Analyze a pipeline using GSF SCI methodology",
)
async def tool_analyze_pipeline(
    body: AnalyzePipelineInput,
) -> AnalyzePipelineOutput:
    """
    Fetch a GitLab pipeline's jobs and commits, estimate energy (GSF Impact
    Framework), calculate SCI (ISO/IEC 21031:2024), and classify urgency.

    Requires GITLAB_TOKEN to be configured.
    """
    try:
        report = await _analyzer.analyze_from_gitlab(
            project_id=body.project_id,
            pipeline_id=body.pipeline_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("analyze_pipeline tool failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Analysis error. Check server logs for details.",
        ) from exc

    return AnalyzePipelineOutput(
        pipeline_id=body.pipeline_id,
        sci_score_gco2e=round(report.sci.sci_score, 6),
        total_energy_kwh=round(report.total_energy_kwh, 8),
        carbon_intensity_gco2_kwh=round(report.carbon_intensity_gco2_kwh, 2),
        urgency_class=report.urgency_class,
        urgency_confidence=round(report.urgency_confidence, 4),
        can_defer=report.can_defer,
        scheduling_message=report.scheduling_message,
        runner_location=report.runner_location,
        gsf_standards_used=report.gsf_standards_used,
        markdown_summary=_build_markdown_summary(report),
    )


@agent_tools_router.post(
    "/generate_sci_report",
    response_model=GenerateSCIReportOutput,
    summary="Generate a formatted SCI report (optionally post as MR comment)",
)
async def tool_generate_sci_report(
    body: GenerateSCIReportInput,
) -> GenerateSCIReportOutput:
    """
    Run a full GSF analysis and render the result as GitLab-flavoured markdown.

    If `post_as_comment=True` and a GitLab client is configured, the report
    is posted directly to the merge request identified by `mr_iid`.
    """
    try:
        report = await _analyzer.analyze_from_gitlab(
            project_id=body.project_id,
            pipeline_id=body.pipeline_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("generate_sci_report tool failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Analysis error. Check server logs for details.",
        ) from exc

    markdown = format_mr_comment(report)
    comment_posted = False
    comment_url: str | None = None
    mr_iid = body.mr_iid

    if body.post_as_comment and _analyzer._gitlab is not None:
        # Auto-discover MR if not provided
        if mr_iid is None:
            mr_iid = _analyzer._gitlab.find_mr_for_pipeline(
                body.project_id, body.pipeline_id
            )
        if mr_iid is not None:
            ok = _analyzer._gitlab.post_mr_comment(body.project_id, mr_iid, markdown)
            if ok:
                comment_posted = True
                logger.info(
                    "SCI report posted to MR !%s on project %s",
                    mr_iid, body.project_id,
                )
        else:
            logger.info(
                "No open MR found for pipeline %s — report not posted.",
                body.pipeline_id,
            )

    return GenerateSCIReportOutput(
        markdown_report=markdown,
        sci_score_gco2e=round(report.sci.sci_score, 6),
        comment_posted=comment_posted,
        mr_iid=mr_iid,
        comment_url=comment_url,
    )


@agent_tools_router.post(
    "/suggest_scheduling",
    response_model=SuggestSchedulingOutput,
    summary="Find the lowest-carbon execution window for a pipeline",
)
async def tool_suggest_scheduling(
    body: SuggestSchedulingInput,
) -> SuggestSchedulingOutput:
    """
    Query the GSF Carbon Aware SDK forecast to find the lowest-carbon
    execution window within the next `horizon_hours`.
    """
    # Current intensity (for context)
    try:
        current_intensity, _ = await _carbon_service.get_intensity(body.location)
    except Exception:
        current_intensity = None

    best_window = None
    forecast_available = False
    try:
        best_window = await _carbon_service.find_best_execution_window(
            location=body.location,
            duration_minutes=body.duration_minutes,
            horizon_hours=body.horizon_hours,
        )
        if best_window:
            forecast_available = True
    except Exception as exc:
        logger.warning("suggest_scheduling: forecast lookup failed: %s", exc)

    if best_window and current_intensity is not None:
        savings_pct = max(
            0.0,
            (current_intensity - best_window["intensity_gco2_kwh"]) / current_intensity * 100,
        )
        message = (
            f"Best low-carbon window at {best_window.get('timestamp', 'TBD')}: "
            f"{best_window['intensity_gco2_kwh']:.1f} gCO₂e/kWh "
            f"({savings_pct:.1f}% lower than current {current_intensity:.1f} gCO₂e/kWh)."
        )
    elif best_window:
        message = (
            f"Best window at {best_window.get('timestamp', 'TBD')}: "
            f"{best_window['intensity_gco2_kwh']:.1f} gCO₂e/kWh."
        )
    else:
        ci_str = f"{current_intensity:.1f} gCO₂e/kWh" if current_intensity else "unknown"
        message = (
            f"No forecast data available for '{body.location}'. "
            f"Current intensity: {ci_str}."
        )

    return SuggestSchedulingOutput(
        location=body.location,
        current_intensity_gco2_kwh=round(current_intensity, 2) if current_intensity else None,
        best_window=best_window,
        forecast_available=forecast_available,
        message=message,
    )


@agent_tools_router.post(
    "/classify_urgency",
    response_model=ClassifyUrgencyOutput,
    summary="Classify pipeline urgency from commit messages",
)
async def tool_classify_urgency(
    body: ClassifyUrgencyInput,
) -> ClassifyUrgencyOutput:
    """
    Classify commit message urgency using the DistilBERT model (INT8 quantized)
    or the keyword-based fallback when the model is not available.

    Supported urgency classes:
    - `urgent`     — hotfix, critical, security patch → run immediately
    - `normal`     — feature, bugfix → proceed on schedule
    - `deferrable` — docs, refactor, style → consider carbon-aware scheduling
    """
    messages = list(body.commit_messages)

    # If no messages supplied and a GitLab pipeline is given, fetch them live
    if not messages and body.pipeline_id and body.project_id and _analyzer._gitlab:
        try:
            commits = _analyzer._gitlab.get_pipeline_commits(
                body.project_id, body.pipeline_id
            )
            messages = [c.message for c in commits]
        except Exception as exc:
            logger.warning("Could not fetch commits for urgency: %s", exc)

    result = classify_urgency(messages)

    _explanations = {
        "urgent": (
            "The commit messages contain urgency signals (e.g. 'hotfix', 'critical', "
            "'security') indicating the pipeline should run immediately."
        ),
        "normal": (
            "No strong urgency or deferral signals detected. "
            "The pipeline should run on its normal schedule."
        ),
        "deferrable": (
            "The commit messages suggest low-urgency work (e.g. 'docs', 'refactor', "
            "'style') — a good candidate for carbon-aware scheduling."
        ),
    }

    return ClassifyUrgencyOutput(
        urgency_class=result.urgency_class,
        confidence=round(result.confidence, 4),
        source=result.source,
        can_defer=result.urgency_class == "deferrable",
        explanation=_explanations.get(result.urgency_class, ""),
    )


@agent_tools_router.post(
    "/analyze_code_efficiency",
    response_model=AnalyzeCodeEfficiencyOutput,
    summary="Analyse MR code for energy efficiency using Claude",
)
async def tool_analyze_code_efficiency(
    body: AnalyzeCodeEfficiencyInput,
) -> AnalyzeCodeEfficiencyOutput:
    """
    Send a merge request diff to Claude for green code profiling.

    Identifies energy-inefficient patterns (N+1 queries, missing caching,
    unbounded loops, sync I/O, over-computation) and returns structured
    suggestions with estimated energy impact.

    Provide **either** ``project_id`` + ``mr_iid`` to fetch the diff from
    GitLab, **or** ``diff_text`` for offline / manual analysis.

    Requires ``ANTHROPIC_API_KEY`` to be configured.
    """
    if not _code_analyzer.is_available:
        return AnalyzeCodeEfficiencyOutput(
            error="Code analyzer unavailable: ANTHROPIC_API_KEY not configured "
                  "or anthropic package not installed.",
            diff_source="unavailable",
        )

    diff_text = body.diff_text
    diff_source = "manual"

    # Fetch diff from GitLab if project_id + mr_iid provided
    if not diff_text and body.project_id and body.mr_iid:
        if _analyzer._gitlab is None:
            return AnalyzeCodeEfficiencyOutput(
                error="GitLab client not configured (GITLAB_TOKEN missing). "
                      "Provide diff_text directly for offline analysis.",
                diff_source="unavailable",
            )
        diff_text = _analyzer._gitlab.get_mr_diff(body.project_id, body.mr_iid)
        diff_source = "gitlab_mr"

    if not diff_text:
        return AnalyzeCodeEfficiencyOutput(
            error="No diff provided. Supply project_id + mr_iid or diff_text.",
            diff_source="unavailable",
        )

    result = await _code_analyzer.analyze_diff(diff_text)

    return AnalyzeCodeEfficiencyOutput(
        suggestions=[
            CodeEfficiencySuggestion(
                file=s.file,
                line_range=s.line_range,
                issue_type=s.issue_type,
                description=s.description,
                estimated_energy_impact=s.estimated_energy_impact,
                suggested_fix=s.suggested_fix,
            )
            for s in result.suggestions
        ],
        overall_assessment=result.overall_assessment,
        estimated_energy_reduction=result.estimated_energy_reduction,
        model_used=result.model_used,
        tokens_used=result.tokens_used,
        error=result.error,
        diff_source=diff_source,
    )


# ---------------------------------------------------------------------------
# Webhook router
# ---------------------------------------------------------------------------

webhook_router = APIRouter(prefix="/agent/webhooks", tags=["Agent Webhooks"])


@webhook_router.post(
    "/pipeline",
    response_model=AgentWebhookResponse,
    summary="GitLab pipeline completion webhook",
)
async def webhook_pipeline_event(
    event: GitLabPipelineEvent,
    x_gitlab_token: str | None = Header(default=None),
) -> AgentWebhookResponse:
    """
    Receive a GitLab pipeline event.

    When the pipeline reaches a terminal state (success/failed/canceled),
    GreenPipe:
      1. Analyses the pipeline using GSF SCI methodology
      2. Formats a markdown report
      3. Posts it as an MR comment (if an open MR exists for the pipeline ref)

    Configure this URL in GitLab → Settings → Webhooks with the
    "Pipeline events" trigger and set the secret token to GITLAB_WEBHOOK_SECRET.
    """
    _verify_webhook_token(x_gitlab_token)

    pipeline_id = event.pipeline_id
    project_id = event.project_id
    pipeline_status = event.status

    logger.info(
        "Pipeline webhook received: project=%s pipeline=%s status=%s",
        project_id, pipeline_id, pipeline_status,
    )

    # Only act on terminal states
    if pipeline_status not in _TERMINAL_STATUSES:
        return AgentWebhookResponse(
            status="skipped",
            message=f"Pipeline status '{pipeline_status}' is not terminal — skipping analysis.",
        )

    if not pipeline_id or not project_id:
        return AgentWebhookResponse(
            status="skipped",
            message="Missing pipeline_id or project_id in event payload.",
        )

    # GitLab client required for live analysis
    if _analyzer._gitlab is None:
        return AgentWebhookResponse(
            status="skipped",
            message="GitLab client not configured (GITLAB_TOKEN missing). "
                    "Use POST /api/v1/pipeline/analyze with 'jobs' for offline analysis.",
        )

    try:
        report = await _analyzer.analyze_from_gitlab(
            project_id=project_id,
            pipeline_id=pipeline_id,
        )
    except Exception as exc:
        logger.error(
            "Analysis failed for pipeline %s/%s: %s", project_id, pipeline_id, exc,
            exc_info=True,
        )
        return AgentWebhookResponse(
            status="error",
            message="Analysis failed. Check server logs for details.",
        )

    # --- Evaluate deferral decision ---
    ref = event.ref or ""
    deferral = await _evaluate_deferral(report, pipeline_id, project_id, ref)
    logger.info(
        "Deferral decision for pipeline %s: action=%s reason=%s",
        pipeline_id, deferral.action, deferral.reason,
    )

    # Build the MR comment — include deferral info when relevant
    if deferral.action in ("deferred", "awaiting_approval", "recommended"):
        markdown = format_mr_comment(report) + "\n\n" + format_deferral_comment(deferral)
    else:
        markdown = format_mr_comment(report)

    # Find and comment on the associated MR
    mr_iid = None
    comment_posted = False
    try:
        mr_iid = _analyzer._gitlab.find_mr_for_pipeline(project_id, pipeline_id)
        if mr_iid is not None:
            ok = _analyzer._gitlab.post_mr_comment(project_id, mr_iid, markdown)
            comment_posted = ok
            if ok:
                logger.info("Posted carbon report to MR !%s on project %s", mr_iid, project_id)
    except Exception as exc:
        logger.warning("Could not post MR comment: %s", exc)

    return AgentWebhookResponse(
        status="accepted",
        message=(
            f"Pipeline {pipeline_id} analysed. "
            f"SCI: {report.sci.sci_score:.3f} gCO₂e. "
            + (f"Comment posted to MR !{mr_iid}." if comment_posted else "No open MR found.")
        ),
        details={
            "project_id": project_id,
            "pipeline_id": pipeline_id,
            "sci_score_gco2e": round(report.sci.sci_score, 4),
            "total_energy_kwh": round(report.total_energy_kwh, 8),
            "urgency_class": report.urgency_class,
            "can_defer": report.can_defer,
            "mr_iid": mr_iid,
            "comment_posted": comment_posted,
        },
        deferral=deferral,
    )


@webhook_router.post(
    "/mention",
    response_model=AgentWebhookResponse,
    summary="GitLab @greenpipe mention webhook",
)
async def webhook_mention_event(
    event: GitLabNoteEvent,
    x_gitlab_token: str | None = Header(default=None),
) -> AgentWebhookResponse:
    """
    Receive a GitLab note event triggered by an @greenpipe mention.

    Supported commands (case-insensitive, anywhere in the comment):
      @greenpipe analyze        — analyse the latest pipeline for this MR
      @greenpipe report         — same as analyze, full SCI report
      @greenpipe schedule       — show carbon-optimal execution windows
      @greenpipe run-now        — override deferral and run the pipeline immediately
      @greenpipe confirm-defer  — approve a pending deferral
      @greenpipe defer          — manually request deferral to best window
      @greenpipe why            — explain the last urgency classification
      @greenpipe help           — list available commands

    Configure this URL in GitLab → Settings → Webhooks with the
    "Comments" trigger restricted to merge request notes.
    """
    _verify_webhook_token(x_gitlab_token)

    note = event.note_body.strip()
    project_id = event.project_id
    mr_iid = event.mr_iid

    logger.info(
        "Mention webhook: project=%s MR=%s note=%.80r",
        project_id, mr_iid, note,
    )

    # Only respond to @greenpipe mentions
    if "@greenpipe" not in note.lower():
        return AgentWebhookResponse(
            status="skipped",
            message="Note does not mention @greenpipe.",
        )

    # Only respond to MR notes (not issue/commit notes)
    if event.noteable_type not in ("MergeRequest", ""):
        return AgentWebhookResponse(
            status="skipped",
            message=f"GreenPipe only responds to MR notes (got '{event.noteable_type}').",
        )

    command = _parse_mention_command(note)
    logger.info("Parsed @greenpipe command: %s", command)

    if command == "help":
        reply = format_help_comment()
        _post_reply(project_id, mr_iid, reply)
        return AgentWebhookResponse(
            status="accepted",
            message="Help message posted.",
            details={"command": "help"},
        )

    if command in ("analyze", "report"):
        if not project_id or not mr_iid:
            return AgentWebhookResponse(
                status="error",
                message="Cannot determine project_id or MR IID from event.",
            )
        if _analyzer._gitlab is None:
            reply = (
                "> ⚠️ **GreenPipe:** GitLab token not configured — "
                "live analysis unavailable. "
                "Use the API directly with job data for offline analysis."
            )
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="skipped",
                message="GitLab client not configured.",
            )

        # Find the latest pipeline for this MR
        pipeline_id = _latest_pipeline_for_mr(event)
        if pipeline_id is None:
            reply = "> ⚠️ **GreenPipe:** Could not find a pipeline for this MR."
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="skipped",
                message="No pipeline found for MR.",
            )

        try:
            report = await _analyzer.analyze_from_gitlab(
                project_id=project_id,
                pipeline_id=pipeline_id,
            )
            reply = format_mr_comment(report)
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="accepted",
                message=f"Report for pipeline {pipeline_id} posted to MR !{mr_iid}.",
                details={
                    "pipeline_id": pipeline_id,
                    "sci_score_gco2e": round(report.sci.sci_score, 4),
                    "urgency_class": report.urgency_class,
                },
            )
        except Exception as exc:
            logger.error("Mention analysis failed: %s", exc, exc_info=True)
            reply = "> ❌ **GreenPipe error:** Analysis failed. Check server logs."
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="error",
                message="Analysis failed. Check server logs for details.",
            )

    if command == "schedule":
        try:
            window = await _carbon_service.find_best_execution_window(
                location="us-east1",  # default; TODO: infer from MR project settings
            )
            if window:
                reply = (
                    f"> 🌱 **GreenPipe scheduling:** Best low-carbon window: "
                    f"`{window.get('timestamp', 'TBD')}` at "
                    f"**{window['intensity_gco2_kwh']:.1f} gCO₂e/kWh**."
                )
            else:
                intensity, source = await _carbon_service.get_intensity("us-east1")
                reply = (
                    f"> 🌱 **GreenPipe scheduling:** No forecast available. "
                    f"Current intensity: **{intensity:.1f} gCO₂e/kWh** ({source})."
                )
        except Exception as exc:
            logger.warning("schedule command failed: %s", exc)
            reply = "> ⚠️ **GreenPipe:** Could not retrieve forecast data."
        _post_reply(project_id, mr_iid, reply)
        return AgentWebhookResponse(
            status="accepted",
            message="Scheduling suggestion posted.",
            details={"command": "schedule"},
        )

    if command == "run-now":
        # Override: retry the pipeline immediately regardless of deferral
        pipeline_id = _latest_pipeline_for_mr(event)
        if pipeline_id and _analyzer._gitlab:
            new_id = _analyzer._gitlab.retry_pipeline(project_id, pipeline_id)
            if new_id:
                reply = (
                    f"> ▶️ **GreenPipe:** Deferral overridden. "
                    f"Pipeline re-triggered (new pipeline: #{new_id})."
                )
            else:
                reply = (
                    f"> ▶️ **GreenPipe:** Override requested but could not retry "
                    f"pipeline {pipeline_id}. Please re-run manually."
                )
        else:
            reply = (
                "> ⚠️ **GreenPipe:** Cannot run-now — "
                "no pipeline found or GitLab client not configured."
            )
        _post_reply(project_id, mr_iid, reply)
        return AgentWebhookResponse(
            status="accepted",
            message="run-now override processed.",
            details={"command": "run-now"},
        )

    if command == "confirm-defer":
        # Approve a pending deferral — schedule the pipeline to the best window
        pipeline_id = _latest_pipeline_for_mr(event)
        ref = ""
        if event.merge_request:
            ref = event.merge_request.get("source_branch", "")
        try:
            window = await _carbon_service.find_best_execution_window(
                location="us-east1",
            )
            if window and pipeline_id and ref and _analyzer._gitlab:
                target_dt = _parse_iso_window(window.get("timestamp"))
                cron = _datetime_to_cron(target_dt) if target_dt else "0 3 * * *"
                _analyzer._gitlab.cancel_pipeline(project_id, pipeline_id)
                schedule_id = _analyzer._gitlab.create_pipeline_schedule(
                    project_id=project_id,
                    ref=ref,
                    cron=cron,
                    description="GreenPipe deferred (user-approved)",
                )
                reply = (
                    f"> ✅ **GreenPipe:** Deferral confirmed. Pipeline cancelled and "
                    f"rescheduled to `{window.get('timestamp', 'TBD')}` "
                    f"(schedule #{schedule_id})."
                )
            else:
                reply = (
                    "> ⚠️ **GreenPipe:** Could not confirm deferral — "
                    "no forecast window or missing pipeline context."
                )
        except Exception as exc:
            logger.warning("confirm-defer failed: %s", exc)
            reply = "> ❌ **GreenPipe:** Deferral confirmation failed. Check server logs."
        _post_reply(project_id, mr_iid, reply)
        return AgentWebhookResponse(
            status="accepted",
            message="confirm-defer processed.",
            details={"command": "confirm-defer"},
        )

    if command == "defer":
        # Manual deferral request — find the best window and schedule
        pipeline_id = _latest_pipeline_for_mr(event)
        ref = ""
        if event.merge_request:
            ref = event.merge_request.get("source_branch", "")
        try:
            window = await _carbon_service.find_best_execution_window(
                location="us-east1",
            )
            if window and pipeline_id and ref and _analyzer._gitlab:
                target_dt = _parse_iso_window(window.get("timestamp"))
                cron = _datetime_to_cron(target_dt) if target_dt else "0 3 * * *"
                _analyzer._gitlab.cancel_pipeline(project_id, pipeline_id)
                schedule_id = _analyzer._gitlab.create_pipeline_schedule(
                    project_id=project_id,
                    ref=ref,
                    cron=cron,
                    description="GreenPipe deferred (user-requested)",
                )
                reply = (
                    f"> ⏸️ **GreenPipe:** Pipeline deferred. Cancelled pipeline "
                    f"#{pipeline_id} and scheduled re-run at "
                    f"`{window.get('timestamp', 'TBD')}` "
                    f"({window['intensity_gco2_kwh']:.1f} gCO₂e/kWh). "
                    f"Override with `@greenpipe run-now`."
                )
            elif not window:
                reply = (
                    "> ⚠️ **GreenPipe:** No lower-carbon window found — "
                    "pipeline should run now."
                )
            else:
                reply = (
                    "> ⚠️ **GreenPipe:** Cannot defer — "
                    "no pipeline found or GitLab client not configured."
                )
        except Exception as exc:
            logger.warning("defer command failed: %s", exc)
            reply = "> ❌ **GreenPipe:** Deferral failed. Check server logs."
        _post_reply(project_id, mr_iid, reply)
        return AgentWebhookResponse(
            status="accepted",
            message="defer processed.",
            details={"command": "defer"},
        )

    if command == "optimize":
        # Green Code Profiler — analyse MR diff for energy efficiency
        if not project_id or not mr_iid:
            return AgentWebhookResponse(
                status="error",
                message="Cannot determine project_id or MR IID from event.",
            )
        if not _code_analyzer.is_available:
            reply = (
                "> ⚠️ **GreenPipe:** Code efficiency analysis unavailable — "
                "`ANTHROPIC_API_KEY` not configured."
            )
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="skipped",
                message="Code analyzer not configured.",
            )
        # Fetch diff
        diff_text: str | None = None
        if _analyzer._gitlab:
            diff_text = _analyzer._gitlab.get_mr_diff(project_id, mr_iid)
        if not diff_text:
            reply = (
                "> ⚠️ **GreenPipe:** Could not fetch diff for this MR. "
                "Ensure GitLab token has API access."
            )
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="error",
                message="Could not fetch MR diff.",
            )
        try:
            result = await _code_analyzer.analyze_diff(diff_text)
            reply = format_code_efficiency_comment(result)
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="accepted",
                message=f"Code efficiency analysis posted to MR !{mr_iid}.",
                details={
                    "command": "optimize",
                    "suggestions_count": len(result.suggestions),
                    "model_used": result.model_used,
                },
            )
        except Exception as exc:
            logger.error("optimize command failed: %s", exc, exc_info=True)
            reply = "> ❌ **GreenPipe error:** Code analysis failed. Check server logs."
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="error",
                message="Code analysis failed. Check server logs for details.",
            )

    if command == "why":
        # Explain the urgency classification for the latest pipeline
        pipeline_id = _latest_pipeline_for_mr(event)
        if pipeline_id and _analyzer._gitlab:
            try:
                commits = _analyzer._gitlab.get_pipeline_commits(
                    project_id, pipeline_id
                )
                messages = [c.message for c in commits]
            except Exception:
                messages = []
        else:
            messages = []

        result = classify_urgency(messages)
        _explanations = {
            "urgent": (
                "urgency signals detected (hotfix, critical, security, CVE). "
                "Pipeline must run immediately."
            ),
            "normal": (
                "no strong urgency or deferral signals. "
                "Pipeline runs on its normal schedule."
            ),
            "deferrable": (
                "low-urgency signals detected (docs, refactor, style, chore). "
                "Pipeline is a good candidate for carbon-aware scheduling."
            ),
        }
        keywords = ", ".join(f"`{m[:60]}`" for m in messages[:3]) or "*(no messages)*"
        reply = (
            f"> 🔍 **GreenPipe — Classification Explanation**\n>\n"
            f"> **Urgency:** `{result.urgency_class}` "
            f"(confidence: {result.confidence:.0%})\n"
            f"> **Source:** `{result.source}` classifier\n"
            f"> **Reason:** {_explanations.get(result.urgency_class, 'unknown')}\n"
            f"> **Commit messages analysed:** {keywords}\n>\n"
            f"> Override: use `@greenpipe run-now` to force immediate execution "
            f"or `@greenpipe defer` to request deferral."
        )
        _post_reply(project_id, mr_iid, reply)
        return AgentWebhookResponse(
            status="accepted",
            message="Classification explanation posted.",
            details={"command": "why", "urgency_class": result.urgency_class},
        )

    # Unknown command — post help
    # Sanitise user input before embedding in Markdown: strip backticks and
    # angle brackets to prevent Markdown/HTML injection in the MR comment.
    safe_note = note[:100].replace("`", "").replace("<", "&lt;").replace(">", "&gt;")
    reply = (
        "> ❓ **GreenPipe:** Unknown command. "
        f"You said: `{safe_note}`\n\n"
        + format_help_comment()
    )
    _post_reply(project_id, mr_iid, reply)
    return AgentWebhookResponse(
        status="accepted",
        message="Unknown command — help posted.",
        details={"command": command},
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COMMAND_RE = re.compile(
    r"@greenpipe\s+(analyze|report|schedule|run-now|confirm-defer|defer|optimize|why|help)",
    re.IGNORECASE,
)


def _parse_mention_command(note: str) -> str:
    """
    Extract the first recognised command from a note that mentions @greenpipe.

    Returns 'help' if no recognised command is found.
    """
    match = _COMMAND_RE.search(note)
    if match:
        return match.group(1).lower()
    return "help"


def _post_reply(project_id: int | None, mr_iid: int | None, body: str) -> None:
    """
    Fire-and-forget MR comment post.  Logs a warning on failure rather than
    raising (the webhook response should still return 200 to GitLab).
    """
    if not project_id or not mr_iid or _analyzer._gitlab is None:
        return
    try:
        _analyzer._gitlab.post_mr_comment(project_id, mr_iid, body)
    except Exception as exc:
        logger.warning("Could not post reply comment: %s", exc)


def _latest_pipeline_for_mr(event: GitLabNoteEvent) -> int | None:
    """
    Try to extract a pipeline ID from the note event context.

    GitLab doesn't include the pipeline ID in note events, but the MR payload
    sometimes contains `head_pipeline_id` or `latest_build_started_at`.
    Falls back to finding the MR via the GitLab client.
    """
    if event.merge_request:
        # GitLab note events include the MR object; try head_pipeline_id
        pid = event.merge_request.get("head_pipeline_id")
        if pid:
            return int(pid)
    return None
