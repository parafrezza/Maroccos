param(
    [switch]$Debug,
    [switch]$SkipRebuild
)
$ErrorActionPreference = 'Stop'

if ($Debug) {
    throw "L'opzione -Debug non è più supportata: OFF-player viene compilato solo in Release."
}

# Percorsi
$root = Split-Path -Parent $PSScriptRoot
$offDir = Join-Path $root 'OFF-player'
$copyDllScript = Join-Path $offDir 'tools\copy_runtime_dlls.ps1'
$buildHeadless = Join-Path $PSScriptRoot 'build_headless_windows.ps1'
$buildInstaller = Join-Path $PSScriptRoot 'build_installer.ps1'
$killOffScript = Join-Path $PSScriptRoot 'kill_off_player.ps1'
$iconMakerDir = Join-Path $root 'icon-maker'
$iconSrcPng = Join-Path $iconMakerDir 'icon.png'
$iconOutIco = Join-Path $iconMakerDir 'dist\morocco-player\morocco-player.ico'
$venvPy = Join-Path $root '.venv\Scripts\python.exe'

function Get-NewestTimestamp {
    param([string]$Path, [string[]]$Include, [string[]]$ExcludeDirs)
    $items = Get-ChildItem -LiteralPath $Path -Recurse -File -Include $Include -ErrorAction SilentlyContinue | Where-Object {
        $dir = $_.DirectoryName
        foreach ($ex in $ExcludeDirs) { if ($dir -like "*$ex*") { return $false } }
        return $true
    }
    if (-not $items) { return $null }
    return ($items | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
}

function Should-RebuildHeadless {
    param([switch]$ForDebug)
    if ($SkipRebuild) { return $false }
    $hpDir = Join-Path $root 'headless-player'
    $distDir = Join-Path $hpDir 'dist'
    if ($ForDebug) { $exeDir = Join-Path $distDir 'headless-player-debug' } else { $exeDir = Join-Path $distDir 'headless-player' }
    if ($ForDebug) { $exeName = 'headless-player-debug.exe' } else { $exeName = 'headless-player.exe' }
    $exePath = Join-Path $exeDir $exeName
    if (-not (Test-Path -LiteralPath $exePath)) { return $true }
    $srcNewest = Get-NewestTimestamp -Path $hpDir -Include @('*.py','requirements.txt','VERSION') -ExcludeDirs @('dist','build','build_debug','headless_venv','__pycache__')
    if (-not $srcNewest) { return $false }
    $exeTime = (Get-Item -LiteralPath $exePath).LastWriteTime
    return ($exeTime -lt $srcNewest)
}

function Should-RebuildOFF {
    if ($SkipRebuild) { return $false }
    $bin = Join-Path $offDir 'bin'
    $exe = Join-Path $bin 'OFF-player.exe'
    if (-not (Test-Path -LiteralPath $exe)) { return $true }
    $srcNewest = Get-NewestTimestamp -Path $offDir -Include @('*.cpp','*.c','*.h','*.hpp','addons.make','config.make','Makefile') -ExcludeDirs @('bin','obj','.git')
    if (-not $srcNewest) { return $false }
    $exeTime = (Get-Item -LiteralPath $exe).LastWriteTime
    return ($exeTime -lt $srcNewest)
}

<#
  0) Versione: bump patch SOLO se ricompiliamo headless-player (smart skip attivo di default).
     Se si usa -SkipRebuild o se lo smart skip decide per lo skip, non cambiamo la versione.
#>
$verFile = Join-Path $root 'headless-player\VERSION'
if (Test-Path -LiteralPath $verFile) {
    $ver = (Get-Content -LiteralPath $verFile -Raw).Trim()
    $ver = $ver.TrimStart('v','V')
    $willBuildHeadless = Should-RebuildHeadless -ForDebug:$Debug
    if ($willBuildHeadless) {
    if ($ver -match '^(\d+)\.(\d+)\.(\d+)$') {
        $maj = [int]$Matches[1]; $min = [int]$Matches[2]; $pat = [int]$Matches[3] + 1
        $newVer = "$maj.$min.$pat"
        Set-Content -LiteralPath $verFile -Value $newVer -NoNewline
        Write-Host "[VERSION] headless-player: $ver -> $newVer" -ForegroundColor Yellow
    } else {
        Write-Warning "[VERSION] Formato VERSION inatteso ('$ver'), nessun bump eseguito"
    }
    }
}

# 0-bis) Kill leftover OFF-player processes (avoid linker lock)
if (Test-Path -LiteralPath $killOffScript) {
    Write-Host '==> Stop OFF-player residui' -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File $killOffScript -Quiet
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Terminazione OFF-player incompleta: verificare manualmente.' }
}

# 1) Build headless-player (release)
Write-Host '==> Build headless-player' -ForegroundColor Cyan
$doHeadless = Should-RebuildHeadless -ForDebug:$Debug
if (-not $doHeadless) {
    Write-Host '[SKIP] headless-player up-to-date: salto rebuild' -ForegroundColor DarkGreen
} else {
    Write-Host '[BUILD] headless-player' -ForegroundColor Green
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildHeadless @(
        if ($Debug) { '-BuildDebug' }
    )
    if ($LASTEXITCODE -ne 0) { throw "Build headless-player fallita ($LASTEXITCODE)" }
}

# 2) Build OFF-player con MSYS2 (Release/Debug)
Write-Host '==> Build OFF-player (MSYS2)' -ForegroundColor Cyan
$bash = 'C:\msys64\usr\bin\bash.exe'
if (-not (Test-Path $bash)) { throw "MSYS2 non trovato in $bash" }
$msysEnv = 'export MSYSTEM=MINGW64; export PATH=/mingw64/bin:/usr/bin:$PATH;'
$ws = $root
$wsUnix = & $bash -lc "cygpath -u '$ws'"
$ofRootUnix = "$wsUnix/OFF-ROOT/of_v0.12.1_msys2_mingw64_release"
$cfg = 'Release'
$doOff = Should-RebuildOFF
if (-not $doOff) {
    Write-Host '[SKIP] OFF-player up-to-date: salto rebuild' -ForegroundColor DarkGreen
} else {
    Write-Host "[BUILD] OFF-player ($cfg)" -ForegroundColor Green
    $cmd = "$msysEnv cd '$wsUnix/OFF-player'; make $cfg OF_ROOT='$ofRootUnix'"
    & $bash -lc $cmd
    if ($LASTEXITCODE -ne 0) { throw "make $cfg fallito ($LASTEXITCODE)" }
}

# 3) Copia DLL runtime
Write-Host '==> Copia DLL runtime' -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File $copyDllScript -Config $cfg
if ($LASTEXITCODE -ne 0) { throw "copy_runtime_dlls.ps1 fallito ($LASTEXITCODE)" }

# 4) Genera icone (.ico) se mancanti o obsolete
Write-Host '==> Preparo icone' -ForegroundColor Cyan
try {
    $needIcons = $true
    if (Test-Path -LiteralPath $iconOutIco) {
        $icoTime = (Get-Item -LiteralPath $iconOutIco).LastWriteTime
        if (Test-Path -LiteralPath $iconSrcPng) {
            $pngTime = (Get-Item -LiteralPath $iconSrcPng).LastWriteTime
            $needIcons = ($icoTime -lt $pngTime)
        } else {
            $needIcons = $false
        }
    }
    if ($needIcons) {
        $py = if (Test-Path -LiteralPath $venvPy) { $venvPy } else { 'python' }
        Write-Host "[BUILD] Icone con $py" -ForegroundColor Green
        Push-Location $iconMakerDir
        & $py 'build_app_icons.py' --src 'icon.png' --name 'morocco-player' --outdir 'dist'
        $code = $LASTEXITCODE
        Pop-Location
        if ($code -ne 0) { Write-Warning "Generazione icone fallita (codice $code), proseguo senza icone personalizzate" }
    } else {
        Write-Host '[SKIP] Icone up-to-date' -ForegroundColor DarkGreen
    }
}
catch {
    Write-Warning "Generazione icone non riuscita: $_"
}

# 5) Costruisci installer con Inno Setup e versione da headless-player/VERSION
Write-Host '==> Build installer' -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File $buildInstaller @(
    if ($Debug) { '-Debug' }
)
if ($LASTEXITCODE -ne 0) { throw "build_installer.ps1 fallito ($LASTEXITCODE)" }

Write-Host "Build COMPLETATA: installer in 'installer\\dist'" -ForegroundColor Green
