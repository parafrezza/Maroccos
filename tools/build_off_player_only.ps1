param(
    [string]$MsysRoot = 'C:\msys64',
    [switch]$BumpVersion,
    [string]$Version
)

$ErrorActionPreference = 'Stop'

function Test-FileUnlocked {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $true }
    $temp = "$Path.lockcheck"
    try {
        Move-Item -Path $Path -Destination $temp -Force
        Move-Item -Path $temp -Destination $Path -Force
        return $true
    } catch {
        try {
            if (Test-Path $temp) {
                Move-Item -Path $temp -Destination $Path -Force
            }
        } catch {}
        return $false
    }
}

function Stop-OffPlayerProcesses {
    param(
        [string[]]$ProcessNames = @('OFF-player'),
        [int]$TimeoutSeconds = 5,
        [string]$ExePath = $null
    )
    $zombieDetected = $false
    foreach ($name in $ProcessNames) {
        try {
            $procs = Get-Process -Name $name -ErrorAction SilentlyContinue
            if ($null -eq $procs -or $procs.Count -eq 0) {
                continue
            }
            foreach ($proc in $procs) {
                Write-Host ("Chiudo processo bloccante {0} (PID {1})" -f $proc.ProcessName, $proc.Id) -ForegroundColor Yellow
                try {
                    Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                } catch {
                    Write-Warning ("Impossibile terminare PID {0}: {1}" -f $proc.Id, $_.Exception.Message)
                }
            }
            $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
            while ((Get-Process -Name $name -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
                Start-Sleep -Milliseconds 250
            }
            $remaining = Get-Process -Name $name -ErrorAction SilentlyContinue
            if ($null -ne $remaining -and $remaining.Count -gt 0) {
                foreach ($proc in $remaining) {
                    Write-Warning ("Processo {0} (PID {1}) ancora attivo, forzo con taskkill." -f $proc.ProcessName, $proc.Id)
                    try {
                        & taskkill.exe /PID $proc.Id /F /T | Out-Null
                    } catch {
                        Write-Warning ("taskkill fallito per PID {0}: {1}" -f $proc.Id, $_.Exception.Message)
                    }
                }
                Start-Sleep -Seconds 1
                if (Get-Process -Name $name -ErrorAction SilentlyContinue) {
                    $zombieDetected = $true
                }
            }
        } catch {
            Write-Warning ("Errore durante l'arresto dei processi {0}: {1}" -f $name, $_.Exception.Message)
        }
    }
    if ($ExePath) {
        if (-not (Test-FileUnlocked -Path $ExePath)) {
            throw "OFF-player.exe risulta ancora in uso (massimo). Chiudi manualmente il programma e riprova."
        }
    }
    if ($zombieDetected) {
        Write-Warning "Rilevati processi OFF-player zombie (non terminabili) ma l'eseguibile non è bloccato; continuo la build."
    }
}

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

function Convert-ToMsysPath {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $drive = $full.Substring(0,1).ToLower()
    $rest  = $full.Substring(2).TrimStart('\\')
    $rest  = $rest -replace '\\', '/'
    return "/$drive/$rest"
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

    $wsU = Convert-ToMsysPath -Path $RepoRoot
    $ofU = Convert-ToMsysPath -Path $OFRoot

    $makeCmd = "export MSYSTEM=MINGW64; export PATH=/mingw64/bin:/usr/bin:$PATH; cd '$wsU/OFF-player'; make -j$Jobs $Config OF_ROOT='$ofU'"

    & $bash -lc $makeCmd
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$offRootDir = Join-Path $repoRoot 'OFF-ROOT'
$ofRoot = Find-OFRoot -offRootDir $offRootDir

$cpu = [Environment]::ProcessorCount
$jobs = [Math]::Max(1, $cpu - 1)

$offPlayerExe = Join-Path $repoRoot 'OFF-player\bin\OFF-player.exe'
Stop-OffPlayerProcesses -ExePath $offPlayerExe

Write-Host "[1/2] Compilazione OFF-player ($jobs job) usando OF_ROOT: $ofRoot" -ForegroundColor Cyan
Invoke-BashBuild -MsysRoot $MsysRoot -RepoRoot $repoRoot -OFRoot $ofRoot -Jobs $jobs -Config 'Release'

$copyScript = Join-Path $repoRoot 'OFF-player\tools\copy_runtime_dlls.ps1'
if (Test-Path $copyScript) {
    Write-Host "[2/2] Copia delle DLL runtime in OFF-player\\bin" -ForegroundColor Cyan
    & $copyScript -Config 'Release' -MsysRoot $MsysRoot
    Write-Host "OFF-player build completata con librerie sincronizzate." -ForegroundColor Green
} else {
    Write-Warning "Script di copia DLL non trovato: $copyScript"
    Write-Host "OFF-player build completata (DLL non sincronizzate)." -ForegroundColor Yellow
}

# Optional: bump `OFF-player/bin/data/VERSION` after successful build
if ($BumpVersion -or $Version) {
    $verToWrite = $Version
    if (-not $verToWrite -and $BumpVersion) {
        # prefer headless version file if present
        $headlessVerFile = Join-Path $repoRoot 'headless-player\VERSION'
        if (Test-Path $headlessVerFile) {
            try { $verToWrite = (Get-Content -LiteralPath $headlessVerFile -Raw).Trim() } catch { $verToWrite = $null }
        }
    }
    if ($verToWrite) {
        $outVerFile = Join-Path $repoRoot 'OFF-player\bin\data\VERSION'
        $outDir = Split-Path -Parent $outVerFile
        if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
        try {
            Set-Content -LiteralPath $outVerFile -Value $verToWrite -Encoding ascii -NoNewline
            Write-Host "[VERSION] Wrote OFF-player/bin/data/VERSION => $verToWrite" -ForegroundColor Green
        } catch {
            Write-Warning "[VERSION] Impossibile scrivere OFF-player/bin/data/VERSION: $($_.Exception.Message)"
        }
    } else {
        Write-Warning "[VERSION] Bump version richiesto ma nessun valore determinato: HEADLESS_VERSION/headless-player/VERSION non trovato e -Version non passato"
    }
}
