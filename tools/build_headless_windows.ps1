param(
    [string]$Python = '',
    [switch]$UseVenv,
    [switch]$BuildDebug
)
$ErrorActionPreference = 'Stop'

function Get-Python {
    param([switch]$PreferVenv)
    $repo = Split-Path -Parent $PSScriptRoot
    $venvPy = Join-Path $repo 'headless-player\headless_venv\Scripts\python.exe'
    if ($PreferVenv -and (Test-Path $venvPy)) { return $venvPy }
    if ($env:PYTHON) { return $env:PYTHON }
    return 'python'
}

$py = if ($Python) { $Python } else { Get-Python -PreferVenv:$UseVenv }

Write-Host "Usando Python: $py" -ForegroundColor Cyan

# Assicura pip, dipendenze del progetto e PyInstaller
& $py -m pip install --upgrade pip | Out-Null

$repoRoot = Split-Path -Parent $PSScriptRoot
$hpDir = Join-Path $repoRoot 'headless-player'

# Installa le dipendenze del player headless (includono fastapi/uvicorn/Pillow/psutil/requests/PyQt5)
if (Test-Path (Join-Path $hpDir 'requirements.txt')) {
    Write-Host "Installo dipendenze da requirements.txt…" -ForegroundColor Cyan
    & $py -m pip install -r (Join-Path $hpDir 'requirements.txt')
} else {
    Write-Host "Attenzione: requirements.txt non trovato, installo set base pacchetti…" -ForegroundColor Yellow
    & $py -m pip install fastapi "uvicorn[standard]" pillow psutil requests PyQt5
}

# Installa/aggiorna PyInstaller
& $py -m pip install --upgrade pyinstaller | Out-Null

$dist = Join-Path $hpDir 'dist'
$build = Join-Path $hpDir 'build'
$workDebug = Join-Path $hpDir 'build_debug'

Push-Location $hpDir
try {
    if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
    if (Test-Path $build) { Remove-Item -Recurse -Force $build }
    & $py -m PyInstaller --noconfirm --clean `
        --name headless-player `
        --distpath "$dist" --workpath "$build" `
        --paths "$hpDir" `
        --add-data "$hpDir\VERSION;." `
        --add-data "$hpDir\media;media" `
        --hidden-import uvicorn `
        app.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller exit code $LASTEXITCODE" }
    Write-Host "headless-player.exe creato in $dist" -ForegroundColor Green

    if ($BuildDebug) {
    if (Test-Path $workDebug) { Remove-Item -Recurse -Force $workDebug }
        Write-Host "Crea variante debug con console: headless-player-debug.exe" -ForegroundColor Yellow
        & $py -m PyInstaller --noconfirm --clean `
            --name headless-player-debug `
            --distpath "$dist" --workpath "$workDebug" `
            --paths "$hpDir" `
            --add-data "$hpDir\VERSION;." `
            --add-data "$hpDir\media;media" `
            --log-level DEBUG `
            --console `
            --hidden-import uvicorn `
            app_debug.py
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller(debug) exit code $LASTEXITCODE" }
        Write-Host "headless-player-debug.exe creato in $dist" -ForegroundColor Green
    }
}
finally {
    Pop-Location
}
