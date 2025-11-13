Param(
    [string]$ProjectRoot = "..",
    [string]$Entry = "GUI/main.py",
    [string]$Name = "marocco-manager",
    [string]$Settings = "GUI/settings.json",
    [string]$Viewer = "GUI/vncviewer64-1.15.0.exe"
)

Set-StrictMode -Version Latest
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir $ProjectRoot)).Path
Write-Host "Building GUI executable from $Root..."

Push-Location $Root
try {
    $python = "python"
    $pyinstallerCheck = & $python -m pip show pyinstaller 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PyInstaller not installed; installing..."
        & $python -m pip install pyinstaller | Write-Host
    }

    $IsWindows = $env:OS -eq "Windows_NT"
    $sep = if ($IsWindows) { ";" } else { ":" }
    $settingsPath = (Resolve-Path $Settings).Path
    $viewerPath = (Resolve-Path $Viewer).Path
    $args = @(
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onefile",
        "--name", $Name,
        "--distpath", "dist",
        "--specpath", "build",
        "--add-data", "$settingsPath$sep.",
        "--add-data", "$viewerPath$sep.",
        $Entry
    )

    Write-Host "Running PyInstaller..." -ForegroundColor Cyan
    & $python -m PyInstaller @args
}
finally {
    Pop-Location
}
