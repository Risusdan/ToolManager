from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest
from fake_bitbucket import FakeBitbucket

from toolmanager.backends.bitbucket_server import BitbucketPublisher, BitbucketServerRepository
from toolmanager.backends.local import LocalPublisher, LocalRepository
from toolmanager.core import publish
from toolmanager.core.lfs import parse_pointer
from toolmanager.core.manifest import load_catalog
from toolmanager.core.models import ToolType


@pytest.fixture
def synced(catalog_dir: Path, tmp_path: Path) -> Path:
    """The synced catalog as ToolManager has it: manifests and images, no zips."""
    dest = tmp_path / "synced"
    LocalRepository(catalog_dir).fetch_catalog("x", dest)
    return dest


@pytest.fixture
def program(tmp_path: Path) -> Path:
    folder = tmp_path / "RegisterViewer"
    folder.mkdir()
    (folder / "RegisterViewer.exe").write_bytes(b"MZ new build")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_bytes(b"cache")
    return folder


def new_version(synced: Path, program: Path, version: str = "") -> publish.Draft:
    tool = load_catalog(synced).catalog.tool("register-viewer")
    draft = publish.draft_from_tool(tool, program)
    if version:
        draft.release.version = version
    draft.release.changelog = "- 修正：存檔時的編碼問題"
    return draft


def new_tool(source: Path) -> publish.Draft:
    return publish.Draft(
        tool=publish.ToolInfo(id="flash-helper", name="Flash Helper", summary="一鍵燒錄",
                              category="flash", owners=["user-a"], type=ToolType.PYTHON,
                              readme="# Flash Helper\n\n![畫面](assets/shot.png)\n"),
        release=publish.ReleaseInfo(version="1.0.0", changelog="首次發布", entry="main.py"),
        source=source, is_new=True)


def test_next_version_and_carried_over_fields(synced: Path, program: Path) -> None:
    draft = new_version(synced, program)
    assert draft.release.version == "1.1.1"
    assert draft.release.entry == "RegisterViewer.exe"
    assert draft.tool.owners == ["user-a"] and draft.release.changelog
    assert publish.next_version(None) == "1.0.0"


def test_guess_entry() -> None:
    assert publish.guess_entry(["main.py", "util.py"], ToolType.PYTHON) == "main.py"
    assert publish.guess_entry(["plot.py", "lib/x.py"], ToolType.PYTHON) == "plot.py"
    assert publish.guess_entry(["a.py", "b.py"], ToolType.PYTHON) == ""
    assert publish.guess_entry(["Tool.exe", "a.dll"], ToolType.EXE) == "Tool.exe"


def test_prepare_stages_only_the_new_release(synced: Path, program: Path, tmp_path: Path) -> None:
    prepared = publish.prepare(new_version(synced, program), synced, tmp_path / "w", "user-a")
    assert prepared.report.errors == []
    rel = "tools/register-viewer/releases/1.1.1"
    assert prepared.files == [f"{rel}/register-viewer-1.1.1.zip", f"{rel}/release.toml"]
    with zipfile.ZipFile(prepared.staged / f"{rel}/register-viewer-1.1.1.zip") as zf:
        assert zf.namelist() == ["RegisterViewer.exe"]  # no __pycache__
    text = (prepared.staged / f"{rel}/release.toml").read_text("utf-8")
    assert 'entry = "RegisterViewer.exe"' in text and "編碼問題" in text


def test_prepare_reports_problems(synced: Path, program: Path, tmp_path: Path) -> None:
    draft = new_version(synced, program, version="1.0.5")
    draft.release.entry = "Missing.exe"
    draft.release.important = True
    report = publish.prepare(draft, synced, tmp_path / "w", "user-b").report
    assert report.errors == ["標為「建議盡快更新」時，請用一句話說明原因"]  # form errors first
    draft.release.important = False
    text = "\n".join(publish.prepare(draft, synced, tmp_path / "w", "user-b").report.errors)
    assert "找不到 entry「Missing.exe」" in text
    assert "必須大於目前最新的 1.1.0" in text
    assert "user-b 不在 owners" in text


def test_new_tool_with_readme_image(synced: Path, tmp_path: Path) -> None:
    source = tmp_path / "flash-helper"
    (source / "assets").mkdir(parents=True)
    (source / "main.py").write_text("print('flash')\n", "utf-8")
    (source / "assets" / "shot.png").write_bytes(b"png")
    prepared = publish.prepare(new_tool(source), synced, tmp_path / "w", "someone")
    assert prepared.report.errors == []
    assert "flash-helper：新工具，需管理員核准" in prepared.report.notes
    assert "tools/flash-helper/assets/shot.png" in prepared.files
    assert "tools/flash-helper/tool.toml" in prepared.files

    taken = new_tool(source)
    taken.tool.id = "csv-plotter"
    assert "已經有人使用" in publish.prepare(taken, synced, tmp_path / "w", "x").report.errors[0]


def test_submit_to_bitbucket_merges_a_new_version(synced: Path, program: Path,
                                                   tmp_path: Path) -> None:
    server = FakeBitbucket()
    server.branches["publish/register-viewer-1.1.1"] = {}  # left over from an earlier try
    repo = BitbucketServerRepository("https://bb.example.com", "TM", "catalog", "tok",
                                     client=server.client())
    publisher = BitbucketPublisher(repo)
    prepared = publish.prepare(new_version(synced, program), synced, tmp_path / "w",
                               publisher.current_user())
    steps: list[str] = []
    result = publish.submit(prepared, publisher, steps.append)

    assert result.merged and result.url == "https://bb.example.com/pr/1"
    main = server.branches["main"]
    pointer = parse_pointer(main["tools/register-viewer/releases/1.1.1/register-viewer-1.1.1.zip"])
    assert pointer is not None and pointer.oid in server.lfs  # zip in LFS, pointer in git
    assert b"RegisterViewer.exe" in main["tools/register-viewer/releases/1.1.1/release.toml"]
    assert server.prs[0]["from"] == "publish/register-viewer-1.1.1-2"
    assert server.prs[0]["title"] == "[新版本] Register Viewer 1.1.1"
    assert steps[-1] == "merge"


def test_submit_new_tool_waits_for_review_and_edits_existing_files(
        synced: Path, program: Path, tmp_path: Path) -> None:
    server = FakeBitbucket(files={"tools/register-viewer/tool.toml": b"old"})
    publisher = BitbucketPublisher(BitbucketServerRepository(
        "https://bb.example.com", "TM", "catalog", "tok", client=server.client()))
    draft = new_version(synced, program)
    draft.tool.summary = "新的摘要"  # tool.toml changes: an existing file is edited
    prepared = publish.prepare(draft, synced, tmp_path / "w", "user-a")
    assert "tools/register-viewer/tool.toml" in prepared.files
    publish.submit(prepared, publisher)
    assert "新的摘要" in server.branches["main"]["tools/register-viewer/tool.toml"].decode()

    blocked = FakeBitbucket(block_merge="Requires 1 approval")
    publisher = BitbucketPublisher(BitbucketServerRepository(
        "https://bb.example.com", "TM", "catalog", "tok", client=blocked.client()))
    result = publish.submit(prepared, publisher)
    assert not result.merged and "Requires 1 approval" in result.waiting_reason

    source = tmp_path / "flash"
    source.mkdir()
    (source / "main.py").write_text("x", "utf-8")
    draft = new_tool(source)
    draft.tool.readme = ""
    fresh = FakeBitbucket()
    publisher = BitbucketPublisher(BitbucketServerRepository(
        "https://bb.example.com", "TM", "catalog", "tok", client=fresh.client()))
    result = publish.submit(publish.prepare(draft, synced, tmp_path / "w2", "u"), publisher)
    assert not result.merged and result.waiting_reason == "new_tool"
    assert fresh.prs[0]["state"] == "OPEN" and "tools/flash-helper/tool.toml" not in (
        fresh.branches["main"])


def test_local_publisher_writes_real_files(catalog_dir: Path, synced: Path, program: Path,
                                           tmp_path: Path) -> None:
    prepared = publish.prepare(new_version(synced, program), synced, tmp_path / "w", "user-a")
    result = publish.submit(prepared, LocalPublisher(catalog_dir, user="user-a"))
    assert result.merged and result.url == ""
    zip_path = catalog_dir / "tools/register-viewer/releases/1.1.1/register-viewer-1.1.1.zip"
    assert zipfile.is_zipfile(zip_path)
    assert str(load_catalog(catalog_dir).catalog.tool("register-viewer").latest.version) == "1.1.1"


def test_submit_refuses_errors_and_empty_changes(synced: Path, program: Path,
                                                 tmp_path: Path) -> None:
    prepared = publish.prepare(new_version(synced, program, "1.0.0"), synced, tmp_path / "w", "x")
    with pytest.raises(publish.PublishError):
        publish.submit(prepared, LocalPublisher(tmp_path))
    prepared.report.errors.clear()
    prepared.files.clear()
    with pytest.raises(publish.PublishError):
        publish.submit(prepared, LocalPublisher(tmp_path))
    shutil.rmtree(tmp_path / "w")


def _publisher(server: FakeBitbucket) -> BitbucketPublisher:
    return BitbucketPublisher(BitbucketServerRepository(
        "https://bb.example.com", "TM", "catalog", "tok", client=server.client()))


def test_write_access_decides_who_is_a_developer(tmp_path: Path) -> None:
    assert _publisher(FakeBitbucket()).can_write()
    assert not _publisher(FakeBitbucket(writable=[("OTHER", "catalog", "catalog")])).can_write()
    # The repo's display name differs from its slug and the user writes to many repos:
    many = [("P", f"r{i}", f"r{i}") for i in range(250)] + [("TM", "catalog", "Tool Catalog")]
    assert _publisher(FakeBitbucket(writable=many)).can_write()
    assert not _publisher(FakeBitbucket(writable=[])).can_write()
    assert LocalPublisher(tmp_path).can_write()


def test_wizard_only_for_writers(monkeypatch) -> None:
    from toolmanager.ui import state as ui_state

    tool = load_catalog(Path(__file__).resolve().parents[1] / "examples" / "catalog"
                        ).catalog.tool("register-viewer")
    monkeypatch.setattr(ui_state.state, "publisher", LocalPublisher(Path("."), user="user-a"))
    monkeypatch.setattr(ui_state.state, "can_write", None)
    assert not ui_state.can_publish() and not ui_state.can_publish(tool)
    monkeypatch.setattr(ui_state.state, "can_write", True)
    assert ui_state.can_publish() and ui_state.can_publish(tool)  # user-a owns it
    monkeypatch.setattr(ui_state.state, "publisher", LocalPublisher(Path("."), user="user-z"))
    assert ui_state.can_publish() and not ui_state.can_publish(tool)


def test_python_args_carry_over_and_round_trip(synced: Path, tmp_path: Path) -> None:
    import tomllib

    tool = load_catalog(synced).catalog.tool("csv-plotter")
    source = tmp_path / "plotter"
    source.mkdir()
    (source / "main.py").write_text("print()\n", "utf-8")
    draft = publish.draft_from_tool(tool, source)
    draft.release.changelog = "x"
    assert [a.name for a in draft.release.args] == ["input", "column", "width", "sort"]
    assert draft.release.args[2].default == "40" and draft.release.python == ">=3.11"
    draft.release.args.append(publish.ArgDraft(name="mode", type="choice", choices="a, b",
                                               default="b", label="模式"))
    prepared = publish.prepare(draft, synced, tmp_path / "w", "user-b")
    assert prepared.report.errors == []
    data = tomllib.loads((prepared.staged / "tools/csv-plotter/releases/0.3.1/release.toml"
                          ).read_text("utf-8"))
    old = tomllib.loads((synced / "tools/csv-plotter/releases/0.3.0/release.toml"
                         ).read_text("utf-8"))
    assert data["args"][:4] == old["args"]  # unchanged fields come out exactly as they were
    assert data["args"][4] == {"name": "mode", "type": "choice", "label": "模式",
                               "choices": ["a", "b"], "default": "b"}
    assert data["python"] == ">=3.11"


def test_args_problems() -> None:
    drafts = [publish.ArgDraft(name="n", type="int", default="abc"),
              publish.ArgDraft(name="x"), publish.ArgDraft(name="x"),
              publish.ArgDraft(name="", type="string"),
              publish.ArgDraft(name="c", type="choice", choices="a,b", default="z"),
              publish.ArgDraft(name="9bad")]
    tables, errors = publish.args_problems(drafts)
    text = "\n".join(errors)
    assert "「abc」不是數字" in text and "「x」重複" in text and "第 4 個參數沒有名稱" in text
    assert "not one of its choices" in text and "invalid arg name '9bad'" in text
    assert [t["name"] for t in tables] == ["x", "x"]


def test_command_preview() -> None:
    from toolmanager.ui.args_editor import command_preview

    specs = publish.arg_specs([
        publish.ArgDraft(name="input", type="file", label="輸入 CSV", required=True),
        publish.ArgDraft(name="width", type="int", default="40"),
        publish.ArgDraft(name="sort", type="bool", default=True, flag="-s"),
        publish.ArgDraft(name="out", type="folder", positional=True, label="輸出資料夾")])
    assert command_preview("main.py", specs) == (
        "python main.py --input <輸入 CSV> --width 40 -s <輸出資料夾>")
