# Build the installable ToolManager layout (see docs/DESIGN.md §6.2, §10.3):
#
#   dist\ToolManager\
#     ToolManager.exe          C++ launcher (launcher\)
#     app-<version>\           PyInstaller onedir build of the Python app
#       ToolManagerApp.exe
#       .complete              written last: marks the folder as a finished build
#
# Requires: uv, CMake and a C++ compiler (Visual Studio Build Tools or MinGW).
# Optional: Inno Setup 6 (ISCC.exe) to also build dist\ToolManagerSetup-<version>.exe.
#   -DefaultsFile <path>  bundle the department defaults.toml into the installer
param([string]$DefaultsFile = "")
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

# 4. Release zip for publishing ToolManager itself to the catalog (tool id "toolmanager");
#    installed copies download it and prepare app-<version> next to their launcher.
$zip = "dist\toolmanager-$version.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\ToolManagerApp\*" -DestinationPath $zip

# 5. Installer (first installation for colleagues)
$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($iscc) {
    $isccArgs = @("/DAppVersion=$version")
    if ($DefaultsFile) { $isccArgs += "/DDefaultsFile=$((Resolve-Path $DefaultsFile).Path)" }
    & $iscc @isccArgs installer\ToolManager.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Installer: dist\ToolManagerSetup-$version.exe"
} else {
    Write-Warning "Inno Setup 6 not found: skipped the installer (https://jrsoftware.org/isdl.php)"
}

Write-Host "Built: $out\ToolManager.exe (launcher) + app-$version"
Write-Host "Release zip for the catalog: $zip"
