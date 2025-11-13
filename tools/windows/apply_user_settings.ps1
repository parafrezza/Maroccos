<#
    apply_user_settings.ps1 — Applica impostazioni desktop per l'utente corrente
    
    Questo script viene eseguito automaticamente al primo login per configurare:
    - Sfondo nero
    - Icone desktop nascoste
    - Tema scuro
    - No animazioni
    
    Può essere eseguito anche manualmente per ripristinare le impostazioni.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

function Write-Info([string]$Message) {
    Write-Host "[INFO] $Message" -ForegroundColor Gray
}

function Write-Ok([string]$Message) {
    Write-Host "[OK]  $Message" -ForegroundColor Green
}

Write-Info "Applicazione impostazioni desktop per utente: $env:USERNAME"

# Sfondo nero (SOLID color)
try {
    New-Item -Path 'HKCU:\\Control Panel\\Desktop' -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop' -Name 'Wallpaper' -Value '' -ErrorAction SilentlyContinue
    Set-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop' -Name 'WallpaperStyle' -Value '0' -ErrorAction SilentlyContinue
    Set-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop' -Name 'TileWallpaper' -Value '0' -ErrorAction SilentlyContinue
    New-Item -Path 'HKCU:\\Control Panel\\Colors' -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path 'HKCU:\\Control Panel\\Colors' -Name 'Background' -Value '0 0 0' -ErrorAction SilentlyContinue
    New-Item -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Wallpapers' -Force -ErrorAction SilentlyContinue | Out-Null
    try { Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Wallpapers' -Name 'BackgroundType' -Value 1 -ErrorAction SilentlyContinue } catch {}
    try { Remove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Wallpapers' -Name 'WallpaperPath' -ErrorAction SilentlyContinue } catch {}
    try {
        $themesDir = Join-Path $Env:APPDATA 'Microsoft\\Windows\\Themes'
        $transcoded = Join-Path $themesDir 'TranscodedWallpaper'
        if (Test-Path $transcoded) { Remove-Item -Path $transcoded -Force -ErrorAction SilentlyContinue }
    } catch {}
    RUNDLL32.EXE user32.dll, UpdatePerUserSystemParameters ,1 ,True
    Write-Ok "Sfondo nero SOLID impostato"
} catch {
    Write-Warning "Impossibile impostare sfondo SOLID nero: $($_.Exception.Message)"
}

# Sfondo nero (fallback BMP)
try {
    $wallDir = "$Env:ProgramData\player_provision"
    $bmp = Join-Path $wallDir 'black.bmp'
    if ($false -and Test-Path $bmp) {
        Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'Wallpaper' -Value $bmp -ErrorAction Stop
        RUNDLL32.EXE user32.dll, UpdatePerUserSystemParameters ,1 ,True
        Write-Ok "Sfondo nero impostato"
    } else {
        Write-Info "File sfondo nero non trovato: $bmp (verrà creato al prossimo provisioning)"
    }
} catch {
    Write-Warning "Impossibile impostare sfondo nero: $($_.Exception.Message)"
}

# Nascondi icone desktop
try {
    New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name 'HideIcons' -Value 1 -ErrorAction Stop
    Write-Ok "Icone desktop nascoste"
} catch {
    Write-Warning "Impossibile nascondere icone desktop: $($_.Exception.Message)"
}

# Disabilita animazioni
try {
    New-Item -Path 'HKCU:\Control Panel\Accessibility' -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path 'HKCU:\Control Panel\Accessibility' -Name 'Animation' -Value 0 -ErrorAction Stop
    Write-Ok "Animazioni disabilitate"
} catch {
    Write-Warning "Impossibile disabilitare animazioni: $($_.Exception.Message)"
}

# Disabilita trasparenze
try {
    New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name 'EnableTransparency' -Value 0 -ErrorAction Stop
    Write-Ok "Trasparenze disabilitate"
} catch {
    Write-Warning "Impossibile disabilitare trasparenze: $($_.Exception.Message)"
}

# Tema scuro (opzionale)
try {
    Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name 'AppsUseLightTheme' -Value 0 -ErrorAction SilentlyContinue
    Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name 'SystemUsesLightTheme' -Value 0 -ErrorAction SilentlyContinue
    Write-Ok "Tema scuro attivato"
} catch {}

# Pulisci desktop utente
try {
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    if (Test-Path $desktopPath) {
        Get-ChildItem $desktopPath -Force -ErrorAction SilentlyContinue | 
            Where-Object { $_.Name -notlike '*marocco*' -and $_.Name -notlike '*player*' } | 
            ForEach-Object {
                try { Remove-Item $_.FullName -Force -Recurse -ErrorAction SilentlyContinue } catch {}
            }
        Write-Ok "Desktop pulito"
    }
} catch {}

# Riavvia Explorer per applicare le modifiche
try {
    Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-Process explorer.exe
    Write-Ok "Explorer riavviato per applicare le modifiche"
} catch {
    Write-Info "Riavvia Explorer manualmente o riavvia il sistema per vedere le modifiche"
}

Write-Ok "Impostazioni applicate con successo per $env:USERNAME"
