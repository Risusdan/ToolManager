"""Editor for a Python script's parameters ([[args]]) in the publish wizard, with a preview of
the run page's form and of the command line it produces."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ..core.models import ArgSpec, ArgType
from ..core.publish import ArgDraft, ReleaseInfo, arg_specs, args_problems
from ..i18n import _
from .run_page import arg_field


def _type_options() -> dict[str, str]:
    return {
        ArgType.STRING: _("Text"),
        ArgType.INT: _("Integer"),
        ArgType.FLOAT: _("Number"),
        ArgType.BOOL: _("Checkbox (on/off)"),
        ArgType.FILE: _("File"),
        ArgType.FOLDER: _("Folder"),
        ArgType.CHOICE: _("Choice from a list"),
    }


def command_preview(entry: str, specs: tuple[ArgSpec, ...]) -> str:
    """The command a run with the default values gives; empty fields show as <label>."""
    parts = ["python", entry or "main.py"]
    positional = []
    for spec in specs:
        if spec.type is ArgType.BOOL:
            if spec.default:
                parts.append(spec.cli_flag)
            continue
        value = "" if spec.default is None else str(spec.default)
        if not value:
            if not (spec.required or spec.positional):
                continue  # left out: the script's own default applies
            value = f"<{spec.label}>"
        elif " " in value:
            value = f'"{value}"'
        if spec.positional:
            positional.append(value)
        else:
            parts += [spec.cli_flag, value]
    return " ".join(parts + positional)


def args_editor(release: ReleaseInfo, entry: Callable[[], str]) -> None:
    """Rows of fields, one per parameter, then the live preview."""
    ui.label(_("Parameters fill a form on the run page and become command-line arguments, "
               "e.g. --input <file>. Without parameters the script runs directly in a "
               "console.")).classes("text-xs text-slate-500")

    @ui.refreshable
    def preview() -> None:
        tables, errors = args_problems(release.args)
        for text in errors:
            with ui.row().classes("items-start no-wrap gap-2 text-sm text-red-700"):
                ui.icon("error", size="18px").classes("mt-0.5")
                ui.label(text)
        specs = arg_specs(release.args)
        if not specs:
            return
        with ui.column().classes("w-full gap-2 bg-slate-50 rounded-lg p-3"):
            ui.label(_("Preview of the run page")).classes(
                "text-xs font-semibold text-slate-500")
            values: dict = {}
            for spec in specs:
                arg_field(spec, values)
            ui.label(command_preview(entry(), specs)).classes(
                "text-xs font-mono text-slate-600 break-all")

    @ui.refreshable
    def rows() -> None:
        for index, arg in enumerate(release.args):
            _row(release, index, arg, on_change=preview.refresh, on_layout=refresh_all)
        ui.button(_("Add parameter"), icon="add", on_click=add).props(
            "outline no-caps color=grey-8 dense").classes("px-3")

    def refresh_all() -> None:
        rows.refresh()
        preview.refresh()

    def add() -> None:
        release.args.append(ArgDraft(name=f"arg{len(release.args) + 1}"))
        refresh_all()

    rows()
    preview()


def _row(release: ReleaseInfo, index: int, arg: ArgDraft, on_change: Callable[[], None],
         on_layout: Callable[[], None]) -> None:
    args = release.args

    def move(step: int) -> None:
        target = index + step
        if 0 <= target < len(args):
            args[index], args[target] = args[target], args[index]
            on_layout()

    def remove() -> None:
        args.pop(index)
        on_layout()

    def type_changed() -> None:
        kind = ArgType(arg.type)
        arg.default = False if kind is ArgType.BOOL else (
            "" if isinstance(arg.default, bool) else arg.default)
        if kind is ArgType.BOOL:
            arg.positional = False
        on_layout()  # the default field changes shape

    kind = ArgType(arg.type)
    with ui.column().classes("w-full gap-2 border border-slate-200 rounded-lg p-3"):
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            ui.input(_("Name (for the command line)")).props("outlined dense").classes(
                "w-44").bind_value(arg, "name").on_value_change(on_change)
            ui.input(_("Label on the form")).props("outlined dense").classes(
                "flex-grow").bind_value(arg, "label").on_value_change(on_change)
            ui.select(_type_options(), label=_("Type")).props("outlined dense").classes(
                "w-40").bind_value(arg, "type").on_value_change(type_changed)
            ui.button(icon="arrow_upward", on_click=lambda: move(-1)).props(
                "flat dense round color=grey-7").tooltip(_("Move up"))
            ui.button(icon="arrow_downward", on_click=lambda: move(1)).props(
                "flat dense round color=grey-7").tooltip(_("Move down"))
            ui.button(icon="delete_outline", on_click=remove).props(
                "flat dense round color=grey-7").tooltip(_("Remove"))
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            if kind is ArgType.BOOL:
                ui.checkbox(_("Ticked by default")).bind_value(arg, "default").on_value_change(
                    on_change)
            else:
                ui.input(_("Default (optional)")).props("outlined dense").classes(
                    "w-44").bind_value(arg, "default").on_value_change(on_change)
            if kind is ArgType.CHOICE:
                ui.input(_("Choices, comma separated")).props("outlined dense").classes(
                    "flex-grow").bind_value(arg, "choices").on_value_change(on_change)
            ui.input(_("Help text (optional)")).props("outlined dense").classes(
                "flex-grow").bind_value(arg, "help").on_value_change(on_change)
        with ui.row().classes("w-full items-center gap-4"):
            ui.checkbox(_("Required")).bind_value(arg, "required").on_value_change(on_change)
            if kind is not ArgType.BOOL:
                ui.checkbox(_("Positional (value only, no --name)")).bind_value(
                    arg, "positional").on_value_change(on_change)
            ui.input(_("Command-line flag (default --name)"),
                     placeholder=f"--{arg.name or 'name'}").props("outlined dense").classes(
                "w-56").bind_value(arg, "flag").on_value_change(on_change)
