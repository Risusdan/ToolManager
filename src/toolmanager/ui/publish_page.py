"""Publish wizard (docs/DESIGN.md §8): a new tool, or a new version of one the user owns.

One page, top to bottom: the program, the tool's details, the version, then check and
publish. The checks are core.catalog_check's (the same that run before merging), so what
passes here also passes review automatically.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

from nicegui import app, ui
from nicegui import run as ng_run

from ..core import paths
from ..core.catalog_check import Report
from ..core.models import Tool, ToolType
from ..core.publish import (
    Draft,
    PublishError,
    ReleaseInfo,
    SubmitResult,
    ToolInfo,
    draft_from_tool,
    guess_entry,
    package_names,
    prepare,
    submit,
)
from ..core.repository import RepositoryError
from ..i18n import _
from .args_editor import args_editor
from .state import can_publish, check_write_access, state, sync
from .theme import frame
from .widgets import empty_state

log = logging.getLogger(__name__)

_ENTRY_SUFFIXES = {
    ToolType.EXE: (".exe", ".bat", ".cmd"),
    ToolType.PYTHON: (".py", ".pyw"),
    ToolType.INSTALLER: (".exe", ".msi"),
}


@ui.page("/publish")
async def publish_new_tool() -> None:
    await _page(None)


@ui.page("/publish/{tool_id}")
async def publish_new_version(tool_id: str) -> None:
    await _page(tool_id)


def _type_options() -> dict[str, str]:
    return {ToolType.EXE: _("Program (zip, runs without installing)"),
            ToolType.PYTHON: _("Python script"),
            ToolType.INSTALLER: _("Installer (setup.exe / .msi)")}


def _new_draft() -> Draft:
    user = state.publisher.known_user() if state.publisher else ""
    categories = state.catalog.categories if state.catalog else ()
    return Draft(
        tool=ToolInfo(id="", name="", summary="", category=categories[0].id if categories else "",
                      owners=[user] if user else [], type=ToolType.EXE),
        release=ReleaseInfo(version="1.0.0", changelog=_("First release"), entry=""),
        source=Path(""), is_new=True)


async def _page(tool_id: str | None) -> None:
    frame(None)
    catalog = state.catalog
    if state.publisher is None or catalog is None:
        empty_state("cloud_off", _("Publishing is not available"),
                    _("Connect to the catalog in Settings first."), (_("Settings"), "/settings"))
        return
    if state.can_write is None:  # opened before the first sync finished asking
        await check_write_access()
    if not can_publish():
        empty_state("lock", _("Publishing is for tool developers"),
                    _("It needs write access to the catalog repo. Ask an admin if you develop "
                      "tools."), (_("All tools"), "/"))
        return
    tool: Tool | None = catalog.tool(tool_id) if tool_id else None
    if tool_id and tool is None:
        empty_state("search_off", _("Tool not found"), button=(_("All tools"), "/"))
        return
    if tool is not None and not can_publish(tool):
        empty_state("lock", _("Only the owners can publish {name}", name=tool.name),
                    _("Owners: {names}", names=", ".join(tool.owners)),
                    (_("Back"), f"/tool/{tool.id}"))
        return

    draft = draft_from_tool(tool, Path("")) if tool else _new_draft()
    form = {"source": "", "owners": ", ".join(draft.tool.owners)}
    names: list[str] = []

    title = (_("Publish a new version of {name}", name=tool.name) if tool
             else _("Publish a new tool"))
    ui.label(title).classes("text-xl font-semibold")
    ui.label(_("New versions of your own tools go online right away. A new tool goes online "
               "once an admin approves it.")).classes("text-sm text-slate-500 -mt-2")

    def card(heading: str):
        box = ui.card().props("flat bordered").classes("w-full max-w-3xl rounded-lg gap-3")
        with box:
            ui.label(heading).classes("text-sm font-semibold text-slate-700")
        return box

    # 1. The program -----------------------------------------------------------------
    with card(_("1. Program")):
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            source = ui.input(_("A zip file or the program's folder")).props(
                "outlined dense").classes("flex-grow").bind_value(form, "source")
            if getattr(app.native, "main_window", None):
                ui.button(_("Choose zip..."), on_click=lambda: _browse(source, False)).props(
                    "outline no-caps color=grey-8")
                ui.button(_("Choose folder..."), on_click=lambda: _browse(source, True)).props(
                    "outline no-caps color=grey-8")
        package_info = ui.label().classes("text-xs text-slate-500")
        ui.label(_("A folder is zipped for you; caches such as __pycache__ and .venv are left "
                   "out.")).classes("text-xs text-slate-500")

    # 2. The tool --------------------------------------------------------------------
    with card(_("2. Tool")):
        if draft.is_new:
            with ui.row().classes("w-full gap-3 no-wrap"):
                hint = _("Lowercase letters, digits and -; cannot be changed later")
                ui.input(_("Tool ID"), placeholder="register-viewer").props(
                    f'outlined dense hint="{hint}"').classes("w-64").bind_value(draft.tool, "id")
                type_select = ui.select(_type_options(), label=_("Type")).props(
                    "outlined dense").classes("flex-grow").bind_value(draft.tool, "type")
        else:
            type_select = None
        with ui.row().classes("w-full gap-3 no-wrap"):
            ui.input(_("Name")).props("outlined dense").classes("w-64").bind_value(
                draft.tool, "name")
            ui.select({c.id: c.name for c in catalog.categories}, label=_("Category")).props(
                "outlined dense").classes("flex-grow").bind_value(draft.tool, "category")
        ui.input(_("Summary (one line on the tool card)")).props("outlined dense").classes(
            "w-full").bind_value(draft.tool, "summary")
        with ui.row().classes("w-full gap-3 no-wrap"):
            ui.input(_("Owners (Bitbucket accounts, comma separated)")).props(
                "outlined dense").classes("flex-grow").bind_value(form, "owners")
            ui.input(_("Issue link (optional)")).props("outlined dense").classes(
                "flex-grow").bind_value(draft.tool, "issue_url")
        ui.textarea(_("README (Markdown)")).props("outlined autogrow").classes(
            "w-full font-mono text-sm").bind_value(draft.tool, "readme")
        ui.label(_("Images: put them in the program's folder, e.g. assets/main.png, and write "
                   "![Main window](assets/main.png). At most 2 MB each.")).classes(
            "text-xs text-slate-500 -mt-2")

    # 3. The version -----------------------------------------------------------------
    with card(_("3. Version")):
        with ui.row().classes("w-full gap-3 no-wrap"):
            ui.input(_("Version")).props("outlined dense").classes("w-40").bind_value(
                draft.release, "version")
            entry = ui.select([], label=_("File to start"), with_input=True,
                              new_value_mode="add-unique").props("outlined dense").classes(
                "flex-grow").bind_value(draft.release, "entry")
        ui.textarea(_("What's new")).props("outlined autogrow").classes("w-full").bind_value(
            draft.release, "changelog")

        @ui.refreshable
        def type_fields() -> None:
            if draft.tool.type is ToolType.PYTHON or draft.tool.type == "python":
                ui.checkbox(_("Opens its own window (start without a console)")).bind_value(
                    draft.release, "gui")
            if draft.tool.type is ToolType.INSTALLER or draft.tool.type == "installer":
                ui.input(_("Silent install switches (optional)"), placeholder="/S").props(
                    "outlined dense").classes("w-64").bind_value(draft.release, "installer_args")

        type_fields()
        ui.checkbox(_("Recommend updating soon")).bind_value(draft.release, "important")
        ui.input(_("Why, in one sentence (shown to users)")).props("outlined dense").classes(
            "w-full").bind_value(draft.release, "important_note").bind_visibility_from(
            draft.release, "important")

    @ui.refreshable
    def args_card() -> None:
        if ToolType(draft.tool.type) is not ToolType.PYTHON:
            return
        with card(_("Parameters (the run page's form)")):
            args_editor(draft.release, lambda: draft.release.entry or "")

    args_card()

    def refresh_package() -> None:
        names.clear()
        path = Path(form["source"].strip().strip('"')) if form["source"].strip() else None
        if path is None:
            package_info.set_text("")
        elif not path.exists():
            package_info.set_text(_("Not found: {path}", path=path))
        else:
            try:
                names.extend(package_names(path))
            except (OSError, zipfile.BadZipFile):
                package_info.set_text(_("{name} is not a zip file", name=path.name))
            else:
                package_info.set_text(_("{count} files", count=len(names)))
        suffixes = _ENTRY_SUFFIXES[ToolType(draft.tool.type)]
        options = [n for n in names if n.lower().endswith(suffixes)]
        if draft.release.entry and draft.release.entry not in options:
            options.insert(0, draft.release.entry)
        entry.set_options(options)
        if not draft.release.entry:
            guessed = guess_entry(names, ToolType(draft.tool.type))
            if guessed:
                entry.set_value(guessed)

    source.on_value_change(lambda _e: refresh_package())
    if type_select is not None:
        type_select.on_value_change(
            lambda _e: (type_fields.refresh(), args_card.refresh(), refresh_package()))

    # 4. Check and publish -----------------------------------------------------------
    with card(_("4. Check and publish")):
        result_box = ui.column().classes("w-full gap-1")
        progress = ui.label().classes("text-sm text-slate-500")
        with ui.row().classes("gap-2"):
            check_button = ui.button(_("Check"), icon="fact_check").props(
                "outline no-caps color=grey-8")
            publish_button = ui.button(_("Publish"), icon="publish").props("unelevated no-caps")

    def sync_draft() -> None:
        for part in (draft.tool, draft.release):  # cleared inputs and selects give None
            for name, value in vars(part).items():
                if value is None:
                    setattr(part, name, "")
        draft.source = Path(form["source"].strip().strip('"'))
        draft.tool.owners = [o.strip() for o in form["owners"].split(",") if o.strip()]
        draft.tool.type = ToolType(draft.tool.type)
        draft.tool.id = draft.tool.id.strip()
        draft.release.version = draft.release.version.strip()

    async def run_check():
        sync_draft()
        if not form["source"].strip():
            show_report(Report(errors=[_("Choose the program (zip or folder) first")]))
            return None
        workdir = paths.data_root() / "publish"
        try:
            author = await ng_run.io_bound(state.publisher.current_user)
            prepared = await ng_run.io_bound(prepare, draft, paths.cache_dir() / "current",
                                             workdir, author)
        except (RepositoryError, OSError) as e:
            show_report(Report(errors=[str(e)]))
            return None
        show_report(prepared.report, prepared.files)
        return prepared

    def show_report(report: Report, files: list[str] | None = None) -> None:
        result_box.clear()
        with result_box:
            for text in report.errors:
                _line("error", "text-red-700", text)
            for text in report.warnings:
                _line("warning_amber", "text-amber-800", text)
            for text in report.notes:
                _line("info", "text-slate-600", text)
            if report.ok:
                _line("check_circle", "text-primary",
                      _("Ready to publish: {count} files will be committed",
                        count=len(files or [])))

    async def do_check() -> None:
        check_button.props("loading")
        try:
            await run_check()
        finally:
            check_button.props(remove="loading")

    async def do_publish() -> None:
        publish_button.props("loading")
        check_button.disable()
        try:
            prepared = await run_check()
            if prepared is None or not prepared.report.ok:
                return
            if not await _confirm(draft, len(prepared.files)):
                return
            steps = {"text": ""}
            ticker = ui.timer(0.3, lambda: progress.set_text(steps["text"]))

            def say(step: str) -> None:
                kind, _sep, rel = step.partition(":")
                steps["text"] = {
                    "upload": _("Uploading {file}...", file=rel.rsplit("/", 1)[-1]),
                    "commit": _("Saving {file}...", file=rel),
                    "pull-request": _("Opening the pull request..."),
                    "merge": _("Merging..."),
                }.get(kind, step)

            try:
                result = await ng_run.io_bound(submit, prepared, state.publisher, say)
            except (RepositoryError, PublishError, OSError) as e:
                log.warning("publish failed: %s", e)
                progress.set_text("")
                result_box.clear()
                with result_box:
                    _line("error", "text-red-700", _("Publishing failed: {error}",
                                                     error=_explain(e)))
                return
            finally:
                ticker.cancel()
            log.info("published %s %s: %s", draft.tool.id, draft.release.version, result)
            progress.set_text("")
            show_result(result)
            shutil.rmtree(paths.data_root() / "publish", ignore_errors=True)
            if result.merged:
                await sync()
        finally:
            publish_button.props(remove="loading")
            check_button.enable()

    def show_result(result: SubmitResult) -> None:
        result_box.clear()
        with result_box:
            if result.merged:
                _line("check_circle", "text-primary",
                      _("{name} {version} is published. Colleagues see it at their next sync.",
                        name=draft.tool.name, version=draft.release.version))
            elif result.waiting_reason == "new_tool":
                _line("hourglass_top", "text-slate-700",
                      _("Sent for review. {name} goes online once an admin approves the pull "
                        "request.", name=draft.tool.name))
            else:
                _line("warning_amber", "text-amber-800",
                      _("The pull request is open but could not be merged automatically: "
                        "{reason}", reason=result.waiting_reason))
            if result.url:
                ui.link(_("Open the pull request"), result.url, new_tab=True).classes(
                    "text-sm text-primary")
            if result.merged and not draft.is_new:
                ui.link(_("Back to {name}", name=draft.tool.name), f"/tool/{draft.tool.id}"
                        ).classes("text-sm text-primary no-underline")
        publish_button.disable()

    check_button.on_click(do_check)
    publish_button.on_click(do_publish)


def _line(icon: str, color: str, text: str) -> None:
    with ui.row().classes(f"items-start no-wrap gap-2 text-sm {color}"):
        ui.icon(icon, size="18px").classes("mt-0.5 shrink-0")
        ui.label(text)


def _explain(e: Exception) -> str:
    if isinstance(e, RepositoryError) and e.kind == "forbidden":
        return _("your account cannot write to the catalog repo. Ask an admin for write "
                 "access.")
    if isinstance(e, RepositoryError) and e.kind == "auth":
        return _("the access token is invalid or expired (Settings)")
    return str(e)


async def _confirm(draft: Draft, files: int) -> bool:
    with ui.dialog() as dialog, ui.card().classes("w-[440px] gap-3 p-5"):
        ui.label(_("Publish {name} {version}?", name=draft.tool.name,
                   version=draft.release.version)).classes("text-base font-semibold")
        ui.label(_("{count} files are committed to the catalog repo.", count=files)).classes(
            "text-sm text-slate-600")
        ui.label(_("An admin reviews new tools before they go online.") if draft.is_new
                 else _("Colleagues see the new version at their next sync.")).classes(
            "text-sm text-slate-600")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_("Cancel"), on_click=lambda: dialog.submit(False)).props(
                "flat color=grey-8")
            ui.button(_("Publish"), on_click=lambda: dialog.submit(True)).props("unelevated")
    return bool(await dialog)


async def _browse(field_input: ui.input, folder: bool) -> None:
    window = getattr(app.native, "main_window", None)
    if window is None:
        return
    import webview  # only available in the native (desktop) window

    kind = webview.FOLDER_DIALOG if folder else webview.OPEN_DIALOG
    types = () if folder else ("Zip (*.zip)",)
    result = await window.create_file_dialog(kind, file_types=types)
    if result:
        field_input.set_value(result[0])
