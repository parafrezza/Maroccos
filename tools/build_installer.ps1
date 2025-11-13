# Builds the Windows installer using Inno Setup (ISCC)
# Robustly locates ISCC and invokes it on installer/off-player.iss
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_installer.ps1

param(
    [string]$IssPath,
    [switch]$Debug,
    [string]$Version,
    [string]$AppBaseName = 'marocco-player'
)

$ErrorActionPreference = 'Stop'

if ($Debug) {
    throw "L'opzione -Debug non è più supportata: OFF-player viene distribuito solo in Release."
}

function Resolve-InstallerScriptPath {
    param([string]$Path)
    if ($Path -and (Test-Path -LiteralPath $Path)) { return (Resolve-Path -LiteralPath $Path).Path }
    $root = Split-Path -Parent $PSScriptRoot
    $defaultIss = Join-Path $root 'installer\off-player.iss'
    if (Test-Path -LiteralPath $defaultIss) { return (Resolve-Path -LiteralPath $defaultIss).Path }
    throw "File ISS non trovato: $defaultIss"
}

function Find-ISCC {
    # 1) Environment variable ISCC (file path or directory)
    if ($env:ISCC) {
        $p = $env:ISCC
        if (Test-Path -LiteralPath $p) {
            $item = Get-Item -LiteralPath $p
            if ($item.PSIsContainer) {
                $exe = Join-Path $item.FullName 'ISCC.exe'
                if (Test-Path -LiteralPath $exe) { return (Resolve-Path -LiteralPath $exe).Path }
            } else {
                return (Resolve-Path -LiteralPath $item.FullName).Path
            }
        }
    }
    # 2) User install path
    $userPath = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    if (Test-Path -LiteralPath $userPath) { return (Resolve-Path -LiteralPath $userPath).Path }
    # 3) System install paths
    $sys64 = 'C:\Program Files\Inno Setup 6\ISCC.exe'
    if (Test-Path -LiteralPath $sys64) { return (Resolve-Path -LiteralPath $sys64).Path }
    $sys86 = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
    if (Test-Path -LiteralPath $sys86) { return (Resolve-Path -LiteralPath $sys86).Path }
    return $null
}

function Update-AssetsManifest {
    param([string]$AssetsDir)

    if (-not (Test-Path -LiteralPath $AssetsDir)) {
        return
    }

    $resolved = (Resolve-Path -LiteralPath $AssetsDir).Path
    if (-not ($resolved.EndsWith('\') -or $resolved.EndsWith('/'))) {
        $resolved = $resolved + '\'
    }

    $files = Get-ChildItem -LiteralPath $resolved -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne 'asset_manifest.json' }

    $entries = @()
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($resolved.Length)
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        $entries += [ordered]@{
            path = $relative
            size = $file.Length
            lastWriteTime = $file.LastWriteTimeUtc.ToString('u')
            sha256 = $hash
        }
    }

    $manifest = [ordered]@{
        generatedAtUtc = (Get-Date).ToUniversalTime().ToString('u')
        totalFiles = $entries.Count
        files = $entries
    }

    $manifestPath = Join-Path $AssetsDir 'asset_manifest.json'
    $json = $manifest | ConvertTo-Json -Depth 5
    Set-Content -LiteralPath $manifestPath -Value $json -Encoding utf8
    Write-Host ("Manifest asset aggiornato: {0} file" -f $entries.Count) -ForegroundColor DarkCyan
}

function Invoke-SetResolutionAsset {
    param(
        [string]$Root,
        [string]$AssetsDir
    )

    $proj = Join-Path $Root 'tools\windows\SetResolution\SetResolution.csproj'
    if (-not (Test-Path -LiteralPath $proj)) {
        Write-Warning "SetResolution.csproj non trovato: salto build dell'utility SetResolution"
        return
    }
    $dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
    if (-not $dotnet) {
        throw "dotnet CLI non trovato nel PATH: installa .NET SDK per compilare SetResolution"
    }

    $publishTemp = Join-Path (Split-Path -Parent $proj) 'publish_installer'
    if (Test-Path -LiteralPath $publishTemp) {
        Remove-Item -LiteralPath $publishTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $publishTemp -Force | Out-Null

    Write-Host "[BUILD] SetResolution (dotnet publish)" -ForegroundColor Green
    # Publish SetResolution utility as a self-contained single-file binary so it can run without preinstalled .NET runtimes
    $publishArgs = @(
        'publish', $proj,
        '-c', 'Release',
        '-r', 'win-x64',
        '--self-contained', 'true',
        '-p:PublishSingleFile=true',
        '-p:PublishTrimmed=false',
        '-p:IncludeNativeLibrariesForSelfExtract=true',
        '-o', $publishTemp
    )
    & $dotnet.Source @publishArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "dotnet publish per SetResolution è fallito ($LASTEXITCODE)"
    }

    if (-not (Test-Path -LiteralPath $AssetsDir)) {
        New-Item -ItemType Directory -Path $AssetsDir -Force | Out-Null
    }

    $dest = Join-Path $AssetsDir 'SetResolution'
    if (-not (Test-Path -LiteralPath $dest)) {
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
    }

    $robocopy = Get-Command robocopy.exe -ErrorAction SilentlyContinue
    if (-not $robocopy) {
        Write-Warning "robocopy non disponibile, uso Copy-Item (copia completa)"
        Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        Copy-Item -Path (Join-Path $publishTemp '*') -Destination $dest -Recurse -Force
    } else {
        $robocopyArgs = @(
            $publishTemp,
            $dest,
            '/MIR',
            '/R:1',
            '/W:1',
            '/NFL',
            '/NDL',
            '/NJH',
            '/NJS',
            '/NP'
        )
        & $robocopy.Source @robocopyArgs | Out-Null
        $rc = $LASTEXITCODE
        if ($rc -ge 8) {
            throw "robocopy ha restituito codice $rc durante la sincronizzazione di SetResolution"
        }
        if ($rc -eq 0) {
            Write-Host "[BUILD] SetResolution nessun aggiornamento necessario" -ForegroundColor DarkGray
        } else {
            Write-Host "[BUILD] SetResolution aggiornato (robocopy exit $rc)" -ForegroundColor Green
        }
        $global:LASTEXITCODE = 0
    }

    # Pulizia temporanei
    Remove-Item -LiteralPath $publishTemp -Recurse -Force -ErrorAction SilentlyContinue
}

try {
    $iss = Resolve-InstallerScriptPath -Path $IssPath
    Write-Host "Script Inno Setup:" $iss

    # Validazione: AppId nel file ISS deve usare la notazione {{GUID}}
    try {
        $issText = Get-Content -LiteralPath $iss -Raw -Encoding UTF8
    } catch {
        Write-Error "Impossibile leggere il file ISS per validazione AppId: $($_.Exception.Message)"
        exit 1
    }
    $issLines = $issText -split "`r?`n"
    $appIdLines = @()
    foreach ($ln in $issLines) {
        # ignora righe commentate con ';'
        if ($ln -match '^\s*;') { continue }
        if ($ln -match '^\s*AppId\s*=') { $appIdLines += $ln }
    }
    if ($appIdLines.Count -eq 0) {
        Write-Error "Validazione Inno: AppId mancante nella sezione [Setup] di $iss"
        exit 2
    }
    if ($appIdLines.Count -gt 1) {
        Write-Error "Validazione Inno: più righe AppId trovate in $iss. Mantieni una sola direttiva AppId."
        $appIdLines | ForEach-Object { Write-Host "  -> $_" -ForegroundColor DarkYellow }
        exit 2
    }
    $appIdLine = $appIdLines[0]
    $guidPattern = '^\s*AppId\s*=\s*\{\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}\}\s*$'
    if (-not ($appIdLine -match $guidPattern)) {
        Write-Error "Validazione Inno: AppId non valido. Usa la forma AppId={{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}}"
        Write-Host ("Riga trovata: '{0}'" -f $appIdLine) -ForegroundColor Yellow
        exit 2
    }

    $iscc = Find-ISCC
    if (-not $iscc) {
        Write-Error 'ISCC.exe non trovato. Installa Inno Setup 6 o imposta la variabile di ambiente ISCC.'
        exit 1
    }

    Write-Host "ISCC trovato:" $iscc

    # Determina versione da file se non passata
    if (-not $Version) {
        $root = Split-Path -Parent $PSScriptRoot
        $verFile = Join-Path $root 'headless-player\VERSION'
        if (Test-Path -LiteralPath $verFile) {
            $Version = (Get-Content -LiteralPath $verFile -Raw).Trim()
        } else {
            Write-Warning "VERSION non trovato, uso 0.0.0"
            $Version = '0.0.0'
        }
    }

    $mode = 'Release'
    $appName = $AppBaseName
    $desktopLink = "${appName}_v$Version"
    $headlessDir = 'headless-player'
    $headlessExe = 'headless-player.exe'
    $outBase = $desktopLink

    Write-Host "Costruzione installer: AppName=$appName Version=$Version Mode=$mode" -ForegroundColor Cyan

    $defines = @(
        "/DAppName=$appName",
        "/DAppVersion=$Version",
    "/DBuildMode=$mode",
        "/DOutputBaseFilename=$outBase",
        "/DDesktopLinkName=$desktopLink",
        "/DHeadlessDirName=$headlessDir",
        "/DHeadlessExeName=$headlessExe"
    )
    # Passa percorso icona se disponibile
    $icoPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'icon-maker\dist\morocco-player\morocco-player.ico'
    if (Test-Path -LiteralPath $icoPath) {
        $icoFull = (Resolve-Path -LiteralPath $icoPath).Path
        $defines += "/DIcoSrcPath=$($('"' + $icoFull + '"'))"
    } else {
        Write-Warning "Icona ICO non trovata: $icoPath (l'installer userà le icone di default)"
    }
    $assetsDir = Join-Path (Split-Path -Parent $iss) 'assets'
    Invoke-SetResolutionAsset -Root (Split-Path -Parent $PSScriptRoot) -AssetsDir $assetsDir

    # Verifica presenza K-Lite (nome flessibile con/senza trattino)
    $kliteVariants = @(
        'KLite_Codec_Pack_Basic.exe',
        'K-Lite_Codec_Pack_Basic.exe',
        'klite_codec_pack_basic.exe'
    )
    $klite = $null
    foreach ($name in $kliteVariants) {
        $candidate = Join-Path $assetsDir $name
        if (Test-Path -LiteralPath $candidate) {
            $klite = $candidate
            break
        }
    }
    if (-not $klite) {
        Write-Error "K-Lite Codec Pack Basic non trovato in 'installer\assets'."
        Write-Error "Scarica il setup da https://codecguide.com/download_k-lite_codec_pack_basic.htm"
        Write-Error "e salvalo come 'installer\assets\KLite_Codec_Pack_Basic.exe' (o K-Lite_Codec_Pack_Basic.exe)."
        exit 1
    }
    Write-Host "K-Lite trovato: $(Split-Path -Leaf $klite)" -ForegroundColor Green
    # Passa il path K-Lite a Inno Setup
    $kliteFull = (Resolve-Path -LiteralPath $klite).Path
    $defines += "/DKLitePath=$($('"' + $kliteFull + '"'))"

    Update-AssetsManifest -AssetsDir $assetsDir

    # Abilita log dettagliato di ISCC per diagnosi problemi (es. errori di compressione intermittenti)
    $logsDir = Join-Path (Split-Path -Parent $iss) 'logs'
    if (-not (Test-Path -LiteralPath $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
    $logPath = Join-Path $logsDir ("iscc-" + $Version + "-" + $ts + ".log")

    # Nota: ISCC non supporta /LOG (solo /LOG=filename nelle versioni più vecchie?); facciamo redirect dell'output
    $isccCmd = @($iscc, $iss) + $defines
    Write-Host "Invocazione ISCC: $($isccCmd -join ' ')" -ForegroundColor DarkGray
    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $iscc
    $processInfo.Arguments = ($isccCmd[1..($isccCmd.Count-1)] -join ' ')
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($processInfo)
    $stdOut = $proc.StandardOutput.ReadToEnd()
    $stdErr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    Set-Content -LiteralPath $logPath -Value ($stdOut + "`n" + $stdErr) -Encoding utf8
    Write-Host "Log ISCC salvato: $logPath" -ForegroundColor DarkCyan
    # Echo breve riassunto
    ($stdOut.Split("`n") | Select-Object -First 12) | ForEach-Object { Write-Host $_ }
    if ($stdErr) { Write-Warning "ISCC stderr non vuoto (verificare log)" }
    $global:LASTEXITCODE = $proc.ExitCode
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Error ("ISCC ha restituito codice {0}" -f $code)
        if (Test-Path -LiteralPath $logPath) {
            Write-Host "--- Ultime 60 righe del log ISCC ($logPath) ---" -ForegroundColor Yellow
            Get-Content -LiteralPath $logPath -Tail 60 | ForEach-Object { Write-Host $_ }
            Write-Host "--- Fine log ---" -ForegroundColor Yellow
        }
        exit $code
    }
    Write-Host "Installer creato con successo."
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
