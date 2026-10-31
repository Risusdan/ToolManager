# Build the installable ToolManager layout (see docs/DESIGN.md §6.2, §10.3):
#
#   dist\ToolManager\
#     ToolManager.exe          C++ launcher (launcher\)
#     app-<version>\           PyInstaller onedir build of the Python app
#       ToolManagerApp.exe
#       .complete              written last: marks the folder as a finished build
#
# Requires: uv, CMake and a C++ compiler (Visual Studio Build Tools or MinGW).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# 1. Launcher (statically linked, no VC++ runtime needed on target PCs)
cmake -S launcher -B build\launcher
cmake --build build\launcher --config Release
ctest --test-dir build\launcher -C Release --output-on-failure
if ($LASTEXITCODE -ne 0) { throw "launcher tests failed" }
$launcher = Get-ChildItem build\launcher -Recurse -Filter ToolManager.exe | Select-Object -First 1

# 2. Python app (onedir: no unpacking on every start)
uv sync --group build
uv run nicegui-pack --onedir --windowed --noconfirm --clean --name ToolManagerApp scripts\entry.py
$version = (uv run python -c "import toolmanager; print(toolmanager.__version__)").Trim()

# 3. Assemble
$out = "dist\ToolManager"
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory $out | Out-Null
Copy-Item $launcher.FullName "$out\ToolManager.exe"
Copy-Item -Recurse "dist\ToolManagerApp" "$out\app-$version"
New-Item -ItemType File "$out\app-$version\.complete" | Out-Null

Write-Host "Built: $out\ToolManager.exe (launcher) + app-$version"
