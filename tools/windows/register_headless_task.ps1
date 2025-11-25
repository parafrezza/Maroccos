[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\Program Files\marocco-player',
    [string]$TaskName = 'MaroccosHeadless',
    [ValidateSet('Logon','Startup')]
    [string]$Trigger = 'Logon',
    [int]$DelaySeconds = 15
)

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error 'Esegui questo script da una sessione PowerShell "Esegui come amministratore".'
        exit 2
    }
}

function Format-Delay([int]$Seconds) {
    if ($Seconds -lt 0) { $Seconds = 0 }
    $totalMinutes = [math]::Floor($Seconds / 60)
    $remainSeconds = $Seconds % 60
    if ($totalMinutes -gt 9999) { $totalMinutes = 9999 }
    return ('{0:0000}:{1:00}' -f $totalMinutes, $remainSeconds)
}

function Ensure-Wrapper([string]$ExePath, [int]$DelaySeconds) {
    $workDir = Split-Path $ExePath -Parent
    $cmdPath = Join-Path $workDir 'headless-autostart.cmd'
    if ($DelaySeconds -lt 0) { $DelaySeconds = 0 }
    $content = @"
@echo off
timeout /T $DelaySeconds /NOBREAK >NUL 2>&1
pushd "$workDir"
start "" "$ExePath"
popd
"@
    Set-Content -Path $cmdPath -Value $content -Encoding ASCII -Force
    return $cmdPath
}

try {
    Assert-Admin

    $exePath = Join-Path $InstallRoot 'headless-player\headless-player.exe'
    if (-not (Test-Path $exePath)) {
        Write-Error ("headless-player.exe non trovato in {0}" -f $exePath)
        exit 3
    }

    $wrapperCmd = Ensure-Wrapper -ExePath $exePath -DelaySeconds $DelaySeconds
    $scheduleType = if ($Trigger -eq 'Startup') { 'ONSTART' } else { 'ONLOGON' }
    $delayArg = Format-Delay -Seconds $DelaySeconds

    try {
        & schtasks.exe /Delete /TN $TaskName /F 2>&1 | Out-Null
    } catch {}

    $trCommand = '"' + ($wrapperCmd -replace '"','""') + '"'
    $args = @(
        '/Create',
        '/TN', $TaskName,
        '/TR', $trCommand,
        '/SC', $scheduleType,
        '/DELAY', $delayArg,
        '/RL', 'HIGHEST',
        '/F',
        '/RU', 'SYSTEM'
    )

    $output = & schtasks.exe @args 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $joined = if ($output) { $output -join ' ' } else { 'nessun output' }
        Write-Error ("Impossibile creare la task {0} (exit {1}): {2}" -f $TaskName, $exitCode, $joined)
        exit $exitCode
    }

    Write-Host ("[OK]  Task '{0}' registrata (trigger {1}, delay {2}s)" -f $TaskName, $scheduleType, $DelaySeconds)
    Write-Host ("Per testare subito: Start-ScheduledTask -TaskName '{0}'" -f $TaskName)
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
