"""Entry point. Keep imports above main() light: startup time is a P0 measurement."""

import time

_T0 = time.perf_counter()

import argparse  # noqa: E402
import multiprocessing  # noqa: E402


def main() -> None:
    multiprocessing.freeze_support()  # required for the frozen (PyInstaller) build
    parser = argparse.ArgumentParser(prog="toolmanager")
    parser.add_argument("--catalog", help="use a local catalog folder (dev / demo)")
    parser.add_argument("--browser", action="store_true", help="open in a browser, not native")
    parser.add_argument(
        "--exit-after-ready",
        action="store_true",
        help="log startup time once the first page is served, then quit (P0 measurement)",
    )
    args = parser.parse_args()

    from .ui.app import run

    run(
        t0=_T0,
        native_mode=not args.browser,
        local_catalog=args.catalog,
        exit_after_ready=args.exit_after_ready,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
