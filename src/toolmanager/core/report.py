"""Problem reports go to Jira (see docs/DESIGN.md §10.6).

ToolManager only builds links that open in the user's browser, where they are already
signed in to Jira, so no Jira token is needed:
- known issues: the tool's open tickets (by label), to check before reporting
- new issue: Jira's create page with the label, versions and recent output filled in
"""

from __future__ import annotations

import platform
from urllib.parse import quote, urlencode

from ..i18n import _
from .models import JiraSettings, Tool

# Jira Data Center runs on Tomcat, which rejects request lines + headers beyond ~8 KB by
# default (cookies count too). Keep the create link comfortably below that.
MAX_URL_LENGTH = 6000
LOG_TAIL_LINES = 40
MAX_LINE_LENGTH = 300


def known_issues_url(jira: JiraSettings, tool: Tool) -> str:
    jql = (f'project = {jira.project_key} AND labels = "{jira.label_for(tool.id)}" '
           f"AND statusCategory != Done ORDER BY created DESC")
    return f"{jira.base_url}/issues/?jql={quote(jql)}"


def _description(jira: JiraSettings, tool: Tool, installed_version: str | None,
                 app_version: str, log_lines: list[str]) -> str:
    lines = [
        _("Please check the known issues before submitting: {url}",
          url=known_issues_url(jira, tool)),
        "",
        "*" + _("What happened, and what did you expect?") + "*",
        "",
        "",
        "----",
        _("Tool: {name} ({id})", name=tool.name, id=tool.id),
        _("Installed version: {version}", version=installed_version or "-"),
        _("ToolManager version: {version}", version=app_version),
        _("System: {system}", system=platform.platform()),
    ]
    if log_lines:
        lines += ["", _("Last run output:"), "{noformat}", *log_lines, "{noformat}"]
    return "\n".join(lines)


def new_issue_url(jira: JiraSettings, tool: Tool, installed_version: str | None,
                  app_version: str, log_lines: list[str] | None = None) -> str:
    """Jira's prefilled create page; the log tail is shortened until the link fits."""
    tail = [line[:MAX_LINE_LENGTH] for line in (log_lines or [])[-LOG_TAIL_LINES:]]
    summary = f"[{tool.name}] "

    def build(lines: list[str]) -> str:
        params = {
            "pid": jira.project_id,
            "issuetype": jira.issue_type_id,
            "summary": summary,
            "labels": jira.label_for(tool.id),
            "description": _description(jira, tool, installed_version, app_version, lines),
        }
        return (f"{jira.base_url}/secure/CreateIssueDetails!init.jspa?"
                + urlencode(params, quote_via=quote))

    url = build(tail)
    while len(url) > MAX_URL_LENGTH and tail:
        tail = tail[len(tail) // 4 + 1:]  # drop the oldest lines first
        url = build(tail)
    return url
