"""
GitLab API client for GreenPipe.

Fetches pipeline job data, commit messages, and runner information
needed for GSF-based carbon analysis. Posts sustainability reports
back to merge requests as comments.

References:
- python-gitlab docs: https://python-gitlab.readthedocs.io/
- GitLab REST API: https://docs.gitlab.com/ee/api/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.config import settings

# Lazy import — python-gitlab is only required when GitLabClient is instantiated.
# Data classes in this module remain importable without the package.
try:
    import gitlab
    from gitlab.exceptions import GitlabError
    _GITLAB_AVAILABLE = True
except ImportError:
    _GITLAB_AVAILABLE = False
    GitlabError = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes returned by the client
# (plain dataclasses keep the service layer decoupled from python-gitlab)
# ---------------------------------------------------------------------------


@dataclass
class JobData:
    """Normalised data for a single GitLab CI job."""

    id: int
    name: str
    status: str
    duration_seconds: float
    runner_type: str | None = None
    runner_tags: list[str] = field(default_factory=list)
    runner_location: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stage: str | None = None
    web_url: str | None = None


@dataclass
class PipelineData:
    """Normalised data for a GitLab pipeline."""

    id: int
    project_id: int
    status: str
    sha: str
    ref: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float
    web_url: str
    jobs: list[JobData] = field(default_factory=list)


@dataclass
class CommitData:
    """Commit associated with a pipeline."""

    sha: str
    title: str
    message: str
    author_name: str
    authored_at: datetime | None = None


# ---------------------------------------------------------------------------
# GitLab client
# ---------------------------------------------------------------------------


class GitLabClient:
    """
    Thin wrapper around python-gitlab for the data GreenPipe needs.

    Handles authentication, rate-limit retries, and translates
    python-gitlab objects into plain dataclasses.
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        if not _GITLAB_AVAILABLE:
            raise ImportError(
                "python-gitlab is required for live GitLab API access. "
                "Install it with: pip install python-gitlab"
            )
        self._gl = gitlab.Gitlab(
            url=url or settings.gitlab_url,
            private_token=token or settings.gitlab_token,
            retry_transient_errors=True,
        )

    def _parse_dt(self, value: str | None) -> datetime | None:
        """Parse a GitLab ISO-8601 timestamp string to a timezone-aware datetime."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            return None

    def _job_duration(self, job: Any) -> float:
        """Return job duration in seconds, handling None gracefully."""
        try:
            return float(job.duration or 0)
        except (TypeError, ValueError):
            return 0.0

    def _extract_runner_info(self, job: Any) -> tuple[str | None, list[str], str | None]:
        """
        Extract runner type, tags, and location from a job object.

        Returns (runner_type, runner_tags, runner_location).
        GitLab's API exposes runner info as a nested dict when available.
        """
        runner = getattr(job, "runner", None)
        if not runner:
            return None, [], None

        runner_dict: dict = runner if isinstance(runner, dict) else {}
        description: str = runner_dict.get("description", "") or ""
        tags: list[str] = runner_dict.get("tag_list", []) or []

        # Infer runner type from description (GitLab SaaS naming convention)
        runner_type: str | None = None
        for known in (
            "saas-linux-xlarge-amd64",
            "saas-linux-large-amd64",
            "saas-linux-medium-amd64",
            "saas-linux-small-amd64",
        ):
            if known in description.lower() or known in [t.lower() for t in tags]:
                runner_type = known
                break

        # Location: try to extract from runner description or tags
        location: str | None = None
        location_hints = {"us-east", "us-west", "europe", "asia"}
        for hint in location_hints:
            if hint in description.lower():
                location = hint
                break

        return runner_type or description or None, tags, location

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_pipeline(self, project_id: int, pipeline_id: int) -> PipelineData:
        """
        Fetch a pipeline and all its jobs.

        Raises GitlabError on API failure.
        """
        try:
            project = self._gl.projects.get(project_id)
            pipeline = project.pipelines.get(pipeline_id)
            jobs_raw = pipeline.jobs.list(all=True)
        except GitlabError as exc:
            logger.error(
                "GitLab API error fetching pipeline %s/%s: %s",
                project_id, pipeline_id, exc,
            )
            raise

        jobs: list[JobData] = []
        for job in jobs_raw:
            runner_type, runner_tags, runner_location = self._extract_runner_info(job)
            jobs.append(
                JobData(
                    id=job.id,
                    name=job.name,
                    status=job.status,
                    duration_seconds=self._job_duration(job),
                    runner_type=runner_type,
                    runner_tags=runner_tags,
                    runner_location=runner_location,
                    started_at=self._parse_dt(getattr(job, "started_at", None)),
                    finished_at=self._parse_dt(getattr(job, "finished_at", None)),
                    stage=getattr(job, "stage", None),
                    web_url=getattr(job, "web_url", None),
                )
            )

        started = self._parse_dt(getattr(pipeline, "started_at", None))
        finished = self._parse_dt(getattr(pipeline, "finished_at", None))

        try:
            duration = float(pipeline.duration or 0)
        except (TypeError, ValueError):
            duration = sum(j.duration_seconds for j in jobs)

        return PipelineData(
            id=pipeline.id,
            project_id=project_id,
            status=pipeline.status,
            sha=pipeline.sha,
            ref=pipeline.ref,
            started_at=started,
            finished_at=finished,
            duration_seconds=duration,
            web_url=pipeline.web_url,
            jobs=jobs,
        )

    def get_commit(self, project_id: int, sha: str) -> CommitData | None:
        """Fetch a single commit by SHA."""
        try:
            project = self._gl.projects.get(project_id)
            commit = project.commits.get(sha)
            return CommitData(
                sha=commit.id,
                title=commit.title,
                message=commit.message,
                author_name=commit.author_name,
                authored_at=self._parse_dt(getattr(commit, "authored_date", None)),
            )
        except GitlabError as exc:
            logger.warning("Could not fetch commit %s: %s", sha, exc)
            return None

    def get_pipeline_commits(
        self, project_id: int, pipeline_id: int
    ) -> list[CommitData]:
        """
        Return the commit(s) associated with a pipeline.

        GitLab pipelines are tied to a single SHA, so this normally
        returns a list of one.
        """
        try:
            project = self._gl.projects.get(project_id)
            pipeline = project.pipelines.get(pipeline_id)
            sha = pipeline.sha
        except GitlabError as exc:
            logger.error("GitLab API error: %s", exc)
            return []

        commit = self.get_commit(project_id, sha)
        return [commit] if commit else []

    def post_mr_comment(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
    ) -> bool:
        """
        Post a comment on a merge request.

        Returns True on success, False on failure.
        """
        try:
            project = self._gl.projects.get(project_id)
            mr = project.mergerequests.get(mr_iid)
            mr.notes.create({"body": body})
            logger.info("Posted MR comment on project %s MR !%s", project_id, mr_iid)
            return True
        except GitlabError as exc:
            logger.error("Failed to post MR comment: %s", exc)
            return False

    def find_mr_for_pipeline(
        self, project_id: int, pipeline_id: int
    ) -> int | None:
        """
        Find the MR IID associated with a pipeline's ref, if any.

        Returns the MR IID or None.
        """
        try:
            project = self._gl.projects.get(project_id)
            pipeline = project.pipelines.get(pipeline_id)
            ref = pipeline.ref

            mrs = project.mergerequests.list(
                source_branch=ref,
                state="opened",
                all=True,
            )
            if mrs:
                return mrs[0].iid
        except GitlabError as exc:
            logger.warning("Could not find MR for pipeline %s: %s", pipeline_id, exc)
        return None
