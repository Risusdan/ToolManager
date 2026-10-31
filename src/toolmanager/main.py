"""Entry point. Keep imports above main() light: startup time is a P0 measurement."""

import time

_T0 = time.perf_counter()

import argparse  # noqa: E402
import multiprocessing  # noqa: E402


def main() -> None:
    multiprocessing.freeze_support()  # required for the frozen (PyInstaller) build
    # The desktop window runs in a spawned child process that re-imports this module and
    # so calls main() again. Everything below (instance lock, configuration, cleanup) must
    # only happen in the real application process; NiceGUI starts the window by itself.
    if multiprocessing.current_process().name != "MainProcess":
        return
    parser = argparse.ArgumentParser(prog="toolmanager")
    parser.add_argument("--catalog", help="use a local catalog folder (dev / demo)")
    parser.add_argument("--browser", action="store_true", help="open in a browser, not native")
    parser.add_argument(
        "--exit-after-ready",
        action="store_true",
        help="log startup time once the first page is served, then quit (P0 measurement)",
    )
    args = parser.parse_args()

    # Before loading the UI framework, so a second start returns almost immediately.
    from .core import paths
    from .core.single_instance import (
        InstanceLock,
        activate_running,
        show_already_running_message,
    )

    instance = InstanceLock(paths.data_root())
    if not instance.acquire():
        if not activate_running(paths.data_root()):
            show_already_running_message()
        # Always exit 0: the launcher treats an early non-zero exit as a broken version.
        return

    from .ui.app import run

    run(
        t0=_T0,
        native_mode=not args.browser,
        local_catalog=args.catalog,
        exit_after_ready=args.exit_after_ready,
        instance=instance,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
