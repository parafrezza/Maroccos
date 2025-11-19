# Builds the Windows installer using Inno Setup (ISCC)
# Robustly locates ISCC and invokes it on installer/off-player.iss
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_installer.ps1

param(
    [string]$IssPath,
    [switch]$Debug,
    [string]$Version,
    [string]$AppBaseName = 'marocco-player',
    [switch]$SilentOnly
)

$ErrorActionPreference = 'Stop'

function Parse-VersionString {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Stringa versione non valida (vuota)."
    }

    $trimmed = $Value.Trim()
    $prefix = ''
    if ($trimmed.StartsWith('v', [System.StringComparison]::OrdinalIgnoreCase)) {
        $prefix = 'v'
        $trimmed = $trimmed.Substring(1)
    }

    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        throw "Stringa versione non valida dopo il prefisso: '$Value'"
    }

    return [pscustomobject]@{
        Prefix = $prefix
        Core   = $trimmed
        Raw    = $Value
    }
}

function Increment-VersionCore {
    param([string]$Core)

    if ([string]::IsNullOrWhiteSpace($Core)) {
        throw "Versione di origine vuota: impossibile incrementare."
    }

    $parts = $Core.Split('.')
    if ($parts.Count -lt 1) { throw "Versione non valida: '$Core'" }

    for ($i = 0; $i -lt $parts.Count; $i++) {
        if ($parts[$i] -notmatch '^[0-9]+$') {
            throw "Componente versione non numerico: '$($parts[$i])' in '$Core'"
        }
    }

    while ($parts.Count -lt 3) {
        $parts += '0'
    }

    $lastIndex = $parts.Count - 1
    $parts[$lastIndex] = ([int]$parts[$lastIndex] + 1).ToString()

    return ($parts -join '.')
}

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

function Ensure-HeadlessBinary {
    param(
        [string]$ExpectedVersion
    )

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $distDir = Join-Path $repoRoot 'headless-player\dist'
    $bundleDir = Join-Path $distDir 'headless-player'
    $exePath = Join-Path $bundleDir 'headless-player.exe'

    $needsBuild = $false
    if (-not (Test-Path -LiteralPath $exePath)) {
        $needsBuild = $true
        Write-Host 'headless-player.exe mancante: genero la build Windows' -ForegroundColor Yellow
    } elseif ($ExpectedVersion) {
        $embeddedVersionPath = Join-Path $bundleDir '_internal\VERSION'
        $currentVersion = $null
        if (Test-Path -LiteralPath $embeddedVersionPath) {
            try {
                $currentVersion = (Get-Content -LiteralPath $embeddedVersionPath -Raw).Trim()
            } catch {
                $currentVersion = $null
            }
        }

        if (-not $currentVersion) {
            Write-Warning 'Impossibile determinare la versione incorporata nel player headless: rigenero eseguibile'
            $needsBuild = $true
        } elseif ($currentVersion -ne $ExpectedVersion) {
            Write-Host (
                "Versione headless corrente ({0}) diversa da quella attesa ({1}): rigenero eseguibile" -f `
                    $currentVersion, $ExpectedVersion
            ) -ForegroundColor Yellow
            $needsBuild = $true
        } else {
            Write-Host (
                "headless-player.exe trovato ({0}) con versione {1}" -f $exePath, $ExpectedVersion
            ) -ForegroundColor DarkGray
        }
    } else {
        Write-Host ("headless-player.exe trovato: {0}" -f $exePath) -ForegroundColor DarkGray
    }

    if (-not $needsBuild) {
        return
    }

    $buildScript = Join-Path $PSScriptRoot 'build_headless_windows.ps1'
    if (-not (Test-Path -LiteralPath $buildScript)) {
        throw "build_headless_windows.ps1 non trovato: $buildScript"
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildScript
    if ($LASTEXITCODE -ne 0) {
        throw "build_headless_windows.ps1 fallito ($LASTEXITCODE)"
    }

    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "headless-player.exe non trovato dopo build_headless_windows.ps1"
    }

    $postVersion = $null
    if ($ExpectedVersion) {
        $embeddedVersionPath = Join-Path $bundleDir '_internal\VERSION'
        if (Test-Path -LiteralPath $embeddedVersionPath) {
            try {
                $postVersion = (Get-Content -LiteralPath $embeddedVersionPath -Raw).Trim()
            } catch {
                $postVersion = $null
            }
        }
    }

    Write-Host ("headless-player.exe creato: {0}" -f $exePath) -ForegroundColor Green
    if ($ExpectedVersion -and $postVersion -and $postVersion -ne $ExpectedVersion) {
        Write-Warning (
            "La versione incorporata nel player headless ({0}) non corrisponde a {1}" -f `
                $postVersion, $ExpectedVersion
        )
    }
}

function Invoke-IsccBuild {
    param(
        [string]$IsccPath,
        [string]$IssPath,
        [string[]]$Defines,
        [string]$LogsDir,
        [string]$Version,
        [string]$OutputBaseFilename,
        [string]$RepoRoot,
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $LogsDir)) {
        New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
    }

    $labelSafe = if ([string]::IsNullOrWhiteSpace($Label)) { 'interactive' } else { ($Label -replace '[^A-Za-z0-9_-]', '_') }
    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
    $logPath = Join-Path $LogsDir ("iscc-$Version-$labelSafe-$ts.log")

    $isccArgs = @($IssPath) + $Defines
    Write-Host ("Invocazione ISCC [$labelSafe]: $IsccPath $($isccArgs -join ' ')") -ForegroundColor DarkGray

    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $IsccPath
    $processInfo.Arguments = ($isccArgs -join ' ')
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true

    $proc = [System.Diagnostics.Process]::Start($processInfo)
    $stdOut = $proc.StandardOutput.ReadToEnd()
    $stdErr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    Set-Content -LiteralPath $logPath -Value ($stdOut + "`n" + $stdErr) -Encoding utf8
    Write-Host ("Log ISCC [$labelSafe] salvato: $logPath") -ForegroundColor DarkCyan

    ($stdOut.Split("`n") | Where-Object { $_ } | Select-Object -First 12) | ForEach-Object { Write-Host $_ }
    if ($stdErr) { Write-Warning "ISCC stderr non vuoto (verificare log)" }

    $global:LASTEXITCODE = $proc.ExitCode
    $code = $proc.ExitCode
    if ($code -ne 0) {
        Write-Error ("ISCC ha restituito codice {0} per la build {1}" -f $code, $labelSafe)
        if (Test-Path -LiteralPath $logPath) {
            Write-Host ("--- Ultime 60 righe del log ISCC [$labelSafe] ---") -ForegroundColor Yellow
            Get-Content -LiteralPath $logPath -Tail 60 | ForEach-Object { Write-Host $_ }
            Write-Host "--- Fine log ---" -ForegroundColor Yellow
        }
        throw "Build Inno Setup fallita ($labelSafe)"
    }

    $installerDir = Join-Path (Split-Path -Parent $IssPath) 'dist'
    $builtInstaller = Join-Path $installerDir ($OutputBaseFilename + '.exe')
    if (Test-Path -LiteralPath $builtInstaller) {
        $rootDist = Join-Path $RepoRoot 'dist'
        if (-not (Test-Path -LiteralPath $rootDist)) {
            New-Item -ItemType Directory -Path $rootDist -Force | Out-Null
        }
        $destInstaller = Join-Path $rootDist (Split-Path -Leaf $builtInstaller)
        Copy-Item -LiteralPath $builtInstaller -Destination $destInstaller -Force
        Write-Host ("Installer [$labelSafe] copiato in {0}" -f $destInstaller) -ForegroundColor Green
    } else {
        Write-Warning ("Impossibile trovare l'eseguibile Inno atteso ({0}) per la build {1}" -f $builtInstaller, $labelSafe)
    }

    return [pscustomobject]@{
        Label = $labelSafe
        LogPath = $logPath
        OutputPath = if (Test-Path -LiteralPath $builtInstaller) { Join-Path (Join-Path $RepoRoot 'dist') (Split-Path -Leaf $builtInstaller) } else { $null }
    }
}

$versionFileExisted = $false
$originalVersionContent = $null
$versionFileUpdated = $false
$versionFilePath = $null
$versionDisplay = $null

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

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $verFile = Join-Path $repoRoot 'headless-player\VERSION'
    $versionFilePath = $verFile

    $existingRaw = $null
    $existingParsed = $null
    if (Test-Path -LiteralPath $verFile) {
        $existingRaw = (Get-Content -LiteralPath $verFile -Raw).Trim()
        $versionFileExisted = $true
        $originalVersionContent = $existingRaw
        if ($existingRaw) {
            try {
                $existingParsed = Parse-VersionString $existingRaw
            } catch {
                Write-Warning "Contenuto VERSION non riconosciuto ('${existingRaw}'): verrà sovrascritto"
                $existingParsed = $null
            }
        }
    }

    if (-not $Version) {
        if (-not $existingRaw) {
            Write-Warning "VERSION non trovato, inizializzo da 0.0.0"
            $existingRaw = '0.0.0'
            $existingParsed = Parse-VersionString $existingRaw
        } elseif (-not $existingParsed) {
            $existingParsed = Parse-VersionString $existingRaw
        }

        $newCore = Increment-VersionCore $existingParsed.Core
        $prefixForFile = if ($existingParsed.Prefix) { $existingParsed.Prefix } else { 'v' }
        $oldDisplay = if ($existingParsed.Prefix) { $existingParsed.Prefix + $existingParsed.Core } else { $existingParsed.Core }
        $versionDisplay = if ($prefixForFile) { $prefixForFile + $newCore } else { $newCore }
        Write-Host ("Versione incrementata automaticamente: {0} -> {1}" -f $oldDisplay, $versionDisplay) -ForegroundColor Cyan
        $Version = $newCore
    } else {
        $parsedParam = Parse-VersionString $Version
        $Version = $parsedParam.Core
        if (-not $existingParsed -and $existingRaw) {
            try {
                $existingParsed = Parse-VersionString $existingRaw
            } catch {
                $existingParsed = $null
            }
        }
        $prefixForFile = if ($parsedParam.Prefix) { $parsedParam.Prefix } elseif ($existingParsed -and $existingParsed.Prefix) { $existingParsed.Prefix } else { 'v' }
        $versionDisplay = if ($prefixForFile) { $prefixForFile + $Version } else { $Version }
    }

    if (-not $versionDisplay) {
        $versionDisplay = "v$Version"
    }

    $mode = 'Release'
    $appName = $AppBaseName
    $versionTag = $versionDisplay
    $desktopLink = "${appName}_${versionTag}"
    $headlessDir = 'headless-player'
    $headlessExe = 'headless-player.exe'
    $outBase = "${appName}-installer_${versionTag}"

    Write-Host "Costruzione installer: AppName=$appName Version=$versionTag (core=$Version) Mode=$mode" -ForegroundColor Cyan

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

    if (-not (Test-Path -LiteralPath (Split-Path -Parent $verFile))) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $verFile) -Force | Out-Null
    }
    if ($existingRaw -ne $versionDisplay) {
        Set-Content -LiteralPath $verFile -Value $versionDisplay -Encoding ascii
        $versionFileUpdated = $true
        Write-Host ("File VERSION impostato a {0}" -f $versionDisplay) -ForegroundColor Green
    } else {
        Write-Host ("File VERSION già impostato a {0}" -f $versionDisplay) -ForegroundColor DarkGray
    }

    Ensure-HeadlessBinary -ExpectedVersion $versionDisplay

    Update-AssetsManifest -AssetsDir $assetsDir

    $logsDir = Join-Path (Split-Path -Parent $iss) 'logs'

    $results = @()
    if (-not $SilentOnly) {
        try {
            $results += Invoke-IsccBuild -IsccPath $iscc -IssPath $iss -Defines $defines -LogsDir $logsDir -Version $Version -OutputBaseFilename $outBase -RepoRoot $repoRoot -Label 'interactive'
        } catch {
            Write-Warning "Build interattiva fallita: $_"
        }
    }

    $outBaseSilent = "${appName}-installer_${versionTag}_auto"
    $silentDefines = $defines.Clone()
    for ($i = 0; $i -lt $silentDefines.Length; $i++) {
        if ($silentDefines[$i] -like '/DOutputBaseFilename=*') {
            $silentDefines[$i] = "/DOutputBaseFilename=$outBaseSilent"
        }
    }
    $silentDefines += '/DSilentInstall=1'
    $results += Invoke-IsccBuild -IsccPath $iscc -IssPath $iss -Defines $silentDefines -LogsDir $logsDir -Version $Version -OutputBaseFilename $outBaseSilent -RepoRoot $repoRoot -Label 'auto'

    if ($SilentOnly) {
        Write-Host "Installer automatico creato con successo." -ForegroundColor Green
    } else {
        Write-Host "Installer interattivo e automatico creati con successo." -ForegroundColor Green
    }
    $results | ForEach-Object {
        if ($_.OutputPath) {
            Write-Host (" - [{0}] {1}" -f $_.Label, $_.OutputPath) -ForegroundColor Cyan
        }
    }
    exit 0
}
catch {
    if ($versionFileUpdated) {
        try {
            if ($versionFileExisted) {
                $restoreValue = if ($null -eq $originalVersionContent) { '' } else { $originalVersionContent }
                Set-Content -LiteralPath $versionFilePath -Value $restoreValue -Encoding ascii
                Write-Warning "Ripristinato headless-player/VERSION al contenuto precedente"
            } elseif ($versionFilePath -and (Test-Path -LiteralPath $versionFilePath)) {
                Remove-Item -LiteralPath $versionFilePath -Force
                Write-Warning "Rimosso headless-player/VERSION creato durante la build fallita"
            }
        } catch {
            Write-Warning "Impossibile ripristinare headless-player/VERSION: $($_.Exception.Message)"
        }
    }
    Write-Error $_
    exit 1
}
