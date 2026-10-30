# Build ToolManager as a PyInstaller onedir bundle (see docs/DESIGN.md §10.3).
# Output: dist\ToolManager\ToolManager.exe
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

uv sync --group build
uv run nicegui-pack --onedir --windowed --noconfirm --clean --name ToolManager scripts\entry.py

Write-Host "Built: dist\ToolManager\ToolManager.exe"
