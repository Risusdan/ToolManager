"""PyInstaller entry script (nicegui-pack needs a file, not a module)."""

from toolmanager.main import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
