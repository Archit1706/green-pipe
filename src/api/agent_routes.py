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

import logging
import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from src.api.agent_schemas import (
    AgentWebhookResponse,
    AnalyzePipelineInput,
    AnalyzePipelineOutput,
    ClassifyUrgencyInput,
    ClassifyUrgencyOutput,
    GenerateSCIReportInput,
    GenerateSCIReportOutput,
    GitLabNoteEvent,
    GitLabPipelineEvent,
    SuggestSchedulingInput,
    SuggestSchedulingOutput,
)
from src.api.report_formatter import format_help_comment, format_mr_comment
from src.config import settings
from src.nlp.classifier import classify_urgency
from src.services.carbon_service import CarbonService
from src.services.pipeline_analyzer import PipelineAnalyzer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared service singletons
# ---------------------------------------------------------------------------

_carbon_service = CarbonService()
_analyzer = PipelineAnalyzer(carbon_service=_carbon_service)

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
    if x_gitlab_token != secret:
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
        raise HTTPException(status_code=500, detail=f"Analysis error: {exc}") from exc

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
        raise HTTPException(status_code=500, detail=f"Analysis error: {exc}") from exc

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
            message=f"Analysis failed: {exc}",
        )

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
      @greenpipe analyze   — analyse the latest pipeline for this MR
      @greenpipe report    — same as analyze, full SCI report
      @greenpipe schedule  — show carbon-optimal execution windows
      @greenpipe help      — list available commands

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
            reply = f"> ❌ **GreenPipe error:** {exc}"
            _post_reply(project_id, mr_iid, reply)
            return AgentWebhookResponse(
                status="error",
                message=f"Analysis failed: {exc}",
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
            reply = f"> ⚠️ **GreenPipe:** Could not retrieve forecast data ({exc})."
        _post_reply(project_id, mr_iid, reply)
        return AgentWebhookResponse(
            status="accepted",
            message="Scheduling suggestion posted.",
            details={"command": "schedule"},
        )

    # Unknown command — post help
    reply = (
        "> ❓ **GreenPipe:** Unknown command. "
        f"You said: `{note[:100]}`\n\n"
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
    r"@greenpipe\s+(analyze|report|schedule|help)",
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
