param(
    [string]$MsysRoot = 'C:\msys64',
    [string]$KLiteInstaller = '',
    [switch]$SkipKLite
)

$ErrorActionPreference = 'Stop'

function Find-OFRoot {
    param([string]$offRootDir)
    if (-not (Test-Path $offRootDir)) {
        throw "OFF-ROOT non trovato: $offRootDir"
    }
    $candidates = Get-ChildItem -Path $offRootDir -Directory | Where-Object {
        $_.Name -match '^of_v[0-9]+\.[0-9]+\.[0-9]+_msys2_mingw64_release$'
    } | Sort-Object Name -Descending
    if ($candidates.Count -eq 0) {
        throw "Nessuna release MSYS2 trovata in $offRootDir (attesa *_msys2_mingw64_release)"
    }
    return $candidates[0].FullName
}

function Invoke-BashBuild {
    param(
        [string]$MsysRoot,
        [string]$RepoRoot,
        [string]$OFRoot,
        [int]$Jobs = 1,
        [ValidateSet('Debug','Release')] [string]$Config = 'Release'
    )
    $bash = Join-Path $MsysRoot 'usr\bin\bash.exe'
    if (-not (Test-Path $bash)) { throw "bash non trovato: $bash" }

    $env:PATH = "$MsysRoot\\mingw64\\bin;$MsysRoot\\usr\\bin;" + $env:PATH

    function To-MSYSPath([string]$winPath) {
        $full = [System.IO.Path]::GetFullPath($winPath)
        $drive = $full.Substring(0,1).ToLower()
        $rest  = $full.Substring(2).TrimStart('\')
        $rest  = $rest -replace '\\', '/'
        return "/$drive/$rest"
    }

    $wsU = To-MSYSPath $RepoRoot
    $ofU = To-MSYSPath $OFRoot

    # Comando in-line semplice, senza cygpath e senza nidificare virgolette
    $makeCmd = "export MSYSTEM=MINGW64; export PATH=/mingw64/bin:/usr/bin:$PATH; cd '$wsU/OFF-player'; make -j$Jobs $Config OF_ROOT='$ofU'"

    & $bash -lc $makeCmd
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$offRootDir = Join-Path $repoRoot 'OFF-ROOT'
$ofRoot = Find-OFRoot -offRootDir $offRootDir

$cpu = [Environment]::ProcessorCount
$jobs = [Math]::Max(1, $cpu - 1)

Write-Host "[1/4] Compilazione ($jobs job) usando OF_ROOT: $ofRoot" -ForegroundColor Cyan
Invoke-BashBuild -MsysRoot $MsysRoot -RepoRoot $repoRoot -OFRoot $ofRoot -Jobs $jobs -Config 'Release'

Write-Host "[2/4] Copia DLL runtime" -ForegroundColor Cyan
$copyScript = Join-Path $repoRoot 'OFF-player\tools\copy_runtime_dlls.ps1'
if (-not (Test-Path $copyScript)) { throw "Script copia DLL non trovato: $copyScript" }
& powershell -NoProfile -ExecutionPolicy Bypass -File $copyScript -Config Release -MsysRoot $MsysRoot

Write-Host "[3/4] Installazione K-Lite (opzionale)" -ForegroundColor Cyan
if (-not $SkipKLite) {
    if (-not $KLiteInstaller) {
        $assetsDir = Join-Path $repoRoot 'installer\assets'
        if (Test-Path $assetsDir) {
            $candidates = Get-ChildItem -Path $assetsDir -Filter *.exe -ErrorAction SilentlyContinue | Sort-Object Name -Descending
            if ($candidates) {
                $pref = $candidates | Where-Object { $_.Name -match 'K[- ]?Lite|klite' } | Select-Object -First 1
                if (-not $pref) { $pref = $candidates | Select-Object -First 1 }
                if ($pref) { $KLiteInstaller = $pref.FullName }
            }
        }
        if (-not $KLiteInstaller) {
            $defaultKLite = Join-Path $repoRoot 'installer\assets\KLite_Codec_Pack_Basic.exe'
            if (Test-Path $defaultKLite) { $KLiteInstaller = $defaultKLite }
        }
    }
    if ($KLiteInstaller -and (Test-Path $KLiteInstaller)) {
        Write-Host "Avvio installer K-Lite in silent: $KLiteInstaller" -ForegroundColor Yellow
        try {
            Start-Process -FilePath $KLiteInstaller -ArgumentList '/verysilent','/norestart' -Wait
        } catch {
            Write-Warning "Installazione K-Lite fallita: $_"
        }
    } else {
        Write-Host "K-Lite non trovato (salto). Metti un EXE in installer\\assets (nome che contiene 'K-Lite') o passa -KLiteInstaller." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "Skip K-Lite su richiesta" -ForegroundColor DarkYellow
}

Write-Host "[4/6] Setup headless-player (Windows)" -ForegroundColor Cyan
$headlessSetup = Join-Path $repoRoot 'headless-player\setup_headless_windows.bat'
if (Test-Path $headlessSetup) {
    try {
        Start-Process -FilePath $headlessSetup -Verb RunAs -Wait
    } catch {
        Write-Warning "Esecuzione setup_headless_windows.bat fallita (prova a eseguire come amministratore): $_"
    }
} else {
    Write-Host "setup_headless_windows.bat non trovato (salto)." -ForegroundColor DarkYellow
}

# [5/6] Costruzione headless-player.exe con PyInstaller
Write-Host "[5/6] Costruzione headless-player.exe (PyInstaller)" -ForegroundColor Cyan
try {
    $venv = Join-Path $repoRoot 'headless-player\headless_venv'
    $python = if (Test-Path (Join-Path $venv 'Scripts\python.exe')) { Join-Path $venv 'Scripts\python.exe' } else { 'python' }
    & $python -m pip install --upgrade pip pyinstaller | Out-Null
    $hpDir = Join-Path $repoRoot 'headless-player'
    Push-Location $hpDir
    try {
        $dist = Join-Path $hpDir 'dist'
        $build = Join-Path $hpDir 'build'
        if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
        if (Test-Path $build) { Remove-Item -Recurse -Force $build }
        & $python -m PyInstaller --noconfirm --clean `
            --name headless-player `
            --distpath "$dist" --workpath "$build" `
            --paths "$hpDir" `
            --add-data "$hpDir\VERSION;." `
            --add-data "$hpDir\media;media" `
            app.py
    }
    finally { Pop-Location }
}
catch {
    Write-Warning "Costruzione headless-player.exe fallita: $_"
}

# Costruzione installer Inno Setup (se ISCC presente)
Write-Host "[6/6] Costruzione installer (Inno Setup)" -ForegroundColor Cyan
$iss = Join-Path $repoRoot 'installer\off-player.iss'
if (Test-Path $iss) {
    $candidates = @()
    if ($env:ISCC) { $candidates += $env:ISCC }
    $userIscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    $candidates += $userIscc
    $candidates += 'C:\\Program Files\\Inno Setup 6\\ISCC.exe'
    $candidates += 'C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe'
    $iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($iscc) {
        try {
            & $iscc $iss
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Installer generato in installer\\dist" -ForegroundColor Green
            } else {
                Write-Warning "Costruzione installer terminata con codice $LASTEXITCODE"
            }
        } catch {
            Write-Warning "Costruzione installer fallita: $_"
        }
    } else {
        Write-Host "ISCC.exe non trovato (salto). Installa Inno Setup o imposta $env:ISCC al percorso di ISCC.exe." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "Script Inno Setup non trovato: $iss (salto)." -ForegroundColor DarkYellow
}

Write-Host "Release completata." -ForegroundColor Green
