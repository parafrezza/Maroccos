[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\Program Files\marocco-player',
    [string]$TaskName = 'MaroccosHeadless',
    [ValidateSet('Logon','Startup')]
    [string]$Trigger = 'Logon',
    [int]$DelaySeconds = 15,
    [string]$RunAsUser = 'extra'
)

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error 'Esegui questo script da una sessione PowerShell "Esegui come amministratore".'
        exit 2
    }
}

try {
    Assert-Admin

    $exePath = Join-Path $InstallRoot 'headless-player\headless-player.exe'
    if (-not (Test-Path $exePath)) {
        Write-Error ("headless-player.exe non trovato in {0}" -f $exePath)
        exit 3
    }

    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    $helperPath = Join-Path $scriptRoot 'headless_task_helpers.ps1'
    if (-not (Test-Path $helperPath)) {
        Write-Error ("Helper non trovato: {0}" -f $helperPath)
        exit 4
    }
    . $helperPath

    $task = Register-HeadlessAutostartTask -InstallRoot $InstallRoot -TaskName $TaskName -Trigger $Trigger -DelaySeconds $DelaySeconds -RunAsUser $RunAsUser

    $targetUser = if ($Trigger -eq 'Startup') { 'SYSTEM' } else { $RunAsUser }
    Write-Host ("[OK]  Task '{0}' registrata (trigger {1}, delay {2}s, utente {3})" -f $TaskName, $Trigger.ToUpper(), $DelaySeconds, $targetUser)
    Write-Host ("Per testare subito: Start-ScheduledTask -TaskName '{0}'" -f $TaskName)
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
