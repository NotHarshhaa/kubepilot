"""GitHub Actions / commit inspection tools (read-only)."""

import io
import zipfile

import httpx

from app.config.settings import get_settings
from app.tools.registry import SafetyCategory, register

API = "https://api.github.com"


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@register(SafetyCategory.READ)
def get_repository(repo: str) -> str:
    """Get metadata for a GitHub repository, e.g. 'NotHarshhaa/kubepilot':
    description, default branch, and recent push info."""
    try:
        response = httpx.get(f"{API}/repos/{repo}", headers=_headers(), timeout=30)
        response.raise_for_status()
        data = response.json()
        visibility = "private" if data["private"] else "public"
        return (
            f"Repository '{data['full_name']}': {data.get('description') or 'no description'}\n"
            f"  default branch: {data['default_branch']}  visibility: {visibility}\n"
            f"  last push: {data.get('pushed_at')}"
        )
    except Exception as exc:
        return f"Error fetching repository '{repo}': {exc}"


@register(SafetyCategory.READ)
def get_workflow(repo: str, run_id: int) -> str:
    """Get details of a GitHub Actions workflow run: workflow name, status, conclusion,
    branch, and triggering commit."""
    try:
        response = httpx.get(f"{API}/repos/{repo}/actions/runs/{run_id}", headers=_headers(), timeout=30)
        response.raise_for_status()
        run = response.json()
        return (
            f"Workflow run {run_id} in '{repo}':\n"
            f"  workflow: {run['name']}  event: {run.get('event')}\n"
            f"  status: {run['status']}  conclusion: {run.get('conclusion')}\n"
            f"  branch: {run.get('head_branch')}  commit: {run.get('head_sha', '')[:7]}\n"
            f"  started: {run.get('run_started_at')}  url: {run.get('html_url')}"
        )
    except Exception as exc:
        return f"Error fetching workflow run {run_id} in '{repo}': {exc}"


@register(SafetyCategory.READ)
def get_workflow_jobs(repo: str, run_id: int) -> str:
    """List jobs and steps for a GitHub Actions run, highlighting which step failed."""
    try:
        response = httpx.get(f"{API}/repos/{repo}/actions/runs/{run_id}/jobs", headers=_headers(), timeout=30)
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
        if not jobs:
            return f"No jobs found for run {run_id} in '{repo}'."
        lines = [f"Jobs for run {run_id} in '{repo}':"]
        for job in jobs:
            lines.append(f"  Job '{job['name']}': {job['status']} / {job.get('conclusion')}")
            for step in job.get("steps", []):
                conclusion = step.get("conclusion") or step.get("status")
                mark = "X" if step.get("conclusion") == "failure" else "-"
                lines.append(f"    [{mark}] {step['name']} ({conclusion})")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error fetching jobs for run {run_id} in '{repo}': {exc}"


@register(SafetyCategory.READ)
def get_workflow_logs(repo: str, run_id: int, max_chars: int = 8000) -> str:
    """Download the logs of a GitHub Actions workflow run and return them
    (each job's tail, truncated)."""
    try:
        response = httpx.get(
            f"{API}/repos/{repo}/actions/runs/{run_id}/logs",
            headers=_headers(),
            timeout=60,
            follow_redirects=True,
        )
        if response.status_code == 404:
            return f"No logs available for run {run_id} in '{repo}' (expired or not yet generated)."
        response.raise_for_status()
        chunks = []
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for name in archive.namelist():
                text = archive.read(name).decode("utf-8", errors="replace")
                chunks.append(f"===== {name} (last 2000 chars) =====\n{text[-2000:]}")
        logs = "\n".join(chunks)
        if len(logs) > max_chars:
            return logs[:max_chars] + "\n... (truncated)"
        return logs or "Log archive is empty."
    except Exception as exc:
        return f"Error fetching logs for run {run_id} in '{repo}': {exc}"


@register(SafetyCategory.READ)
def get_commit(repo: str, sha: str) -> str:
    """Get a commit from a GitHub repository: message, author, and date.
    Useful to correlate a deployment with recent changes."""
    try:
        response = httpx.get(f"{API}/repos/{repo}/commits/{sha}", headers=_headers(), timeout=30)
        response.raise_for_status()
        commit = response.json()
        return (
            f"Commit {sha[:7]} in '{repo}' by {commit['commit']['author']['name']} "
            f"on {commit['commit']['author']['date']}:\n"
            f"  {commit['commit']['message'].splitlines()[0]}\n"
            f"  files changed: {len(commit.get('files', []))}"
        )
    except Exception as exc:
        return f"Error fetching commit {sha} in '{repo}': {exc}"
