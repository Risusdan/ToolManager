from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from toolmanager.core.manifest import load_catalog
from toolmanager.core.models import JiraSettings, Tool, ToolType
from toolmanager.core.report import MAX_URL_LENGTH, known_issues_url, new_issue_url
from toolmanager.i18n import set_language

JIRA = JiraSettings("https://jira.example.com", "TOOLS", 10200, 1)
TOOL = Tool(id="csv-plotter", name="CSV Plotter", summary="", category="image",
            owners=("user-b",), type=ToolType.PYTHON)


@pytest.fixture(autouse=True)
def english() -> None:
    set_language("en")


def test_known_issues_filters_by_label_and_open_status() -> None:
    url = known_issues_url(JIRA, TOOL)
    assert url.startswith("https://jira.example.com/issues/?jql=")
    jql = unquote(url.split("jql=", 1)[1])
    assert jql == ('project = TOOLS AND labels = "tm-csv-plotter" AND statusCategory != Done '
                   "ORDER BY created DESC")


def test_new_issue_is_prefilled() -> None:
    url = new_issue_url(JIRA, TOOL, "0.3.0", "0.1.0", ["step 1", "Traceback: boom"])
    parts = urlsplit(url)
    assert parts.path == "/secure/CreateIssueDetails!init.jspa"
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert (q["pid"], q["issuetype"], q["labels"]) == ("10200", "1", "tm-csv-plotter")
    assert q["summary"] == "[CSV Plotter] "
    d = q["description"]
    assert known_issues_url(JIRA, TOOL) in d  # reminder to check first
    assert "Installed version: 0.3.0" in d and "ToolManager version: 0.1.0" in d
    assert "{noformat}\nstep 1\nTraceback: boom\n{noformat}" in d


def test_long_output_is_trimmed_to_a_safe_url_length() -> None:
    log = [f"line {i} " + "x" * 200 for i in range(500)] + ["y" * 5000]
    url = new_issue_url(JIRA, TOOL, "0.3.0", "0.1.0", log)
    assert len(url) <= MAX_URL_LENGTH
    description = parse_qs(urlsplit(url).query)["description"][0]
    assert "y" * 300 in description and "y" * 301 not in description  # newest line kept, capped
    assert "line 0 " not in description  # oldest lines dropped first


def test_no_output_means_no_log_block() -> None:
    url = new_issue_url(JIRA, TOOL, None, "0.1.0")
    assert "noformat" not in parse_qs(urlsplit(url).query)["description"][0]


def _catalog_with(catalog_dir: Path, table: str):
    path = catalog_dir / "catalog.toml"
    text = path.read_text("utf-8")
    start = text.find("[jira]")
    if start != -1:  # replace the example's table
        end = text.find("\n[", start + 1)
        text = text[:start] + (text[end + 1:] if end != -1 else "")
    path.write_text(text + "\n" + table, "utf-8")
    return load_catalog(catalog_dir).catalog


def test_jira_table_parsed(catalog_dir: Path) -> None:
    catalog = _catalog_with(catalog_dir, '[jira]\nbase_url = "https://jira.example.com/"\n'
                                         'project_key = "TOOLS"\nproject_id = 10200\n'
                                         'issue_type_id = 1\n')
    assert catalog.jira == JiraSettings("https://jira.example.com", "TOOLS", 10200, 1)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('base_url = "jira"\nproject_key = "T1"\nproject_id = 1\nissue_type_id = 1', "base_url"),
        ('base_url = "https://j"\nproject_key = "bad key"\nproject_id = 1\nissue_type_id = 1',
         "project_key"),
        ('base_url = "https://j"\nproject_key = "T1"\nproject_id = "x"\nissue_type_id = 1',
         "project_id"),
        ('base_url = "https://j"\nproject_key = "T1"\nproject_id = 1\nissue_type_id = 1\n'
         'label_prefix = "a b"', "label_prefix"),
    ],
)
def test_invalid_jira_table(catalog_dir: Path, table: str, message: str) -> None:
    from toolmanager.core.manifest import ManifestError

    with pytest.raises(ManifestError, match=message):
        _catalog_with(catalog_dir, "[jira]\n" + table + "\n")
