param(
    [switch]$Quiet,
    [int]$MaxRetries = 15,
    [int]$SleepMillis = 400,
    [switch]$DisableAutostart,
    [string]$HeadlessBaseUrl = 'http://127.0.0.1:8080',
    [switch]$NoElevate
)
$ErrorActionPreference = 'Stop'

function Write-Info {
    param([string]$Message)
    if (-not $Quiet) { Write-Host $Message }
}

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

if (-not (Test-IsAdmin)) {
    if (-not $NoElevate) {
        Write-Info '[OFF-KILL] Richiesta elevazione privilegi...'
        $psi = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" `"-Quiet:$Quiet`" `"-MaxRetries:$MaxRetries`" `"-SleepMillis:$SleepMillis`" `"-DisableAutostart:$DisableAutostart`" `"-HeadlessBaseUrl:$HeadlessBaseUrl`" -NoElevate"
        Start-Process -FilePath powershell -Verb RunAs -ArgumentList $psi | Out-Null
        exit 0
    } else {
        Write-Warning '[OFF-KILL] Non in esecuzione come amministratore; alcune terminazioni potrebbero fallire.'
    }
}

function New-ProcRecord {
    param([int]$Id, [string]$Name, [string]$CommandLine, [string]$Source)
    return [pscustomobject]@{
        Id = $Id
        Name = $Name
        CommandLine = $CommandLine
        Source = $Source
    }
}

function Get-TargetProcesses {
    $records = @()
    $namePatterns = @('OFF-player', 'headless-player')

    foreach ($pattern in $namePatterns) {
        try {
            $records += Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.ProcessName -like "$pattern*"
            } | ForEach-Object {
                New-ProcRecord -Id $_.Id -Name $_.ProcessName -CommandLine $null -Source 'ProcessName'
            }
        }
        catch {
            # ignore enumeration errors
        }
    }

    try {
        $candidates = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.CommandLine -and ($_.CommandLine -match 'headless-player' -or $_.CommandLine -match 'OFF-player')
        }
        foreach ($proc in $candidates) {
            if ($records | Where-Object { $_.Id -eq $proc.ProcessId }) { continue }
            $cmd = $proc.CommandLine
            if ($cmd -and $cmd.Length -gt 256) { $cmd = $cmd.Substring(0, 252) + ' ...' }
            $records += New-ProcRecord -Id $proc.ProcessId -Name $proc.Name -CommandLine $cmd -Source 'CommandLine'
        }
    }
    catch {
        # WMI failure: ignore and proceed with name-based detection
    }

    return $records | Sort-Object Id -Unique
}

function Stop-TargetProcess {
    param([pscustomobject]$Proc)
    $label = "PID={0} Name={1}" -f $Proc.Id, $Proc.Name
    if ($Proc.CommandLine) {
        $label = $label + " Cmd=" + $Proc.CommandLine
    }
    Write-Info ("[OFF-KILL] Stop " + $label)
    $stopped = $false
    try {
        Stop-Process -Id $Proc.Id -Force -ErrorAction Stop
        $stopped = $true
    }
    catch {
        Write-Warning ("[OFF-KILL] Stop-Process fallita per PID {0}: {1}" -f $Proc.Id, $_.Exception.Message)
    }
    if (-not $stopped) {
        try {
            $null = taskkill /PID $Proc.Id /F /T 2>$null
            if ($LASTEXITCODE -eq 0) { $stopped = $true }
        }
        catch {
            Write-Warning ("[OFF-KILL] taskkill fallita per PID {0}: {1}" -f $Proc.Id, $_.Exception.Message)
        }
    }
    return $stopped
}

$attempt = 0
$lastRemaining = @()

while ($true) {
    if ($DisableAutostart) {
        try { Invoke-RestMethod -Method Post -Uri "$HeadlessBaseUrl/off/autostart" -Body (@{ enabled = $false } | ConvertTo-Json) -ContentType 'application/json' -TimeoutSec 2 | Out-Null } catch {}
        try { Invoke-RestMethod -Method Post -Uri "$HeadlessBaseUrl/off/process/stop" -TimeoutSec 2 | Out-Null } catch {}
    }

    # Fast path: kill by image names to reduce respawn races
    foreach ($img in @('OFF-player.exe','OFF-player_debug.exe','headless-player.exe')) { try { $null = taskkill /IM $img /F /T 2>$null } catch {} }

    $targets = Get-TargetProcesses
    if (-not $targets -or $targets.Count -eq 0) {
        Write-Info '[OFF-KILL] Nessun processo residuo trovato'
        exit 0
    }

    $attempt++
    $stubborn = @()
    foreach ($p in $targets) {
        if (-not (Stop-TargetProcess -Proc $p)) {
            $stubborn += $p.Id
        }
    }

    Start-Sleep -Milliseconds $SleepMillis
    $remaining = Get-TargetProcesses

    if (-not $remaining -or $remaining.Count -eq 0) {
        Write-Info '[OFF-KILL] Tutti i processi terminati'
        exit 0
    }

    $remainingIds = $remaining | ForEach-Object { $_.Id } | Sort-Object

    if ($stubborn.Count -gt 0) {
        Write-Warning ("[OFF-KILL] Persistono PID: {0}" -f ($remainingIds -join ', '))
    }

    if ($attempt -ge $MaxRetries) {
        Write-Warning '[OFF-KILL] Raggiunto limite tentativi: alcuni processi restano attivi.'
        exit 1
    }

    if ($lastRemaining -and (@($lastRemaining) -join ',') -eq ($remainingIds -join ',')) {
        Write-Info '[OFF-KILL] Stessi processi ancora attivi, ritento...'
    }
    $lastRemaining = $remainingIds
}
