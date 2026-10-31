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
#   -PythonDir <path>     Python 3.12 folder to bundle for script tools (default: a
#                         uv-managed, relocatable CPython 3.12; see docs/DESIGN.md §5.1)
#   -CustomerDefaultsFile <path>  also build dist\ToolManager-<version>-customer.zip, an
#                         unzip-and-run copy for customers (see docs/CUSTOMER.md); the file
#                         must point at the customer catalog repo and say portable = true
param([string]$DefaultsFile = "", [string]$PythonDir = "", [string]$CustomerDefaultsFile = "")
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

# 2b. Runtime for Python script tools, inside the app folder (runtime\python, runtime\uv.exe):
#     colleagues' PCs never download an interpreter.
if (-not $PythonDir) {
    $pyExe = (uv python find 3.12 --managed-python 2>$null)
    if (-not $pyExe) {
        # Needs access to the interpreter downloads (GitHub) or UV_PYTHON_INSTALL_MIRROR.
        uv python install 3.12
        $pyExe = (uv python find 3.12 --managed-python)
    }
    $PythonDir = Split-Path $pyExe.Trim()
}
$runtime = "dist\ToolManagerApp\runtime"
New-Item -ItemType Directory $runtime -Force | Out-Null
Copy-Item -Recurse $PythonDir "$runtime\python"
Copy-Item (Get-Command uv).Source "$runtime\uv.exe"
& "$runtime\python\python.exe" -c "import sys, venv; print('bundled Python', sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "bundled Python does not run" }

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

# 4b. Customer copy: same program, customer catalog, data kept inside the unzipped folder
if ($CustomerDefaultsFile) {
    $customerDefaults = Get-Content -Raw (Resolve-Path $CustomerDefaultsFile)
    if ($customerDefaults -notmatch '(?m)^\s*portable\s*=\s*true') {
        throw "$CustomerDefaultsFile must contain 'portable = true' (data stays inside the unzipped folder)"
    }
    $stage = "dist\customer\ToolManager"
    Remove-Item -Recurse -Force "dist\customer" -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $stage | Out-Null
    Copy-Item "$out\ToolManager.exe" $stage
    Copy-Item -Recurse "$out\app-$version" "$stage\app-$version"
    Copy-Item (Resolve-Path $CustomerDefaultsFile) "$stage\defaults.toml"
    $customerZip = "dist\ToolManager-$version-customer.zip"
    Remove-Item $customerZip -ErrorAction SilentlyContinue
    Compress-Archive -Path "dist\customer\ToolManager" -DestinationPath $customerZip
    Write-Host "Customer copy: $customerZip"
}

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
