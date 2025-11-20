<#
    provision_player.ps1 — Provisioning Windows 11 per "Player"

    Scopo:
    - Disabilitare servizi/feature che consumano risorse (update, ads, indicizzazione, telemetria, animazioni).
    - Impostare Ethernet con IP statico 192.168.10.220/24 (senza gateway per reti chiuse), mantenendo il Wi‑Fi in DHCP.
    - Installare TigerVNC (opzionale, con prompt) per controllo remoto.
    - Impostare tema minimale (sfondo nero, desktop pulito) e piano di alimentazione ad alte prestazioni.
    - Garantire account locale "extra" con password "extra" nel gruppo Administrators.

    Uso:
    - Eseguire come Amministratore.
    - Lo script è verboso: spiega ogni passo e chiede conferme per azioni potenzialmente invasive.

    Note importanti:
    - Impostare lo stesso IP 192.168.10.220 su più dispositivi è corretto SOLO se le reti sono isolate punto‑punto
      (p.es. ogni player collegato ad un proprio ricevitore 433 MHz via ethernet dedicata). In LAN condivise causerebbe conflitti.
    - Disabilitare gli aggiornamenti Windows limita l'applicazione di patch di sicurezza. Prevedere un ciclo di manutenzione offline.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$NonInteractive,
    [string]$EthernetAlias,
    [string]$StaticIP = '192.168.8.221',
    [int]$PrefixLength = 24,
    [string]$Gateway = '192.168.8.1',
    [string[]]$DnsServers = @('8.8.8.8','1.1.1.1'),
    [switch]$SkipTigerVNC,
    [string]$TigerVNCPassword = 'extra',
    [string]$ScheduledTaskName,
    [string]$HeadlessTaskName = 'MaroccosHeadless',
    [string]$HeadlessTaskUser = 'extra',
    [string]$InstallRoot = 'C:\Program Files\marocco-player',
    [switch]$SkipMediaShare,
    [switch]$SkipHostnameSync
)

$ErrorActionPreference = 'Stop'
$script:RebootRequired = $false
$script:ScriptPath = $MyInvocation.MyCommand.Path
$script:ScriptPathLog = if ([string]::IsNullOrWhiteSpace($script:ScriptPath)) { '<unknown>' } else { $script:ScriptPath }
$script:ScriptDirectory = if ([string]::IsNullOrWhiteSpace($script:ScriptPath)) { $null } else { Split-Path -Parent $script:ScriptPath }

# Logging helpers
if ($script:ScriptDirectory) {
    $LogDir = $script:ScriptDirectory
    $script:LogAclRequiresGrant = $false
} else {
    $LogDir = Join-Path $InstallRoot 'logs'
    $script:LogAclRequiresGrant = $true
}

$ProvisionLogFile = Join-Path $LogDir 'provision.log'
$script:LogAclReady = $false
$script:ProvisionFailures = @()

function Ensure-LogDirectoryPermissions {
    if ($script:LogAclReady) { return }
    try {
        if (-not (Test-Path $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }

        if ($script:LogAclRequiresGrant) {
            $targets = @('Users')
            try {
                if (Get-LocalUser -Name 'extra' -ErrorAction SilentlyContinue) {
                    $targets += 'extra'
                }
            } catch {}

            foreach ($principal in $targets | Select-Object -Unique) {
                try {
                    $grant = "${principal}:(OI)(CI)M"
                    icacls $LogDir /grant $grant /t /c | Out-Null
                } catch {}
            }
        }

        $script:LogAclReady = $true
    } catch {}
}

function Write-ProvisionLog([string]$Level, [string]$Message) {
    try {
        Ensure-LogDirectoryPermissions
        $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -Path $ProvisionLogFile -Value "$timestamp [$Level] $Message"
    } catch {
        # ignore logging failures
    }
}

function Write-Step([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
    Write-ProvisionLog 'STEP' $Message
}
function Write-Info([string]$Message) {
    Write-Host "[INFO] $Message" -ForegroundColor Gray
    Write-ProvisionLog 'INFO' $Message
}
function Write-Ok([string]$Message) {
    Write-Host "[OK]  $Message" -ForegroundColor Green
    Write-ProvisionLog 'OK' $Message
}
function Write-Warn([string]$Message) {
    Write-Warning $Message
    Write-ProvisionLog 'WARN' $Message
}

function Write-Fail([string]$Message) {
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    Write-ProvisionLog 'FAIL' $Message
}

# Log script origin now that logging helpers are defined
Write-Info ("Esecuzione provisioning da script: {0}" -f $script:ScriptPathLog)
Write-Info ("Log scritto in: {0}" -f $ProvisionLogFile)

$script:RecycleSupportChecked = $false
$script:RecycleSupportAvailable = $false

function Ensure-RecycleSupport {
    if ($script:RecycleSupportChecked) { return $script:RecycleSupportAvailable }
    $script:RecycleSupportChecked = $true
    try {
        Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
        $script:RecycleSupportAvailable = $true
    } catch {
        Write-Warn ("Funzioni Cestino non disponibili: {0}" -f $_.Exception.Message)
        $script:RecycleSupportAvailable = $false
    }
    return $script:RecycleSupportAvailable
}

function Send-ToRecycleBin {
    param([string]$Target)
    if ([string]::IsNullOrWhiteSpace($Target)) { return $false }
    if (-not (Test-Path -LiteralPath $Target)) { return $false }
    if (-not (Ensure-RecycleSupport)) { return $false }
    try {
        $item = Get-Item -LiteralPath $Target -Force -ErrorAction Stop
        if ($item.PSIsContainer) {
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
                $Target,
                [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
                [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin,
                [Microsoft.VisualBasic.FileIO.UICancelOption]::ThrowException
            )
        } else {
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
                $Target,
                [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
                [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin
            )
        }
        return $true
    } catch {
        Write-Warn ("Impossibile spostare nel Cestino '{0}': {1}" -f $Target, $_.Exception.Message)
        return $false
    }
}

function ConvertTo-RegExePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }

    if ($Path.StartsWith('Registry::', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $Path.Substring(10)
    }

    $match = [regex]::Match($Path, '^(HK[A-Z0-9]+):(.*)$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $match.Success) { return $Path }

    $root = switch ($match.Groups[1].Value.ToUpperInvariant()) {
        'HKCU' { 'HKEY_CURRENT_USER' }
        'HKLM' { 'HKEY_LOCAL_MACHINE' }
        'HKU'  { 'HKEY_USERS' }
        'HKCR' { 'HKEY_CLASSES_ROOT' }
        'HKCC' { 'HKEY_CURRENT_CONFIG' }
        'HKPD' { 'HKEY_PERFORMANCE_DATA' }
        'HKPT' { 'HKEY_PERFORMANCE_TEXT' }
        'HKR'  { 'HKEY_CLASSES_ROOT' }
        default { return $null }
    }

    $rest = $match.Groups[2].Value.TrimStart('\')
    if ([string]::IsNullOrWhiteSpace($rest)) { return $root }
    return "$root\$rest"
}

function Convert-PrefixLengthToMaskString {
    param([Parameter(Mandatory = $true)][int]$PrefixLength)

    if ($PrefixLength -lt 0 -or $PrefixLength -gt 32) { return $null }
    $bits = ('1' * $PrefixLength).PadRight(32, '0')
    $parts = for ($i = 0; $i -lt 4; $i++) {
        [Convert]::ToInt32($bits.Substring($i * 8, 8), 2)
    }
    return ($parts -join '.')
}

function Unload-RegistryHiveIfPresent {
    param([Parameter(Mandatory = $true)][string]$HivePath)

    if ([string]::IsNullOrWhiteSpace($HivePath)) { return }

    $rootMap = @{
        'HKCU' = 'HKEY_CURRENT_USER'
        'HKLM' = 'HKEY_LOCAL_MACHINE'
        'HKU'  = 'HKEY_USERS'
        'HKCR' = 'HKEY_CLASSES_ROOT'
        'HKCC' = 'HKEY_CURRENT_CONFIG'
        'HKEY_CURRENT_USER'    = 'HKEY_CURRENT_USER'
        'HKEY_LOCAL_MACHINE'   = 'HKEY_LOCAL_MACHINE'
        'HKEY_USERS'           = 'HKEY_USERS'
        'HKEY_CLASSES_ROOT'    = 'HKEY_CLASSES_ROOT'
        'HKEY_CURRENT_CONFIG'  = 'HKEY_CURRENT_CONFIG'
    }

    $match = [regex]::Match($HivePath, '^(?<root>HK[A-Z]{1,2}|HKEY_[A-Z_]+)(\\(?<sub>.+))?$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $match.Success) { return }

    $root = $match.Groups['root'].Value.ToUpperInvariant()
    if (-not $rootMap.ContainsKey($root)) { return }

    $regRoot = $rootMap[$root]
    $subKey = $match.Groups['sub'].Value
    $testPath = if ([string]::IsNullOrEmpty($subKey)) { "Registry::$regRoot" } else { "Registry::$regRoot\$subKey" }

    if (-not (Test-Path -LiteralPath $testPath)) { return }

    $null = & reg.exe unload $HivePath 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail ("Impossibile scaricare hive {0} (exit {1})" -f $HivePath, $LASTEXITCODE)
        throw "reg unload failed for $HivePath"
    }
}

function Set-RegistryValueStrict {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][object]$Value,
        [string]$Type = 'REG_DWORD'
    )

    $regType = $Type.ToUpperInvariant()
    try {
        New-Item -Path $Path -Force -ErrorAction Stop | Out-Null
        $propertyParams = @{
            Path        = $Path
            Name        = $Name
            Value       = $Value
            Force       = $true
            ErrorAction = 'Stop'
        }
        switch ($regType) {
            'REG_DWORD'    { $propertyParams['PropertyType'] = 'DWord' }
            'REG_QWORD'    { $propertyParams['PropertyType'] = 'QWord' }
            'REG_SZ'       { $propertyParams['PropertyType'] = 'String' }
            'REG_BINARY'   { $propertyParams['PropertyType'] = 'Binary' }
            'REG_MULTI_SZ' { $propertyParams['PropertyType'] = 'MultiString' }
            default        { $propertyParams['PropertyType'] = 'String' }
        }
        New-ItemProperty @propertyParams | Out-Null
    } catch {
        $regPath = ConvertTo-RegExePath -Path $Path
        if (-not $regPath) {
            Write-Fail ("Impossibile risolvere percorso registro '{0}' per fallback: {1}" -f $Path, $_.Exception.Message)
            throw
        }
        try {
            $data = $Value
            if ($regType -eq 'REG_SZ' -and [string]::IsNullOrEmpty([string]$data)) {
                $data = '""'
            }
            & reg.exe add $regPath /v $Name /t $regType /d $data /f | Out-Null
            Write-Info ("Valore {0}\{1} impostato via fallback reg.exe" -f $regPath, $Name)
        } catch {
            Write-Fail ("Impossibile impostare {0}\{1}: {2}" -f $regPath, $Name, $_.Exception.Message)
            throw
        }
    }
}

function Remove-RegistryTreeStrict {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -Path $Path)) { return }
    try {
        Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
    } catch {
        $regPath = ConvertTo-RegExePath -Path $Path
        if (-not $regPath) {
            Write-Fail ("Impossibile risolvere percorso registro '{0}' per eliminazione: {1}" -f $Path, $_.Exception.Message)
            throw
        }
        try {
            & reg.exe delete $regPath /f | Out-Null
            Write-Info ("Chiave {0} eliminata via fallback reg.exe" -f $regPath)
        } catch {
            Write-Fail ("Impossibile eliminare {0}: {1}" -f $regPath, $_.Exception.Message)
            throw
        }
    }
}
function Pause-IfNeeded([string]$Prompt = 'Premi INVIO per continuare...') {
    if (-not $NonInteractive) { Read-Host $Prompt | Out-Null }
}

function Ensure-Winget {
    Write-Step 'Verifica presenza winget'
    $winget = Get-Command -Name 'winget.exe' -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Ok 'winget disponibile'
        return $true
    }
    Write-Info 'winget non trovato. Installo Microsoft App Installer (richiede connettività Internet).'
    $tempDir = Join-Path ([IO.Path]::GetTempPath()) 'maroccos-winget'
    try { New-Item -ItemType Directory -Path $tempDir -Force | Out-Null } catch {}
    $bundlePath = Join-Path $tempDir 'AppInstaller.msixbundle'
    try {
        Invoke-WebRequest -Uri 'https://aka.ms/getwinget' -OutFile $bundlePath -UseBasicParsing -ErrorAction Stop
        Add-AppxPackage -Path $bundlePath -ErrorAction Stop | Out-Null
        Write-Ok 'App Installer installato con successo'
        return $true
    } catch {
        Write-Warn "Installazione winget non riuscita: $($_.Exception.Message)"
        return $false
    } finally {
        try { Remove-Item -Path $bundlePath -Force -ErrorAction SilentlyContinue } catch {}
        try { Remove-Item -Path $tempDir -Force -Recurse -ErrorAction SilentlyContinue } catch {}
    }
}

function Create-RestorePoint {
    param([string]$Description = 'Maroccos Player Provisioning')
    Write-Step 'Creazione punto di ripristino di sistema'
    try {
        # Best-effort: su Windows 11 il servizio VSS gestisce automaticamente il restore point
        try {
            $sr = Get-Service -Name 'VSS' -ErrorAction SilentlyContinue
            if ($sr -and $sr.Status -ne 'Running') {
                Start-Service -Name $sr.Name -ErrorAction SilentlyContinue | Out-Null
            }
        } catch {}
        Checkpoint-Computer -Description $Description -RestorePointType 'MODIFY_SETTINGS' | Out-Null
        Write-Ok 'Punto di ripristino creato'
    } catch {
        Write-Warn "Creazione punto di ripristino non riuscita: $($_.Exception.Message)"
    }
}

function Assert-Admin {
    Write-Step 'Verifica privilegi amministrativi'
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        throw 'Esegui questo script come Amministratore.'
    }
    try {
        $probeKey = 'HKLM:SOFTWARE\MaroccosProvisioningTest'
        New-Item -Path $probeKey -Force | Out-Null
        New-ItemProperty -Path $probeKey -Name 'AccessProbe' -Value (Get-Date -Format 'o') -PropertyType String -Force | Out-Null
        Remove-Item -Path $probeKey -Recurse -Force -ErrorAction SilentlyContinue
    } catch {
        throw "Privilegi insufficienti (token non completamente elevato): $($_.Exception.Message)"
    }
    Write-Ok 'Privilegi amministrativi ok'
}

function Ensure-UserExtra {
    Write-Step 'Verifica/creazione account locale "extra" (amministratore)'
    $plainPassword = 'extra'
    $securePassword = ConvertTo-SecureString $plainPassword -AsPlainText -Force
    try {
        $user = Get-LocalUser -Name 'extra' -ErrorAction Stop
        Write-Info 'Utente "extra" già presente'
    } catch {
        Write-Info 'Creo utente locale "extra" con password "extra"'
        New-LocalUser -Name 'extra' -Password $securePassword -FullName 'Extra Admin' -PasswordNeverExpires:$true -UserMayNotChangePassword:$true | Out-Null
        $user = Get-LocalUser -Name 'extra' -ErrorAction Stop
    }
    try {
        Add-LocalGroupMember -Group 'Administrators' -Member 'extra' -ErrorAction SilentlyContinue
    } catch { }

    try {
        $sid = $user.SID.Value
        $profileKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid"
        $profilePath = $null
        if (Test-Path $profileKey) {
            $profilePath = (Get-ItemProperty -Path $profileKey -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
        }

        if (-not $profilePath -or -not (Test-Path "$profilePath\NTUSER.DAT")) {
            Write-Info "Inizializzo il profilo dell'utente 'extra' eseguendo un processo in background"
            $cred = New-Object System.Management.Automation.PSCredential('extra', $securePassword)
            $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-NonInteractive','-Command','exit' -Credential $cred -LoadUserProfile -WindowStyle Hidden -PassThru -ErrorAction Stop
            $proc.WaitForExit()
            if ($proc.ExitCode -ne 0) {
                Write-Fail "Processo di inizializzazione profilo 'extra' terminato con codice $($proc.ExitCode)"
                throw 'Impossibile inizializzare profilo utente extra'
            }
            Start-Sleep -Seconds 2
            if (Test-Path $profileKey) {
                $profilePath = (Get-ItemProperty -Path $profileKey -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
            }
        }

        if (-not $profilePath -or -not (Test-Path "$profilePath\NTUSER.DAT")) {
            Write-Fail "Profilo dell'utente 'extra' non presente dopo l'inizializzazione automatica"
            throw 'Profilo utente extra non disponibile'
        }
    } catch {
        Write-Fail "Impossibile garantire il profilo per utente 'extra': $($_.Exception.Message)"
        throw
    }

    Write-Ok 'Account "extra" amministratore pronto'
}

function Disable-WindowsUpdate {
    Write-Step 'Disabilitazione Windows Update (servizio e criteri)'
    Try { Stop-Service wuauserv -Force -ErrorAction SilentlyContinue } Catch {}
    Set-Service wuauserv -StartupType Disabled -ErrorAction SilentlyContinue
    New-Item -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Force | Out-Null
    New-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name 'NoAutoUpdate' -Value 1 -PropertyType DWord -Force | Out-Null
    Write-Ok 'Windows Update disabilitato'
}

function Disable-ConsumerAndTelemetry {
    Write-Step 'Riduzione componenti consumer/telemetria'
    New-Item -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Force | Out-Null
    New-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name 'DisableConsumerFeatures' -Value 1 -PropertyType DWord -Force | Out-Null
    # Disattiva alcuni suggerimenti/ads nella sessione corrente (HKCU)
    $cdm = 'HKCU:Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'
    New-Item -Path $cdm -Force | Out-Null
    foreach ($name in 'ContentDeliveryAllowed','OemPreInstalledAppsEnabled','PreInstalledAppsEnabled','SilentInstalledAppsEnabled','SystemPaneSuggestionsEnabled') {
        New-ItemProperty -Path $cdm -Name $name -Value 0 -PropertyType DWord -Force | Out-Null
    }
    # Telemetria base
    New-Item -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Force | Out-Null
    New-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Name 'AllowTelemetry' -Value 0 -PropertyType DWord -Force | Out-Null
    Write-Ok 'Componenti consumer/telemetria ridotti'
}

function Disable-ServicesBloat {
    Write-Step 'Disattivazione servizi non essenziali (best-effort)'
    $services = @(
        'DoSvc',          # Delivery Optimization
        'BITS',           # Background Intelligent Transfer (valuta se necessario)
        'UsoSvc',         # Update Orchestrator
        'WSearch',        # Windows Search (indice)
        'DiagTrack',      # Connected User Experiences and Telemetry
        'dmwappushservice',
        'edgeupdate','edgeupdatem','MapsBroker'
    )
    foreach ($s in $services) {
        Try { Stop-Service $s -Force -ErrorAction SilentlyContinue } Catch {}
        Try { Set-Service $s -StartupType Disabled -ErrorAction SilentlyContinue } Catch {}
    }
    Write-Ok 'Servizi disabilitati (dove possibile)'
}

function Configure-Visuals {
    Write-Step 'Ottimizzazione aspetti grafici (no animazioni, no trasparenze)'
    # Disabilita animazioni e trasparenze per utente corrente
    New-Item -Path 'HKCU:Control Panel\Accessibility' -Force | Out-Null
    New-ItemProperty -Path 'HKCU:Control Panel\Accessibility' -Name 'Animation' -Value 0 -PropertyType String -Force | Out-Null
    New-Item -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Force | Out-Null
    New-ItemProperty -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name 'EnableTransparency' -Value 0 -PropertyType DWord -Force | Out-Null
    Write-Ok 'Animazioni/trasparenze disabilitate'
}

function Clean-Desktop {
    Write-Step 'Pulizia desktop (rimozione icone pubbliche e utente)'
    $paths = @(
        "$Env:PUBLIC\Desktop",
        "$Env:USERPROFILE\Desktop"
    )
    $keepItems = @('Player.lnk', 'media')
    foreach ($p in $paths) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue |
            Where-Object { $keepItems -notcontains $_.Name } |
            ForEach-Object {
                $itemPath = $_.FullName
                if (-not (Send-ToRecycleBin -Target $itemPath)) {
                    try {
                        $params = @{
                            LiteralPath = $itemPath
                            Force       = $true
                            ErrorAction = 'Stop'
                        }
                        if ($_.PSIsContainer) { $params['Recurse'] = $true }
                        Remove-Item @params
                    } catch {
                        Write-Fail ("Impossibile rimuovere '{0}': {1}" -f $itemPath, $_.Exception.Message)
                        throw
                    }
                }
            }
    }

    try {
        Set-RegistryValueStrict -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name 'HideIcons' -Value 1 -Type 'REG_DWORD'
    } catch {
        Write-Fail "Impossibile impostare HideIcons per utente corrente: $($_.Exception.Message)"
        throw
    }

    try {
        $extraUser = Get-LocalUser -Name 'extra' -ErrorAction SilentlyContinue
        if ($extraUser) {
            $extraSID = $extraUser.SID.Value
            $extraHive = "Registry::HKEY_USERS\$extraSID"
            $extraHiveLoaded = $false
            if (-not (Test-Path "$extraHive\Software")) {
                $profilePath = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$extraSID" -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
                if (-not $profilePath) {
                    Write-Fail "Profilo per utente 'extra' non risolto (chiave ProfileList assente)"
                    throw 'Profilo utente extra non disponibile'
                }
                if (-not (Test-Path "$profilePath\NTUSER.DAT")) {
                    Write-Fail "NTUSER.DAT per utente 'extra' non trovato in $profilePath"
                    throw 'Profilo utente extra non inizializzato'
                }
                Unload-RegistryHiveIfPresent -HivePath "HKU\$extraSID"
                $null = & reg.exe load "HKU\$extraSID" "$profilePath\NTUSER.DAT" 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Write-Fail "Impossibile caricare hive dell'utente 'extra' (exit $LASTEXITCODE)"
                    throw 'Caricamento hive extra fallito'
                }
                Start-Sleep -Milliseconds 500
                $extraHiveLoaded = $true
            }
            if (Test-Path "$extraHive\Software") {
                Set-RegistryValueStrict -Path "$extraHive\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name 'HideIcons' -Value 1 -Type 'REG_DWORD'
                Write-Info "Desktop icons nascosti anche per utente 'extra'"
            } else {
                Write-Fail "Hive dell'utente 'extra' non disponibile per impostare HideIcons"
                throw 'Impostazione desktop utente extra fallita'
            }

            if ($extraHiveLoaded) {
                Unload-RegistryHiveIfPresent -HivePath "HKU\$extraSID"
            }
        }
    } catch {
        Write-Fail "Impossibile applicare impostazioni desktop per utente 'extra': $($_.Exception.Message)"
        throw
    }

    Write-Ok 'Desktop ripulito/nascosto'
}

function Set-BlackWallpaper {
    Write-Step 'Impostazione sfondo nero (Solid Color)'
    $SPI_SETDESKWALLPAPER = 20
    $SPIF_UPDATEINIFILE = 0x1
    $SPIF_SENDCHANGE = 0x2

    function Set-WallpaperSolidBlack {
        param([Parameter(Mandatory = $true)][string]$HiveRoot)

        $desktopKey = Join-Path $HiveRoot 'Control Panel\Desktop'
        Set-RegistryValueStrict -Path $desktopKey -Name 'Wallpaper' -Value '' -Type 'REG_SZ'
        Set-RegistryValueStrict -Path $desktopKey -Name 'WallpaperStyle' -Value '0' -Type 'REG_SZ'
        Set-RegistryValueStrict -Path $desktopKey -Name 'TileWallpaper' -Value '0' -Type 'REG_SZ'

        $colorsKey = Join-Path $HiveRoot 'Control Panel\Colors'
        Set-RegistryValueStrict -Path $colorsKey -Name 'Background' -Value '0 0 0' -Type 'REG_SZ'

        $wpKey = Join-Path $HiveRoot 'Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers'
        Set-RegistryValueStrict -Path $wpKey -Name 'BackgroundType' -Value 1 -Type 'REG_DWORD'
        try { Remove-ItemProperty -Path $wpKey -Name 'WallpaperPath' -ErrorAction SilentlyContinue } catch {}
    }

    $defaultLoaded = $false
    $defaultUnloadFailure = $false
    try {
        Set-WallpaperSolidBlack -HiveRoot 'HKCU:'

        if (-not ([System.Management.Automation.PSTypeName]'Win32.WallpaperUtil').Type) {
            Add-Type @"
using System.Runtime.InteropServices;
public static class WallpaperUtil {
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern bool SystemParametersInfo(int uAction, int uParam, string lpvParam, int fuWinIni);
}
"@
        }
        [void][WallpaperUtil]::SystemParametersInfo($SPI_SETDESKWALLPAPER, 0, '', $SPIF_UPDATEINIFILE -bor $SPIF_SENDCHANGE)
        try { Start-Process -FilePath 'RUNDLL32.EXE' -ArgumentList 'user32.dll,UpdatePerUserSystemParameters' -WindowStyle Hidden -NoNewWindow } catch {}

        try {
            $themesDir = Join-Path $Env:APPDATA 'Microsoft\Windows\Themes'
            $transcoded = Join-Path $themesDir 'TranscodedWallpaper'
            if (Test-Path $transcoded) { Remove-Item -Path $transcoded -Force -ErrorAction SilentlyContinue }
        } catch {}

        $extraUser = Get-LocalUser -Name 'extra' -ErrorAction SilentlyContinue
        if ($extraUser) {
            $extraSID = $extraUser.SID.Value
            $extraHive = "Registry::HKEY_USERS\$extraSID"
            $extraHiveLoaded = $false
            if (-not (Test-Path "$extraHive\Control Panel")) {
                $profilePath = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$extraSID" -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
                if (-not $profilePath) {
                    Write-Fail "Profilo per utente 'extra' non risolto (chiave ProfileList assente)"
                    throw 'Profilo utente extra non disponibile'
                }
                if (-not (Test-Path "$profilePath\NTUSER.DAT")) {
                    Write-Fail "NTUSER.DAT per utente 'extra' non trovato in $profilePath"
                    throw 'Profilo utente extra non inizializzato'
                }
                Unload-RegistryHiveIfPresent -HivePath "HKU\$extraSID"
                $null = & reg.exe load "HKU\$extraSID" "$profilePath\NTUSER.DAT" 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Write-Fail "Impossibile caricare hive dell'utente 'extra' (exit $LASTEXITCODE)"
                    throw 'Caricamento hive extra fallito'
                }
                Start-Sleep -Milliseconds 500
                $extraHiveLoaded = $true
            }

            if (-not (Test-Path "$extraHive\Control Panel")) {
                Write-Fail "Hive dell'utente 'extra' non disponibile per impostare lo sfondo"
                throw 'Hive utente extra non disponibile'
            }

            Set-WallpaperSolidBlack -HiveRoot $extraHive
            Write-Info "Sfondo SOLID nero impostato anche per utente 'extra'"

            if ($extraHiveLoaded) {
                Unload-RegistryHiveIfPresent -HivePath "HKU\$extraSID"
            }
        }

        Unload-RegistryHiveIfPresent -HivePath 'HKU\DefaultUser'
        $null = & reg.exe load 'HKU\DefaultUser' 'C:\Users\Default\NTUSER.DAT' 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Impossibile caricare hive del profilo Default (exit $LASTEXITCODE)"
            throw 'Caricamento hive Default fallito'
        }
        Start-Sleep -Milliseconds 500
        $defaultLoaded = $true

        Set-WallpaperSolidBlack -HiveRoot 'Registry::HKEY_USERS\DefaultUser'
        Write-Info 'Sfondo SOLID nero impostato per profilo Default'

        Write-Ok 'Sfondo impostato a SOLID nero'
    } catch {
        $errMsg = "Impossibile impostare sfondo nero: {0}" -f $_.Exception.Message
        Write-Fail $errMsg
        $script:ProvisionFailures += $errMsg
    } finally {
        if ($defaultLoaded) {
            $unloaded = $false
            foreach ($delay in 0, 1000, 2000) {
                if ($delay -gt 0) { Start-Sleep -Milliseconds $delay }
                try {
                    Unload-RegistryHiveIfPresent -HivePath 'HKU\DefaultUser'
                    $unloaded = $true
                    break
                } catch {
                    # retry after short delay
                }
            }
            if (-not $unloaded) {
                try {
                    $psi = New-Object System.Diagnostics.ProcessStartInfo
                    $psi.FileName = 'reg.exe'
                    $psi.Arguments = 'UNLOAD "HKU\DefaultUser"'
                    $psi.UseShellExecute = $false
                    $psi.CreateNoWindow = $true
                    $proc = [System.Diagnostics.Process]::Start($psi)
                    $proc.WaitForExit()
                    if ($proc.ExitCode -eq 0) { $unloaded = $true }
                } catch {}
            }
            if (-not $unloaded) {
                Write-Warn 'Scaricamento hive Default fallito al primo tentativo: ritento tra 3 secondi'
                $defaultUnloadFailure = $true
            }
        }

        if ($defaultUnloadFailure) {
            Start-Sleep -Seconds 3
            try {
                Unload-RegistryHiveIfPresent -HivePath 'HKU\DefaultUser'
                $defaultUnloadFailure = $false
                $defaultLoaded = $false
                Write-Info 'Hive Default scaricato al secondo tentativo'
            } catch {
                $defaultUnloadFailure = $true
                Write-Warn ("Secondo tentativo di scaricare hive Default fallito: {0}" -f $_.Exception.Message)
            }
        }

        if ($defaultUnloadFailure) {
            $manualMsg = 'Scaricamento hive Default fallito: eseguire manualmente reg unload "HKU\DefaultUser" prima di proseguire.'
            Write-Fail $manualMsg
            $script:ProvisionFailures += $manualMsg
        }
    }
}

# Disabilita Windows Spotlight, suggerimenti/banner (best-effort) e riduce contenuti promozionali
function Disable-SpotlightAndBanners {
    Write-Step 'Disattivazione Spotlight, suggerimenti e banner (best-effort)'
    try {
        # Policy macchina: disattiva Spotlight/Consumer
        New-Item -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Force | Out-Null
        New-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name 'DisableWindowsSpotlightFeatures' -Value 1 -PropertyType DWord -Force | Out-Null
        New-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name 'DisableSpotlightCollectionOnDesktop' -Value 1 -PropertyType DWord -Force | Out-Null
        New-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name 'DisableConsumerFeatures' -Value 1 -PropertyType DWord -Force | Out-Null
        New-Item -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\Personalization' -Force | Out-Null
        New-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\Personalization' -Name 'NoLockScreen' -Value 1 -PropertyType DWord -Force | Out-Null

        # Utente corrente: riduci suggerimenti/spotlight
        $cdm = 'HKCU:Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'
        New-Item -Path $cdm -Force | Out-Null
        $cdmFlags = @(
            'ContentDeliveryAllowed','OemPreInstalledAppsEnabled','PreInstalledAppsEnabled','SilentInstalledAppsEnabled','SystemPaneSuggestionsEnabled',
            'RotatingLockScreenEnabled','RotatingLockScreenOverlayEnabled','SoftLandingEnabled',
            'SubscribedContent-310093Enabled','SubscribedContent-338387Enabled','SubscribedContent-338388Enabled','SubscribedContent-338389Enabled','SubscribedContent-353694Enabled','SubscribedContent-353698Enabled'
        )
        foreach ($name in $cdmFlags) { New-ItemProperty -Path $cdm -Name $name -Value 0 -PropertyType DWord -Force | Out-Null }

        # Notifiche (toast) sopra lock screen disabilitate
        New-Item -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Notifications\Settings' -Force | Out-Null
        New-ItemProperty -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Notifications\Settings' -Name 'NOC_GLOBAL_SETTING_ALLOW_TOASTS_ABOVE_LOCK' -Value 0 -PropertyType DWord -Force | Out-Null
        # Facoltativo: disabilita toast in generale (commenta se non desiderato)
        try { New-Item -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\PushNotifications' -Force | Out-Null; New-ItemProperty -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\PushNotifications' -Name 'ToastEnabled' -Value 0 -PropertyType DWord -Force | Out-Null } catch {}

        # Applica anche per utente 'extra' (se presente)
        try {
            $extraUser = Get-LocalUser -Name 'extra' -ErrorAction SilentlyContinue
            if ($extraUser) {
                $sid = $extraUser.SID.Value
                $hive = "Registry::HKEY_USERS\$sid"
                if (-not (Test-Path "$hive\Software")) {
                    $profilePath = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid" -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
                    if ($profilePath -and (Test-Path "$profilePath\NTUSER.DAT")) { & reg load "HKU\$sid" "$profilePath\NTUSER.DAT" 2>$null; Start-Sleep -Milliseconds 500 }
                }
                if (Test-Path "$hive\Software") {
                    $cdmExtra = "$hive\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
                    New-Item -Path $cdmExtra -Force -ErrorAction SilentlyContinue | Out-Null
                    foreach ($name in $cdmFlags) { New-ItemProperty -Path $cdmExtra -Name $name -Value 0 -PropertyType DWord -Force -ErrorAction SilentlyContinue | Out-Null }
                }
            }
        } catch {}

        Write-Ok 'Spotlight/suggerimenti/banners disattivati (best-effort)'
    } catch {
        Write-Warn "Impossibile applicare disattivazione banners: $($_.Exception.Message)"
    }
}

# Configura taskbar minimale: nasconde Widgets/Chat/Cerca/TaskView, auto-hide e svuota i pinned (best-effort)
function Configure-TaskbarMinimal {
    Write-Step 'Configurazione taskbar minimale (auto-hide, nessuna icona di sistema, pinned svuotati)'
    try {
        $adv = 'HKCU:Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
        Set-RegistryValueStrict -Path $adv -Name 'TaskbarAutoHide' -Value 1 -Type 'REG_DWORD'
        Set-RegistryValueStrict -Path $adv -Name 'ShowTaskViewButton' -Value 0 -Type 'REG_DWORD'
        Set-RegistryValueStrict -Path $adv -Name 'TaskbarDa' -Value 0 -Type 'REG_DWORD'
        Set-RegistryValueStrict -Path $adv -Name 'TaskbarMn' -Value 0 -Type 'REG_DWORD'

        Set-RegistryValueStrict -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Search' -Name 'SearchboxTaskbarMode' -Value 0 -Type 'REG_DWORD'
        Set-RegistryValueStrict -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Feeds' -Name 'ShellFeedsTaskbarViewMode' -Value 2 -Type 'REG_DWORD'

        Remove-RegistryTreeStrict -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband'

        $extraUser = Get-LocalUser -Name 'extra' -ErrorAction SilentlyContinue
        if ($extraUser) {
            $sid = $extraUser.SID.Value
            $hive = "Registry::HKEY_USERS\$sid"
            $extraHiveLoaded = $false
            if (-not (Test-Path "$hive\Software")) {
                $profilePath = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid" -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
                if (-not $profilePath) {
                    Write-Fail "Profilo per utente 'extra' non risolto (chiave ProfileList assente)"
                    throw 'Profilo utente extra non disponibile'
                }
                if (-not (Test-Path "$profilePath\NTUSER.DAT")) {
                    Write-Fail "NTUSER.DAT per utente 'extra' non trovato in $profilePath"
                    throw 'Profilo utente extra non inizializzato'
                }
                Unload-RegistryHiveIfPresent -HivePath "HKU\$sid"
                $null = & reg.exe load "HKU\$sid" "$profilePath\NTUSER.DAT" 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Write-Fail "Impossibile caricare hive dell'utente 'extra' (exit $LASTEXITCODE)"
                    throw 'Caricamento hive extra fallito'
                }
                Start-Sleep -Milliseconds 500
                $extraHiveLoaded = $true
            }

            if (-not (Test-Path "$hive\Software")) {
                Write-Fail "Hive dell'utente 'extra' non disponibile per configurare la taskbar"
                throw 'Hive utente extra non disponibile'
            }

            $extraAdv = "$hive\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
            Set-RegistryValueStrict -Path $extraAdv -Name 'TaskbarAutoHide' -Value 1 -Type 'REG_DWORD'
            Set-RegistryValueStrict -Path $extraAdv -Name 'ShowTaskViewButton' -Value 0 -Type 'REG_DWORD'
            Set-RegistryValueStrict -Path $extraAdv -Name 'TaskbarDa' -Value 0 -Type 'REG_DWORD'
            Set-RegistryValueStrict -Path $extraAdv -Name 'TaskbarMn' -Value 0 -Type 'REG_DWORD'

            Set-RegistryValueStrict -Path "$hive\Software\Microsoft\Windows\CurrentVersion\Search" -Name 'SearchboxTaskbarMode' -Value 0 -Type 'REG_DWORD'
            Remove-RegistryTreeStrict -Path "$hive\Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband"

            if ($extraHiveLoaded) {
                Unload-RegistryHiveIfPresent -HivePath "HKU\$sid"
            }
        }

        try { Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800; Start-Process explorer.exe } catch {}
        Write-Ok 'Taskbar configurata'
    } catch {
        Write-Fail "Impossibile configurare taskbar: $($_.Exception.Message)"
        throw
    }
}

function Configure-PowerPlan {
    Write-Step 'Piano alimentazione Prestazioni elevate + no sleep/hibernate'
    powercfg /setactive SCHEME_MIN | Out-Null
    powercfg /hibernate off | Out-Null
    powercfg /change standby-timeout-ac 0 | Out-Null
    powercfg /change monitor-timeout-ac 0 | Out-Null
    Write-Ok 'Power plan ottimizzato'
}

function Set-PowerButtonActionToShutdown {
    Write-Step 'Configuro il pulsante di accensione per spegnere'
    try {
        $guidPowerButton = '4f971e89-eebd-4455-a8de-9e59040e7347'
        $guidAction = '7648efa3-dd9c-4e3e-b566-50f929386280'
        $shutdownValue = 3
        foreach ($scheme in @('SCHEME_CURRENT', 'SCHEME_MIN')) {
            powercfg /setacvalueindex $scheme $guidPowerButton $guidAction $shutdownValue | Out-Null
            powercfg /setdcvalueindex $scheme $guidPowerButton $guidAction $shutdownValue | Out-Null
        }
        powercfg /S SCHEME_CURRENT | Out-Null
        Write-Ok 'Power button ora spegne su AC e DC'
    } catch {
        Write-Warn "Impossibile impostare pulsante power: $($_.Exception.Message)"
    }
}

function Configure-Firewall {
    Write-Step 'Disattivazione completa firewall Windows (tutti i profili)'
    try {
        Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False -ErrorAction SilentlyContinue
        Try { Stop-Service -Name 'MpsSvc' -Force -ErrorAction SilentlyContinue } Catch {}
        Try { Set-Service -Name 'MpsSvc' -StartupType Disabled -ErrorAction SilentlyContinue } Catch {}
        Write-Ok 'Firewall disattivato (best-effort)'
    } catch {
        Write-Warn "Impossibile disattivare completamente il firewall: $($_.Exception.Message)"
    }
}

function Configure-MediaShare {
    if ($SkipMediaShare) { Write-Info 'Condivisione SMB saltata per opzione'; return }
    Write-Step 'Condivisione cartella media via SMB'
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    $mediaPath = Join-Path $desktopPath 'media'
    if (-not (Test-Path $mediaPath)) {
        try {
            New-Item -ItemType Directory -Path $mediaPath -Force | Out-Null
            Write-Info "Cartella creata: $mediaPath"
        } catch {
            Write-Warn "Impossibile creare la cartella media su Desktop: $($_.Exception.Message)"
            return
        }
    }
    try {
        $svc = Get-Service -Name 'LanmanServer' -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -ne 'Running') {
            Start-Service -Name $svc.Name -ErrorAction SilentlyContinue | Out-Null
        }
    } catch {}
    $shareName = 'media'
    try {
        $existing = Get-SmbShare -Name $shareName -ErrorAction SilentlyContinue
        if ($existing) {
            if ($existing.Path -ne $mediaPath) {
                Write-Info 'Share esistente con percorso differente: lo ricreo'
                Remove-SmbShare -Name $shareName -Force -ErrorAction SilentlyContinue
            }
        }
        if (-not (Get-SmbShare -Name $shareName -ErrorAction SilentlyContinue)) {
            New-SmbShare -Name $shareName -Path $mediaPath -FullAccess 'Everyone' -ErrorAction Stop | Out-Null
        }
        Grant-SmbShareAccess -Name $shareName -AccountName 'Everyone' -AccessRight Change -Force -ErrorAction SilentlyContinue | Out-Null
        icacls "$mediaPath" /grant 'Everyone:(OI)(CI)M' /T /C | Out-Null
        Write-Ok "Share SMB 'media' disponibile ($mediaPath)"
    } catch {
        Write-Warn "Errore configurando share SMB: $($_.Exception.Message)"
    }
}

function Rename-ComputerFromConfig {
    if ($SkipHostnameSync) { Write-Info 'Sincronizzazione hostname saltata per opzione'; return }
    Write-Step 'Allineamento nome computer da config.json'
    $configPath = Join-Path $InstallRoot 'headless-player\config.json'
    if (-not (Test-Path $configPath)) {
        Write-Warn "Config non trovato: $configPath"
        return
    }
    try {
        $configJson = Get-Content -Path $configPath -Raw -ErrorAction Stop
        $config = $configJson | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Warn "Impossibile leggere config.json: $($_.Exception.Message)"
        return
    }
        $desired = $null
        if ($config -and $config.PSObject.Properties['device_name']) {
            $desired = [string]$config.device_name
        }
    if (-not $desired) {
        Write-Warn 'device_name non definito in config.json: salto rinomina'
        return
    }
    if ($desired -match '^[0-9]{1,3}(\.[0-9]{1,3}){3}$') {
        Write-Warn "device_name ($desired) sembra un indirizzo IP: definire un nome esplicito nel manager"
        return
    }
    $current = $Env:COMPUTERNAME
    if ($current -ieq $desired) {
        Write-Ok 'Nome computer già coerente con config.json'
        return
    }
    if ($desired.Length -gt 15) {
        Write-Warn "device_name '$desired' troppo lungo (max 15 caratteri)"
        return
    }
    if ($desired -notmatch '^[A-Za-z0-9-]+$' -or $desired.StartsWith('-') -or $desired.EndsWith('-')) {
        Write-Warn "device_name '$desired' contiene caratteri non validi per il NetBIOS name"
        return
    }
    try {
        Rename-Computer -NewName $desired -Force -ErrorAction Stop | Out-Null
        Write-Ok "Nome computer aggiornato a $desired (riavvio richiesto)"
        $script:RebootRequired = $true
    } catch {
        Write-Warn "Rinomina computer fallita: $($_.Exception.Message)"
    }
}

# Configura esecuzione ad ogni login dell'utente per ribadire impostazioni desktop (nero, taskbar, temi)
function Ensure-RunOnLogin {
    param([string]$InstallRoot = 'C:\\Program Files\\marocco-player')
    Write-Step 'Configura esecuzione impostazioni utente ad ogni login'
    try {
        $runKey = 'HKLM:SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run'
        New-Item -Path $runKey -Force | Out-Null
        $scriptPath = Join-Path $InstallRoot 'tools\\windows\\apply_user_settings.ps1'
        if (-not (Test-Path $scriptPath)) { Write-Warn "Script utente non trovato: $scriptPath"; return }
        $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
        New-ItemProperty -Path $runKey -Name 'MaroccosUserSettings' -Value $cmd -PropertyType String -Force | Out-Null
        Write-Ok 'Auto-run impostato per tutti gli utenti (HKLM\\...\\Run)'
    } catch {
        Write-Warn "Impossibile configurare auto-run per login: $($_.Exception.Message)"
    }
}

function Ensure-ServiceRecovery {
    param(
        [Parameter(Mandatory=$true)][string]$ServiceName
    )
    Write-Step ("Configuro restart automatico del servizio {0}" -f $ServiceName)
    try {
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if (-not $svc) {
            Write-Warn ("Servizio {0} non trovato; salto recovery" -f $ServiceName)
            return
        }
        $cmd = "sc.exe"
        $upload = @(
            "failure",$ServiceName,
            "reset=0",
            "actions=restart/60000/restart/60000/restart/60000"
        )
        Write-Info ("Imposto failure actions: {0}" -f ($upload -join ' '))
        $proc = Start-Process -FilePath $cmd -ArgumentList $upload -NoNewWindow -Wait -PassThru -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) {
            Write-Warn ("sc failure exit code {0}" -f $proc.ExitCode)
        }
    } catch {
        Write-Warn ("Impossibile impostare restart su {0}: {1}" -f $ServiceName, $_.Exception.Message)
    }
}

function Ensure-HeadlessScheduledTask {
    param(
        [string]$InstallRoot = 'C:\Program Files\marocco-player',
        [string]$TaskName = 'MaroccosHeadless',
        [string]$RunAsUser = 'extra',
        [ValidateSet('Logon','Startup')]
        [string]$Trigger = 'Logon',
        [string]$RunAsPassword,
        [switch]$RunAsSystem
    )

    Write-Step "Configuro avvio headless-player (trigger $Trigger)"

    $exePath = Join-Path $InstallRoot 'headless-player\headless-player.exe'
    if (-not (Test-Path $exePath)) {
        Write-Warn ("headless-player.exe non trovato in {0}; salto configurazione autostart" -f $exePath)
        return
    }

    try {
        Import-Module ScheduledTasks -ErrorAction Stop | Out-Null
    } catch {
        Write-Warn ("Modulo ScheduledTasks non disponibile: {0}" -f $_.Exception.Message)
        return
    }

    try {
        $svc = Get-Service -Name 'MaroccosHeadless' -ErrorAction SilentlyContinue
        if ($svc) {
            if ($svc.Status -eq 'Running') {
                try { Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue } catch {}
            }
            if ($svc.StartType -ne 'Manual') {
                try {
                    Set-Service -Name $svc.Name -StartupType Manual -ErrorAction Stop
                    Write-Info 'Servizio MaroccosHeadless impostato su avvio manuale'
                } catch {
                    Write-Warn ("Impossibile modificare startup del servizio MaroccosHeadless: {0}" -f $_.Exception.Message)
                }
            }
        }
    } catch {
        Write-Warn ("Errore durante la gestione del servizio MaroccosHeadless: {0}" -f $_.Exception.Message)
    }

    $workDir = Split-Path $exePath -Parent
    $currentPrincipal = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
    $principalUser = $currentPrincipal

    if ($RunAsSystem) {
        $principalUser = 'SYSTEM'
    } elseif (-not [string]::IsNullOrWhiteSpace($RunAsUser)) {
        try {
            $targetUser = $RunAsUser
            if ($RunAsUser -notmatch '\\|@') {
                $targetUser = "${env:COMPUTERNAME}\$RunAsUser"
            }
            if (Get-LocalUser -Name $RunAsUser -ErrorAction SilentlyContinue) {
                $principalUser = $targetUser
            } else {
                Write-Warn ("Utente '{0}' non trovato; userò {1}" -f $RunAsUser, $currentPrincipal)
            }
        } catch {
            Write-Warn ("Impossibile verificare utente '{0}': {1}" -f $RunAsUser, $_.Exception.Message)
        }
    }

    try {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Info ("Rimuovo attività esistente {0}" -f $TaskName)
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
    } catch {
        Write-Warn ("Impossibile rimuovere l'attività {0}: {1}" -f $TaskName, $_.Exception.Message)
    }

    $action = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $workDir
    switch ($Trigger) {
        'Startup' { $trigger = New-ScheduledTaskTrigger -AtStartup }
        default   {
            if ($principalUser -and -not $RunAsSystem) { $trigger = New-ScheduledTaskTrigger -AtLogOn -User $principalUser }
            else { $trigger = New-ScheduledTaskTrigger -AtLogOn }
        }
    }
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

    $logonType = 'Interactive'
    if ($RunAsSystem) {
        $logonType = 'ServiceAccount'
    } elseif ($RunAsPassword) {
        $logonType = 'Password'
    }

    if ($Trigger -eq 'Startup' -and -not $RunAsSystem -and -not $RunAsPassword) {
        Write-Warn 'Trigger Startup richiede credenziali o account SYSTEM: userò SYSTEM.'
        $principalUser = 'SYSTEM'
        $logonType = 'ServiceAccount'
        $RunAsSystem = $true
    }

    $principal = New-ScheduledTaskPrincipal -UserId $principalUser -LogonType $logonType -RunLevel Highest

    try {
        $definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
        if ($RunAsPassword -and -not $RunAsSystem) {
            Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force -User $principalUser -Password $RunAsPassword | Out-Null
        } else {
            Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force | Out-Null
        }
        Write-Ok ("Attività {0} registrata (utente {1}, trigger {2})" -f $TaskName, $principalUser, $Trigger)
    } catch {
        Write-Warn ("Registrazione attività {0} fallita: {1}" -f $TaskName, $_.Exception.Message)
        return
    }

    Write-Info ('Per testare subito: Start-ScheduledTask -TaskName "{0}"' -f $TaskName)
}

function Apply-PreferredResolution {
    Write-Step 'Forzo risoluzione 1280x720 @ 50Hz (se supportata)'
    $relativeCandidates = @(
        'tools\SetResolution\SetResolution.exe',
        'tools\windows\SetResolution\SetResolution.exe',
        'tools\windows\SetResolution.exe'
    )
    $exe = $relativeCandidates |
        ForEach-Object { [System.IO.Path]::Combine($InstallRoot, $_) } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1

    if (-not $exe) {
        Write-Fail "Utility SetResolution.exe non trovata nel percorso di installazione ($InstallRoot)"
        throw 'SetResolution.exe non disponibile'
    }

    Write-Info "Eseguo $exe 1280 720 50"
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exe
        $psi.Arguments = '1280 720 50'
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        if ($stdout) { Write-Info ($stdout.Trim()) }
        if ($proc.ExitCode -ne 0) {
            if ($stderr) { Write-Warn ($stderr.Trim()) }
            Write-Fail "SetResolution.exe ha restituito codice $($proc.ExitCode)"
            throw "SetResolution.exe exit code $($proc.ExitCode)"
        }
        if ($stderr) { Write-Info ($stderr.Trim()) }
        if ($stdout -match 'riavvio') {
            $script:RebootRequired = $true
        }
        Write-Ok 'Risoluzione preferita impostata.'
    } catch {
        Write-Fail "Impossibile applicare la risoluzione preferita: $($_.Exception.Message)"
        throw
    }
}

function Get-EthernetAdapterAlias {
    if ($EthernetAlias) {
        if ($EthernetAlias -match '^[0-9]{1,3}(\.[0-9]{1,3}){3}$') {
            Write-Warn ("Alias specificato '{0}' sembra un indirizzo IP: ignoro e provo autodetect" -f $EthernetAlias)
        } else {
        $manual = Get-NetAdapter -Name $EthernetAlias -ErrorAction SilentlyContinue
        if ($manual) { return $manual.Name }
        Write-Warn ("Interfaccia specificata '{0}' non trovata: provo autodetect" -f $EthernetAlias)
        }
    }
    $candidates = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue)
    if (-not $candidates) {
        $candidates = @(Get-NetAdapter | Where-Object { $_.HardwareInterface -eq $true })
    }
    if (-not $candidates) { throw 'Nessuna interfaccia Ethernet trovata' }
    $sorted = $candidates |
        Sort-Object -Property @{ Expression = {
            $name = $_.Name
            $status = $_.Status
            if ($name -match 'Ethernet|LAN') {
                if ($status -eq 'Up') { return 0 }
                if ($status -ne 'Disabled') { return 1 }
                return 2
            }
            if ($status -eq 'Up') { return 3 }
            return 4
        }}, @{ Expression = { $_.InterfaceIndex } }
    $cand = $sorted | Select-Object -First 1
    if (-not $cand) { throw 'Nessuna interfaccia Ethernet trovata' }
    return $cand.Name
}

function Configure-EthernetStaticIP {
    Write-Step 'Impostazione IP statico su Ethernet (con gateway)'
    $alias = Get-EthernetAdapterAlias
    Write-Info "Uso interfaccia: $alias"
    try {
        Set-NetIPInterface -InterfaceAlias $alias -Dhcp Disabled -ErrorAction Stop
    } catch {
        $msg = "Impossibile disattivare DHCP su {0} (Active): {1}" -f $alias, $_.Exception.Message
        Write-Fail $msg
        $script:ProvisionFailures += $msg
        throw
    }

    try {
        Set-NetIPInterface -InterfaceAlias $alias -Dhcp Disabled -PolicyStore PersistentStore -ErrorAction Stop
    } catch {
        $msg = "Impossibile disattivare DHCP su {0} (PersistentStore): {1}" -f $alias, $_.Exception.Message
        Write-Fail $msg
        $script:ProvisionFailures += $msg
        throw
    }

    foreach ($storeParam in @($null, 'PersistentStore')) {
        try {
            $getParams = @{ InterfaceAlias = $alias; AddressFamily = 'IPv4'; ErrorAction = 'SilentlyContinue' }
            if ($storeParam) { $getParams['PolicyStore'] = $storeParam }
            Get-NetIPAddress @getParams | ForEach-Object {
                $removeParams = @{ InterfaceAlias = $alias; IPAddress = $_.IPAddress; Confirm = $false; ErrorAction = 'Stop' }
                if ($storeParam) { $removeParams['PolicyStore'] = $storeParam }
                Remove-NetIPAddress @removeParams
            }
        } catch {
            $scope = if ($storeParam) { $storeParam } else { 'Active' }
            Write-Fail ("Impossibile rimuovere IP preesistenti da {0} ({1}): {2}" -f $alias, $scope, $_.Exception.Message)
            throw
        }
    }

    foreach ($policyStore in @('ActiveStore','PersistentStore')) {
        try {
            Get-NetRoute -InterfaceAlias $alias -AddressFamily IPv4 -PolicyStore $policyStore -ErrorAction SilentlyContinue |
                Where-Object { $_.DestinationPrefix -eq '0.0.0.0/0' } |
                ForEach-Object {
                    $removeParams = @{
                        InterfaceAlias    = $alias
                        DestinationPrefix = $_.DestinationPrefix
                        NextHop           = $_.NextHop
                        RouteMetric       = $_.RouteMetric
                        Confirm           = $false
                        ErrorAction       = 'Stop'
                    }
                    if ($policyStore -ne 'ActiveStore') { $removeParams['PolicyStore'] = $policyStore }
                    Remove-NetRoute @removeParams
                }
        } catch {
            Write-Fail ("Impossibile rimuovere default gateway preesistente su {0} ({1}): {2}" -f $alias, $policyStore, $_.Exception.Message)
            throw
        }
    }

    $ipConfigured = $false
    try {
        New-NetIPAddress -InterfaceAlias $alias -IPAddress $StaticIP -PrefixLength $PrefixLength -DefaultGateway $Gateway -ErrorAction Stop | Out-Null
        $ipConfigured = $true
    } catch {
        $warnMsg = "Impossibile impostare IP statico {0} su {1} tramite cmdlet: {2}" -f $StaticIP, $alias, $_.Exception.Message
        Write-Warn $warnMsg

        $maskString = Convert-PrefixLengthToMaskString -PrefixLength $PrefixLength
        if (-not $maskString) {
            $failMsg = "Impossibile convertire prefix {0} in subnet mask" -f $PrefixLength
            Write-Fail $failMsg
            $script:ProvisionFailures += $failMsg
            throw
        }

        $gatewayArg = if ([string]::IsNullOrWhiteSpace($Gateway)) { 'none' } else { $Gateway }
        $netshOutput = & netsh interface ipv4 set address name="$alias" static $StaticIP $maskString $gatewayArg store=persistent 2>&1
        $exitCode = $LASTEXITCODE
        $netshText = if ($netshOutput) { $netshOutput.Trim() } else { '<nessun output>' }
        if ($exitCode -eq 0) {
            if ($netshOutput) { Write-Info $netshText }
            Write-Info 'IP statico configurato via fallback netsh (store=persistent)'
            $ipConfigured = $true
        } else {
            $failMsg = "Fallback netsh set address fallito (exit {0}): {1}" -f $exitCode, $netshText
            Write-Fail $failMsg
            $script:ProvisionFailures += $failMsg
            throw
        }
    }

    if ($DnsServers -and $DnsServers.Length -gt 0) {
        $dnsConfigured = $false
        try {
            Set-DnsClientServerAddress -InterfaceAlias $alias -ServerAddresses $DnsServers -ErrorAction Stop
            $dnsConfigured = $true
        } catch {
            $warnMsg = "Impossibile impostare DNS su {0} tramite cmdlet: {1}" -f $alias, $_.Exception.Message
            Write-Warn $warnMsg

            try {
                $primary = $DnsServers[0]
                $netshOutPrimary = & netsh interface ip set dns name="$alias" static $primary primary 2>&1
                $exitPrimary = $LASTEXITCODE
                $primaryText = if ($netshOutPrimary) { $netshOutPrimary.Trim() } else { '<nessun output>' }
                if ($exitPrimary -ne 0) {
                    $failMsg = "Fallback netsh set dns fallito (exit {0}): {1}" -f $exitPrimary, $primaryText
                    Write-Fail $failMsg
                    $script:ProvisionFailures += $failMsg
                    throw
                }
                if ($netshOutPrimary) { Write-Info $primaryText }

                for ($i = 1; $i -lt $DnsServers.Length; $i++) {
                    $addr = $DnsServers[$i]
                    $index = $i + 1
                    $netshOutAdd = & netsh interface ip add dns name="$alias" $addr index=$index 2>&1
                    $exitAdd = $LASTEXITCODE
                    $addText = if ($netshOutAdd) { $netshOutAdd.Trim() } else { '<nessun output>' }
                    if ($exitAdd -ne 0) {
                        $failMsg = "Fallback netsh add dns {0} fallito (exit {1}): {2}" -f $addr, $exitAdd, $addText
                        Write-Fail $failMsg
                        $script:ProvisionFailures += $failMsg
                        throw
                    }
                    if ($netshOutAdd) { Write-Info $addText }
                }

                Write-Info 'DNS configurati via fallback netsh'
                $dnsConfigured = $true
            } catch {
                if (-not $dnsConfigured) {
                    $msg = "Errore durante il fallback netsh per DNS su {0}: {1}" -f $alias, $_.Exception.Message
                    Write-Fail $msg
                    $script:ProvisionFailures += $msg
                    throw
                }
            }
        }
    }

    Write-Ok "Ethernet statico configurato: $StaticIP/$PrefixLength gw $Gateway"
}

function Ensure-WiFiDHCP {
    Write-Step 'Verifica Wi‑Fi in DHCP'
    $wifi = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -match 'Wi-Fi|Wireless' } | Select-Object -First 1
    if ($wifi) {
        Try { Set-NetIPInterface -InterfaceAlias $wifi.Name -Dhcp Enabled -ErrorAction SilentlyContinue } Catch {}
        Try { Set-DnsClientServerAddress -InterfaceAlias $wifi.Name -ResetServerAddresses -ErrorAction SilentlyContinue } Catch {}
        Write-Ok "Wi‑Fi ($($wifi.Name)) impostato su DHCP"
    } else {
        Write-Info 'Nessuna interfaccia Wi‑Fi trovata: salto'
    }
}

function Ensure-WiFiPreferredNetwork {
    Write-Step 'Tentativo connessione Wi‑Fi extratech-marocco'
    $tempProfile = $null
    try {
        $wifi = Get-NetAdapter | Where-Object { $_.Status -ne 'Disabled' -and $_.Name -match 'Wi-Fi|Wireless' } | Select-Object -First 1
        if (-not $wifi) {
            Write-Info 'Nessuna interfaccia Wi‑Fi disponibile: salto connessione preferita'
            return
        }

        $profileName = 'extratech-marocco'
        $profileXml = @"
<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>extratech-marocco</name>
    <SSIDConfig>
        <SSID>
            <name>extratech-marocco</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>eXtratech</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>
"@

            $tempProfile = Join-Path ([IO.Path]::GetTempPath()) 'extratech-marocco.wlan.xml'
            try {
                Set-Content -Path $tempProfile -Value $profileXml -Encoding ASCII -Force
            } catch {
                Write-Warn ('Impossibile scrivere profilo Wi-Fi temporaneo: {0}' -f $_.Exception.Message)
                return
            }

            try {
                & netsh wlan add profile filename="$tempProfile" user=current | Out-Null
            } catch {
                Write-Warn ('netsh add profile fallito: {0}' -f $_.Exception.Message)
                return
            }

            & netsh wlan connect name="$profileName" ssid="$profileName" interface="$($wifi.Name)" | Out-Null
            Start-Sleep -Seconds 3

            $interfaces = & netsh wlan show interfaces
            if ($LASTEXITCODE -eq 0 -and $interfaces -match 'State\s*:\s*connected' -and $interfaces -match 'SSID\s*:\s*extratech-marocco') {
                Write-Ok 'Connessione Wi‑Fi extratech-marocco stabilita (best-effort)'
            } else {
                $warnMsg = 'Connessione Wi‑Fi extratech-marocco non riuscita (verificare copertura o credenziali)'
                Write-Warn $warnMsg
                $script:ProvisionFailures += $warnMsg
            }
        } catch {
            $msg = "Errore durante il tentativo di connessione Wi‑Fi: {0}" -f $_.Exception.Message
            Write-Warn $msg
            $script:ProvisionFailures += $msg
        } finally {
            if ($tempProfile) {
                try { Remove-Item -Path $tempProfile -Force -ErrorAction SilentlyContinue } catch {}
            }
        }
}

# Abilita OpenSSH Server e avvio automatico (porta di default), abilita autenticazione password
function Configure-OpenSSH {
    Write-Step 'Installazione/abilitazione OpenSSH Server'
    try {
        $cap = Get-WindowsCapability -Online | Where-Object { $_.Name -like 'OpenSSH.Server*' }
        if (-not $cap -or $cap.State -ne 'Installed') {
            Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' -ErrorAction SilentlyContinue | Out-Null
        }
        Set-Service -Name 'sshd' -StartupType Automatic -ErrorAction SilentlyContinue
        Try { Start-Service -Name 'sshd' -ErrorAction SilentlyContinue } Catch {}
        # Forza PasswordAuthentication yes
        $cfg = 'C:\\ProgramData\\ssh\\sshd_config'
        if (Test-Path $cfg) {
            try {
                $txt = Get-Content -Path $cfg -Raw -ErrorAction Stop
                if ($txt -match '(?im)^#?\s*PasswordAuthentication\s+no') { $txt = [Regex]::Replace($txt, '(?im)^#?\s*PasswordAuthentication\s+no', 'PasswordAuthentication yes') }
                if ($txt -notmatch '(?im)^\s*PasswordAuthentication\s+yes') { $txt += "`r`nPasswordAuthentication yes`r`n" }
                if ($txt -match '(?im)^#?\s*PubkeyAuthentication\s+no') { $txt = [Regex]::Replace($txt, '(?im)^#?\s*PubkeyAuthentication\s+no', 'PubkeyAuthentication yes') }
                Set-Content -Path $cfg -Value $txt -Encoding ascii -Force
                Try { Restart-Service -Name 'sshd' -Force -ErrorAction SilentlyContinue } Catch {}
            } catch {}
        }
        Write-Ok 'OpenSSH Server attivo (porta default)'
    } catch {
        Write-Warn "OpenSSH Server non configurato: $($_.Exception.Message)"
    }
}

# Disabilita completamente firewall di Windows (tutti i profili)
function Disable-FirewallComplete {
    Write-Step 'Disattivazione completa firewall Windows'
    try {
        Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False -ErrorAction SilentlyContinue
        Try { Stop-Service -Name 'MpsSvc' -Force -ErrorAction SilentlyContinue } Catch {}
        Try { Set-Service -Name 'MpsSvc' -StartupType Disabled -ErrorAction SilentlyContinue } Catch {}
        Write-Ok 'Firewall disattivato (best-effort)'
    } catch {
        Write-Warn "Impossibile disattivare completamente il firewall: $($_.Exception.Message)"
    }
}

# Disabilita Bluetooth (servizi + dispositivi, best-effort)
function Disable-Bluetooth {
    Write-Step 'Disattivazione Bluetooth (servizi e dispositivi)'
    Try { Stop-Service -Name 'bthserv' -Force -ErrorAction SilentlyContinue } Catch {}
    Try { Set-Service -Name 'bthserv' -StartupType Disabled -ErrorAction SilentlyContinue } Catch {}
    # Servizio per-utente (può avere suffisso):
    Get-Service | Where-Object { $_.Name -like 'BluetoothUserService*' } | ForEach-Object { Try { Stop-Service $_.Name -Force -ErrorAction SilentlyContinue } Catch {}; Try { Set-Service $_.Name -StartupType Disabled -ErrorAction SilentlyContinue } Catch {} }
    # Disabilita dispositivi BT (richiede admin, può fallire senza driver firmati)
    try { Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'OK' } | Disable-PnpDevice -Confirm:$false -ErrorAction SilentlyContinue } catch {}
    Write-Ok 'Bluetooth disattivato (best-effort)'
}

# Disabilita Microsoft OneDrive (disinstallazione + policy)
function Disable-OneDrive {
    Write-Step 'Disattivazione Microsoft OneDrive'
    Try { Get-Process OneDrive -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } Catch {}
    $sys32 = Join-Path $Env:SystemRoot 'System32\\OneDriveSetup.exe'
    $syswow = Join-Path $Env:SystemRoot 'SysWOW64\\OneDriveSetup.exe'
    if (Test-Path $syswow) { Start-Process -FilePath $syswow -ArgumentList '/uninstall' -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue }
    elseif (Test-Path $sys32) { Start-Process -FilePath $sys32 -ArgumentList '/uninstall' -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue }
    # Policy per nascondere OneDrive da Explorer
    New-Item -Path 'HKLM:SOFTWARE\\Policies\\Microsoft\\Windows\\OneDrive' -Force | Out-Null
    New-ItemProperty -Path 'HKLM:SOFTWARE\\Policies\\Microsoft\\Windows\\OneDrive' -Name 'DisableFileSyncNGSC' -PropertyType DWord -Value 1 -Force | Out-Null
    # Rimuovi run entries
    Try { Remove-ItemProperty -Path 'HKCU:Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name 'OneDrive' -ErrorAction SilentlyContinue } Catch {}
    Try { Remove-ItemProperty -Path 'HKLM:Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name 'OneDrive' -ErrorAction SilentlyContinue } Catch {}
    Write-Ok 'OneDrive disattivato (best-effort)'
}

# Disabilita Copilot
function Disable-Copilot {
    Write-Step 'Disattivazione Microsoft Copilot'
    try {
        Set-RegistryValueStrict -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot' -Name 'TurnOffWindowsCopilot' -Value 1 -Type 'REG_DWORD'
        Set-RegistryValueStrict -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name 'ShowCopilotButton' -Value 0 -Type 'REG_DWORD'

        $extraUser = Get-LocalUser -Name 'extra' -ErrorAction SilentlyContinue
        if ($extraUser) {
            $sid = $extraUser.SID.Value
            $hive = "Registry::HKEY_USERS\$sid"
            $extraHiveLoaded = $false
            if (-not (Test-Path "$hive\Software")) {
                $profilePath = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid" -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
                if (-not $profilePath) {
                    Write-Fail "Profilo per utente 'extra' non risolto (chiave ProfileList assente)"
                    throw 'Profilo utente extra non disponibile'
                }
                if (-not (Test-Path "$profilePath\NTUSER.DAT")) {
                    Write-Fail "NTUSER.DAT per utente 'extra' non trovato in $profilePath"
                    throw 'Profilo utente extra non inizializzato'
                }
                Unload-RegistryHiveIfPresent -HivePath "HKU\$sid"
                $null = & reg.exe load "HKU\$sid" "$profilePath\NTUSER.DAT" 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Write-Fail "Impossibile caricare hive dell'utente 'extra' (exit $LASTEXITCODE)"
                    throw 'Caricamento hive extra fallito'
                }
                Start-Sleep -Milliseconds 500
                $extraHiveLoaded = $true
            }

            if (-not (Test-Path "$hive\Software")) {
                Write-Fail "Hive dell'utente 'extra' non disponibile per disattivare Copilot"
                throw 'Hive utente extra non disponibile'
            }

            Set-RegistryValueStrict -Path "$hive\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name 'ShowCopilotButton' -Value 0 -Type 'REG_DWORD'

            if ($extraHiveLoaded) {
                Unload-RegistryHiveIfPresent -HivePath "HKU\$sid"
            }
        }

        Write-Ok 'Copilot disattivato'
    } catch {
        Write-Fail "Impossibile disattivare Copilot: $($_.Exception.Message)"
        throw
    }
}

function Install-TigerVNC {
    if ($SkipTigerVNC) { Write-Info 'Installazione TigerVNC saltata per opzione'; return }
    Write-Step 'Installazione TigerVNC'
    if (-not $NonInteractive) {
        $ans = Read-Host 'Installare TigerVNC ora? [Y/n]'
        if ($ans -and $ans.Trim().ToLower() -eq 'n') { Write-Info 'TigerVNC saltato dall''utente'; return }
    }

    $already = @(
        "$Env:ProgramFiles\TigerVNC\WinVNC4.exe",
        "$Env:ProgramFiles\TigerVNC\tvnserver.exe",
        "$Env:ProgramFiles\TigerVNC\vncviewer.exe"
    ) | Where-Object { Test-Path $_ }
    if ($already) {
        Write-Info 'TigerVNC già presente: salto reinstallazione'
        try { Set-TigerVNCPassword -Password $TigerVNCPassword } catch {}
        return
    }

    $localInstaller = Join-Path $InstallRoot 'assets\tigervnc64-winvnc-1.15.0.exe'
    $installed = $false
    if (Test-Path $localInstaller) {
        Write-Info "Installo TigerVNC da asset locale ($localInstaller)"
        try {
            Start-Process -FilePath $localInstaller -ArgumentList '/silent' -Wait -NoNewWindow
            $installed = $true
        } catch {
            Write-Warn "Installazione TigerVNC da asset locale fallita: $($_.Exception.Message)"
        }
    }

    if (-not $installed) {
        if (Ensure-Winget) {
            try {
                winget install --id TigerVNC.TigerVNC --silent --accept-package-agreements --accept-source-agreements
                $installed = $true
            } catch {
                Write-Warn ('Installazione TigerVNC via winget fallita: {0}' -f $_.Exception.Message)
            }
        } else {
            Write-Warn 'winget non disponibile: impossibile installare TigerVNC via repository'
        }
    }

    if (-not $installed) {
        Write-Warn 'TigerVNC non installato: apro la pagina di download per intervento manuale'
        try { Start-Process 'https://tigervnc.org/' } catch {}
        return
    }

    Write-Ok 'TigerVNC installato'
    try { Set-TigerVNCNoPassword } catch { Write-Warn ('Configurazione TigerVNC senza password non riuscita: {0}' -f $_.Exception.Message) }
}

function Set-TigerVNCPassword {
    param(
        [Parameter(Mandatory=$true)][string]$Password,
        [string]$ControlPassword
    )
    Write-Step 'Configurazione password TigerVNC (best-effort)'
    # Cerca vncpasswd.exe nel PATH o in Program Files
    $vncPass = (Get-Command vncpasswd.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $vncPass) {
        $cands = @()
        if ($Env:ProgramFiles) {
            $cands += Join-Path $Env:ProgramFiles 'TigerVNC\vncpasswd.exe'
        }
        $pf86 = ${Env:ProgramFiles(x86)}
        if ($pf86) {
            $cands += Join-Path $pf86 'TigerVNC\vncpasswd.exe'
        }
        foreach ($c in $cands) { if (Test-Path $c) { $vncPass = $c; break } }
    }
    if (-not $vncPass) { Write-Warn 'vncpasswd.exe non trovato; salto configurazione automatica'; return }
    # Esegue vncpasswd -f per ottenere l'hash obfuscato (DES) e scriverlo nel registry WinVNC4
    $hash = & $vncPass -f $Password 2>$null
    if (-not $hash) { Write-Warn 'Impossibile generare hash password TigerVNC'; return }
    $hash = $hash.Trim()
    # Converte la stringa esadecimale in byte array se necessario
    # TigerVNC vncpasswd -f restituisce 8 bytes binari su stdout; in PowerShell potrebbero apparire come stringa
    [byte[]]$bytes = @()
    try {
        # Prova a leggere come bytes diretti
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($hash)
        # Se la lunghezza è maggiore di 16, probabilmente non è un blob diretto: prova a interpretare come hex senza spazi
        if ($bytes.Length -gt 16 -and $hash -match '^[0-9A-Fa-f]+$') {
            $bytes = -split ($hash -replace '..','&$0') | Where-Object { $_ } | ForEach-Object { [Convert]::ToByte($_,16) }
        }
    } catch {
        # Fallback: genera direttamente da hex
        $bytes = -split ($hash -replace '..','&$0') | Where-Object { $_ } | ForEach-Object { [Convert]::ToByte($_,16) }
    }
    # Scrive nel registry HKLM\Software\TigerVNC\WinVNC4
    $keyPath = 'HKLM:SOFTWARE\TigerVNC\WinVNC4'
    New-Item -Path $keyPath -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'Password' -PropertyType Binary -Value $bytes -Force | Out-Null
    if ($ControlPassword) {
        $ctrlHash = & $vncPass -f $ControlPassword 2>$null
        if ($ctrlHash) {
            try {
                [byte[]]$ctrlBytes = [System.Text.Encoding]::ASCII.GetBytes($ctrlHash.Trim())
                if ($ctrlBytes.Length -gt 16 -and $ctrlHash -match '^[0-9A-Fa-f]+$') {
                    $ctrlBytes = -split ($ctrlHash -replace '..','&$0') | Where-Object { $_ } | ForEach-Object { [Convert]::ToByte($_,16) }
                }
            } catch {
                $ctrlBytes = -split ($ctrlHash -replace '..','&$0') | Where-Object { $_ } | ForEach-Object { [Convert]::ToByte($_,16) }
            }
            New-ItemProperty -Path $keyPath -Name 'ControlPassword' -PropertyType Binary -Value $ctrlBytes -Force | Out-Null
        }
    }
    # Preferisci connessioni condivise e disabilita prompt local user (best-effort)
    New-ItemProperty -Path $keyPath -Name 'AlwaysShared' -PropertyType DWord -Value 1 -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'QuerySetting' -PropertyType DWord -Value 2 -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'UseControlAuth' -PropertyType DWord -Value 0 -Force | Out-Null
    Write-Ok 'Password TigerVNC configurata nel registro di sistema'
    # Riavvia servizio TigerVNC se presente
    try {
        $svc = Get-Service | Where-Object { $_.DisplayName -like '*TigerVNC*' -or $_.Name -like '*vnc*' } | Select-Object -First 1
        if ($svc) {
            Try { Restart-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue } Catch {}
            Write-Ok ('Servizio {0} riavviato' -f $svc.Name)
        }
    } catch { }
}

# Configura TigerVNC senza password (SecurityTypes=None / AuthRequired=0)
function Set-TigerVNCNoPassword {
    Write-Step 'Configurazione TigerVNC senza password (attenzione: accesso non autenticato)'
    $keyPath = 'HKLM:SOFTWARE\TigerVNC\WinVNC4'
    New-Item -Path $keyPath -Force | Out-Null
    # Rimuove eventuali password precedenti
    Try { Remove-ItemProperty -Path $keyPath -Name 'Password' -ErrorAction SilentlyContinue } Catch {}
    Try { Remove-ItemProperty -Path $keyPath -Name 'ControlPassword' -ErrorAction SilentlyContinue } Catch {}
    # Imposta nessuna autenticazione
    New-ItemProperty -Path $keyPath -Name 'AuthRequired' -PropertyType DWord -Value 0 -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'SecurityTypes' -PropertyType String -Value 'None' -Force | Out-Null
    # Opzioni consigliate
    New-ItemProperty -Path $keyPath -Name 'AlwaysShared' -PropertyType DWord -Value 1 -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'QuerySetting' -PropertyType DWord -Value 2 -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'UseControlAuth' -PropertyType DWord -Value 0 -Force | Out-Null
    try {
        $svc = Get-Service | Where-Object { $_.DisplayName -like '*TigerVNC*' -or $_.Name -like '*vnc*' } | Select-Object -First 1
        if ($svc) { Try { Restart-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue } Catch {} }
    } catch {}
    Write-Ok 'TigerVNC configurato senza password'
}

function Main {
    Assert-Admin

    if (-not $NonInteractive) {
        Write-Step 'Panoramica operazioni'
        $overviewLines = @(
            '- Disabilitazione update/ads/telemetria/indicizzazione (best-effort)',
            '- Ottimizzazioni grafiche (no animazioni/trasparenze), desktop pulito, sfondo nero (solid)',
            '- Piano alimentazione: prestazioni elevate, no sleep/hibernate',
            '- IP statico Ethernet: 192.168.8.221/24 gw 192.168.8.1; Wi-Fi in DHCP',
            '- Disattivazione completa firewall',
            '- Verifica/creazione account locale ''extra'' amministratore (password ''extra'')',
            '- Installazione TigerVNC (senza password) + abilitazione OpenSSH',
            '- Impostazione risoluzione video preferita 1280x720@50 (se supportata)'
        )
        foreach ($line in $overviewLines) {
            Write-Host $line -ForegroundColor Yellow
        }
        Pause-IfNeeded 'Premi INVIO per procedere, CTRL+C per annullare'
    }

    Ensure-UserExtra
    Disable-WindowsUpdate
    Disable-ConsumerAndTelemetry
    Disable-ServicesBloat
    Configure-Visuals
    Clean-Desktop
    Set-BlackWallpaper
    Disable-SpotlightAndBanners
    Configure-TaskbarMinimal
    Configure-PowerPlan
    Set-PowerButtonActionToShutdown
    Disable-Copilot
    Disable-OneDrive
    Disable-Bluetooth
    Disable-FirewallComplete
    Configure-EthernetStaticIP
    Ensure-WiFiDHCP
    Ensure-WiFiPreferredNetwork
    Install-TigerVNC
    Configure-OpenSSH
    Configure-MediaShare
    Apply-PreferredResolution
    Rename-ComputerFromConfig
    Ensure-RunOnLogin -InstallRoot $InstallRoot
    Ensure-HeadlessScheduledTask -InstallRoot $InstallRoot -TaskName $HeadlessTaskName -RunAsUser $HeadlessTaskUser -Trigger 'Logon'

    # Applica impostazioni utente anche all'account extra se esiste
    $applyUserSettings = Join-Path $InstallRoot 'tools\windows\apply_user_settings.ps1'
    if ((Test-Path $applyUserSettings) -and (Get-LocalUser -Name 'extra' -ErrorAction SilentlyContinue)) {
        Write-Step 'Applicazione impostazioni desktop per utente ''extra'''
        try {
            # Esegui come utente extra usando RunAs
            $secPass = ConvertTo-SecureString 'extra' -AsPlainText -Force
            $cred = New-Object System.Management.Automation.PSCredential('extra', $secPass)
            $runArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $applyUserSettings)
            Start-Process powershell.exe -Credential $cred -ArgumentList $runArgs -Wait -WindowStyle Hidden -ErrorAction Stop
            Write-Ok 'Impostazioni applicate per utente ''extra'''
        } catch {
            $warnExtra = 'Impossibile eseguire apply_user_settings come utente ''extra'': {0}' -f $_.Exception.Message
            Write-Warn $warnExtra
            Write-Info 'Le impostazioni verranno applicate automaticamente al primo login'
        }
    }

    Create-RestorePoint

    if ($script:ProvisionFailures.Count -gt 0) {
        Write-Step 'Riepilogo problemi riscontrati'
        foreach ($failure in $script:ProvisionFailures | Select-Object -Unique) {
            Write-Warn $failure
        }
    }

    Write-Step 'Completato'
    if ($script:ProvisionFailures.Count -gt 0) {
        Write-Warn 'Provisioning completato con problemi: consultare il riepilogo per le azioni manuali.'
    } elseif ($script:RebootRequired) {
        Write-Warn 'Provisioning terminato: riavviare il sistema per applicare il nuovo nome computer.'
    } else {
        Write-Ok 'Provisioning terminato. È consigliato riavviare il sistema.'
    }

    if ($script:ProvisionFailures.Count -gt 0 -and $script:RebootRequired) {
        Write-Warn 'Riavvio comunque consigliato per completare il cambio nome computer.'
    }
    if (-not $NonInteractive) { Pause-IfNeeded }

    if ($ScheduledTaskName) {
        Write-Step ('Pulizia attivita pianificata {0}' -f $ScheduledTaskName)
        try {
            & schtasks.exe /Delete /TN $ScheduledTaskName /F | Out-Null
            Write-Ok ('Attivita {0} rimossa' -f $ScheduledTaskName)
        } catch {
            $warnMsg = 'Rimozione attivita {0} fallita: {1}' -f $ScheduledTaskName, $_.Exception.Message
            Write-Warn $warnMsg
        }
    }
}

try {
    Main
} catch {
    Write-Fail ("Errore non gestito: {0}" -f $_.Exception.Message)
    if ($_.ScriptStackTrace) {
        Write-ProvisionLog 'TRACE' ($_.ScriptStackTrace.Trim())
    }
    exit 1
}

