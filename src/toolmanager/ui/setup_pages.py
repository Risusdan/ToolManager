"""First-run setup wizard (/setup) and settings page (/settings). See docs/DESIGN.md §9."""

from __future__ import annotations

import copy
import logging
import webbrowser

from nicegui import run as ng_run
from nicegui import ui

from ..core import paths
from ..core.connection import check_connection, token_page_url
from ..core.credentials import CredentialError, get_token, set_token
from ..core.repository import RepositoryError
from ..core.settings import BitbucketSettings, Settings, normalize_base_url
from ..i18n import _
from .app import _header, configure, open_folder, state, sync

log = logging.getLogger(__name__)


def connection_error_message(e: Exception) -> str:
    kind = getattr(e, "kind", "other")
    if kind == "auth":
        return _("The token is invalid or has expired. Please create a new one.")
    if kind == "forbidden":
        return _("This token cannot read the catalog repository. Ask an admin for read access.")
    if kind == "not_found":
        return _("Project or repository not found. Check the Project key and Repository name.")
    if kind == "network":
        return _("Cannot reach the server. Check the address and that you are on the company "
                 "network or VPN.")
    return _("Connection failed: {error}", error=e)


def _server_errors(bb: BitbucketSettings) -> list[str]:
    errors = []
    try:
        bb.base_url = normalize_base_url(bb.base_url)
    except ValueError:
        errors.append(_("Server address must look like https://bitbucket.example.com"))
    if not bb.project.strip():
        errors.append(_("Project key is required"))
    if not bb.repo.strip():
        errors.append(_("Repository name is required"))
    if not bb.branch.strip():
        bb.branch = "main"
    bb.project, bb.repo, bb.branch = bb.project.strip(), bb.repo.strip(), bb.branch.strip()
    return errors


def _baseline() -> Settings:
    return Settings.load(None, paths.defaults_file())


def _stored_token(base_url: str) -> str | None:
    try:
        return get_token(base_url) if base_url else None
    except CredentialError:
        return None


async def _apply_and_sync(settings: Settings) -> None:
    if state.dev_override:
        ui.notify(_("Saved. Running with --catalog, so the new settings apply on next start."))
        return
    error = configure(settings)
    if error:
        ui.notify(error, type="negative")
        return
    await sync()


def _server_fields(bb: BitbucketSettings) -> None:
    ui.input(_("Server address"), placeholder="https://bitbucket.example.com").bind_value(
        bb, "base_url"
    ).props("outlined dense").classes("w-full")
    with ui.row().classes("w-full gap-4 no-wrap"):
        ui.input(_("Project key")).bind_value(bb, "project").props("outlined dense").classes(
            "flex-1"
        )
        ui.input(_("Repository")).bind_value(bb, "repo").props("outlined dense").classes("flex-1")
        ui.input(_("Branch")).bind_value(bb, "branch").props("outlined dense").classes("w-32")


@ui.page("/setup")
def setup_page() -> None:
    _header()
    settings = copy.deepcopy(state.settings)
    settings.backend = "bitbucket"
    bb = settings.bitbucket
    prefilled = bool(bb.base_url and bb.project and bb.repo)
    result: dict = {"token": "", "username": None}

    ui.label(_("Connect to the tool catalog")).classes("text-2xl font-bold")
    with ui.stepper().props("vertical flat").classes("w-full max-w-3xl") as stepper:
        with ui.step(_("Server")):
            if prefilled:
                ui.label(_("Filled in by your department. Usually nothing to change here.")
                         ).classes("text-sm text-slate-500")
            _server_fields(bb)
            server_error = ui.label().classes("text-sm text-red-700")

            def next_from_server() -> None:
                errors = _server_errors(bb)
                server_error.set_text(" ".join(errors))
                if not errors:
                    stepper.next()

            with ui.stepper_navigation():
                ui.button(_("Next"), on_click=next_from_server)

        with ui.step(_("Access token")):
            with ui.column().classes("gap-1 text-sm text-slate-700"):
                ui.label(_("1. Open Bitbucket and go to \"HTTP access tokens\"."))
                ui.label(_("2. Create a token named \"ToolManager\" with permission "
                           "\"Repository read\". An expiry date is fine."))
                ui.label(_("3. Copy the token and paste it below. It is stored in Windows "
                           "Credential Manager, not in a file."))
            ui.button(_("Open Bitbucket"), icon="open_in_new",
                      on_click=lambda: webbrowser.open(token_page_url(bb.base_url))).props(
                "outline no-caps"
            )
            ui.label(_("If the link does not work: avatar (top right) → Manage account → "
                       "HTTP access tokens.")).classes("text-xs text-slate-500")
            token_input = ui.input(_("Token"), password=True, password_toggle_button=True).props(
                "outlined dense"
            ).classes("w-full")
            token_error = ui.label().classes("text-sm text-red-700")

            async def verify() -> None:
                token = (token_input.value or "").strip()
                if not token:
                    token_error.set_text(_("Paste the token first"))
                    return
                token_error.set_text(_("Checking..."))
                try:
                    info = await ng_run.io_bound(check_connection, bb, token)
                except RepositoryError as e:
                    token_error.set_text(connection_error_message(e))
                    return
                token_error.set_text("")
                result.update(token=token, username=info.username)
                done_label.set_text(_("Connected as {user}.", user=info.username or "?"))
                stepper.next()

            with ui.stepper_navigation():
                ui.button(_("Verify and continue"), on_click=verify)
                ui.button(_("Back"), on_click=stepper.previous).props("flat")

        with ui.step(_("Done")):
            done_label = ui.label().classes("text-slate-700")
            save_error = ui.label().classes("text-sm text-red-700")

            async def finish() -> None:
                try:
                    set_token(bb.base_url, result["token"])
                except CredentialError as e:
                    save_error.set_text(
                        _("Could not save the token to Windows Credential Manager: {error}",
                          error=e)
                    )
                    return
                settings.save(paths.settings_file(), _baseline())
                await _apply_and_sync(settings)
                ui.navigate.to("/")

            with ui.stepper_navigation():
                ui.button(_("Start using ToolManager"), on_click=finish)


@ui.page("/settings")
def settings_page() -> None:
    _header()
    # Edit what is saved on disk (not a --catalog dev override).
    settings = Settings.load(paths.settings_file(), paths.defaults_file())
    original = copy.deepcopy(settings)
    bb = settings.bitbucket
    form = {"token": ""}

    ui.label(_("Settings")).classes("text-2xl font-bold")
    if state.dev_override:
        ui.label(_("Running with --catalog: changes are saved but apply on next start.")
                 ).classes("text-sm text-amber-800")

    with ui.card().classes("w-full max-w-3xl"):
        ui.label(_("Connection")).classes("text-lg font-semibold")
        _server_fields(bb)
        has_token = _stored_token(bb.base_url) is not None
        ui.label(_("A token is saved for this server.") if has_token
                 else _("No token saved for this server yet.")).classes("text-sm text-slate-500")
        ui.input(_("New token (leave empty to keep the current one)"), password=True,
                 password_toggle_button=True).bind_value(form, "token").props(
            "outlined dense"
        ).classes("w-full")
        status = ui.label().classes("text-sm")

        async def test() -> bool:
            errors = _server_errors(bb)
            if errors:
                status.set_text(" ".join(errors))
                status.classes(replace="text-sm text-red-700")
                return False
            token = form["token"].strip() or _stored_token(bb.base_url)
            if not token:
                status.set_text(_("Paste a token first"))
                status.classes(replace="text-sm text-red-700")
                return False
            status.set_text(_("Checking..."))
            status.classes(replace="text-sm text-slate-500")
            try:
                info = await ng_run.io_bound(check_connection, bb, token)
            except RepositoryError as e:
                status.set_text(connection_error_message(e))
                status.classes(replace="text-sm text-red-700")
                return False
            status.set_text(_("Connected as {user}.", user=info.username or "?"))
            status.classes(replace="text-sm text-slate-600")
            return True

        ui.button(_("Test connection"), on_click=test).props("outline no-caps")

    with ui.card().classes("w-full max-w-3xl"):
        ui.label(_("Updates and Trash")).classes("text-lg font-semibold")
        with ui.row().classes("gap-4"):
            ui.number(_("Check for updates every (hours)"), min=1, max=24, precision=0
                      ).bind_value(settings, "check_interval_hours",
                                   forward=lambda v: int(v or 4)).props("outlined dense").classes(
                "w-56"
            )
            ui.number(_("Keep items in the Trash (days)"), min=1, max=90, precision=0
                      ).bind_value(settings, "trash_retention_days",
                                   forward=lambda v: int(v or 7)).props("outlined dense").classes(
                "w-56"
            )

    with ui.card().classes("w-full max-w-3xl"):
        ui.label(_("Python packages")).classes("text-lg font-semibold")
        ui.input(_("PyPI mirror URL (used by Python tools)"),
                 placeholder="https://pypi.example.com/simple").bind_value(
            settings, "pypi_index_url"
        ).props("outlined dense").classes("w-full")

    with ui.card().classes("w-full max-w-3xl"):
        ui.label(_("Folders")).classes("text-lg font-semibold")
        ui.label(str(paths.data_root())).classes("text-sm text-slate-500 font-mono")
        with ui.row():
            ui.button(_("Open data folder"), on_click=lambda: open_folder(paths.data_root())
                      ).props("flat no-caps color=grey-8")
            ui.button(_("Open log folder"), on_click=lambda: open_folder(paths.logs_dir())
                      ).props("flat no-caps color=grey-8")

    save_error = ui.label().classes("text-sm text-red-700")

    async def save() -> None:
        if settings.pypi_index_url.strip():
            try:
                settings.pypi_index_url = normalize_base_url(settings.pypi_index_url)
            except ValueError:
                save_error.set_text(_("PyPI mirror URL must start with https://"))
                return
        token = form["token"].strip()
        connection_changed = token or bb != original.bitbucket
        if connection_changed and not await test():
            save_error.set_text(_("Not saved: fix the connection first."))
            return
        if token:
            try:
                set_token(bb.base_url, token)
            except CredentialError as e:
                save_error.set_text(
                    _("Could not save the token to Windows Credential Manager: {error}", error=e)
                )
                return
        save_error.set_text("")
        if bb.base_url:
            settings.backend = "bitbucket"
        settings.save(paths.settings_file(), _baseline())
        ui.notify(_("Settings saved"), type="positive")
        await _apply_and_sync(settings)

    ui.button(_("Save"), icon="save", on_click=save).classes("mt-2")
