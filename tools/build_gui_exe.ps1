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

$settingsFullPath = if ([System.IO.Path]::IsPathRooted($Settings)) {
    [System.IO.Path]::GetFullPath($Settings)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $Root $Settings))
}
$defaultSettingsPath = [System.IO.Path]::GetFullPath((Join-Path $Root "GUI/settings.json"))
$settingsTemplatePath = Join-Path $Root "GUI/settings.sample.json"

$headlessVersionPath = Join-Path $Root 'headless-player\VERSION'
$rawVersionTag = $null
if (Test-Path -LiteralPath $headlessVersionPath) {
    try {
        $rawVersionTag = (Get-Content -LiteralPath $headlessVersionPath -Raw).Trim()
    } catch {
        $rawVersionTag = $null
    }
}
if (-not $rawVersionTag) {
    $rawVersionTag = 'v0.1.0'
    Write-Warning "Headless VERSION non trovato o vuoto: uso $rawVersionTag" 
}

if ($rawVersionTag -match '^[vV](.+)$') {
    $guiVersionValue = $Matches[1]
} else {
    $guiVersionValue = $rawVersionTag
}

$guiVersionFile = Join-Path $Root 'GUI\VERSION'
$existingGuiVersion = $null
if (Test-Path -LiteralPath $guiVersionFile) {
    try {
        $existingGuiVersion = (Get-Content -LiteralPath $guiVersionFile -Raw).Trim()
    } catch {
        $existingGuiVersion = $null
    }
}

if ($existingGuiVersion -ne $guiVersionValue) {
    Set-Content -LiteralPath $guiVersionFile -Value $guiVersionValue -Encoding utf8
    Write-Host "GUI/VERSION aggiornato a $guiVersionValue" -ForegroundColor Cyan
} else {
    Write-Host "GUI/VERSION già impostato a $guiVersionValue" -ForegroundColor DarkGray
}

if (-not (Test-Path -LiteralPath $settingsFullPath)) {
    if ([string]::Equals($settingsFullPath, $defaultSettingsPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $settingsTemplatePath) {
            Copy-Item -LiteralPath $settingsTemplatePath -Destination $settingsFullPath -Force
            Write-Host "Created default GUI settings from template ($settingsTemplatePath)." -ForegroundColor Cyan
        } else {
            $settingsDir = Split-Path -Parent $settingsFullPath
            if (-not (Test-Path -LiteralPath $settingsDir)) {
                New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
            }
            $defaultSettings = @'
{
  "network": {
    "player_port": 8080,
    "ping_interval": 10.0,
    "ping_ms": 200,
    "status_poll_ms": 2000
  },
  "media": {
    "server_port": 9000
  },
  "update": {
    "auto_build": true,
    "serve_port": 8000
  },
  "remote": {
    "vnc_password": "extra",
    "vnc_port": 5900,
    "vnc_extra_args": []
  },
  "startup": {
    "known_macs": [],
    "broadcast": "255.255.255.255",
    "port": 9
  }
}
'@
            Set-Content -LiteralPath $settingsFullPath -Value $defaultSettings -Encoding utf8
            Write-Host "Created fallback GUI settings file at $settingsFullPath." -ForegroundColor Cyan
        }
    } else {
        throw "Impossibile trovare il file di settings richiesto: $settingsFullPath"
    }
}

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
    $settingsPath = (Resolve-Path -LiteralPath $settingsFullPath).Path
    $viewerPath = (Resolve-Path $Viewer).Path
    $versionDataPath = $null
    if (Test-Path -LiteralPath $guiVersionFile) {
        $versionDataPath = (Resolve-Path -LiteralPath $guiVersionFile).Path
    }
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
    if ($versionDataPath) {
        $args += "--add-data"
        $args += "$versionDataPath$sep."
    }

    Write-Host "Running PyInstaller..." -ForegroundColor Cyan
    & $python -m PyInstaller @args

    $distDir = Join-Path $Root "dist"
    $exePath = Join-Path $distDir ("{0}.exe" -f $Name)
    if (Test-Path -LiteralPath $exePath) {
        if ($rawVersionTag) {
            $safeVersion = $rawVersionTag -replace '[^0-9A-Za-z_.-]', '_'
            $versionedName = "{0}_{1}.exe" -f $Name, $safeVersion
            $versionedPath = Join-Path $distDir $versionedName
            Copy-Item -LiteralPath $exePath -Destination $versionedPath -Force
            Write-Host "Created versioned copy: $versionedName" -ForegroundColor DarkCyan
        } else {
            Write-Warning "Version tag non disponibile: copia versionata saltata"
        }
    } else {
        Write-Warning "GUI executable non trovato: $exePath"
    }
}
finally {
    Pop-Location
}
