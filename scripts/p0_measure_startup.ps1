# P0: measure startup time of the packaged app.
# Runs the exe N times with --exit-after-ready and reads the timings it logs.
param(
    [string]$Exe = "dist\ToolManager\ToolManager.exe",
    [string]$Catalog = "examples\catalog",
    [int]$Runs = 5
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$home_ = Join-Path $env:TEMP "toolmanager-p0"
Remove-Item -Recurse -Force $home_ -ErrorAction SilentlyContinue
$env:TOOLMANAGER_HOME = $home_
$log = Join-Path $home_ "logs\toolmanager.log"
$catalogPath = (Resolve-Path $Catalog).Path

for ($i = 1; $i -le $Runs; $i++) {
    $wall = Measure-Command {
        Start-Process -FilePath $Exe -ArgumentList "--catalog", "`"$catalogPath`"", "--exit-after-ready" -Wait
    }
    $server = (Select-String -Path $log -Pattern "server ready ([\d.]+)s after" | Select-Object -Last 1).Matches[0].Groups[1].Value
    $page = (Select-String -Path $log -Pattern "first page served ([\d.]+)s" | Select-Object -Last 1).Matches[0].Groups[1].Value
    "run {0}: server ready {1}s, first page {2}s, total wall {3:N2}s" -f $i, $server, $page, $wall.TotalSeconds
}
"(run 1 is a cold start; later runs are warm. Target: first page <= 3s.)"
