# Helper functions shared by provisioning scripts to manage the headless-player autostart task

if (-not (Get-Module -Name ScheduledTasks -ListAvailable)) {
    throw 'Modulo ScheduledTasks non disponibile su questo sistema.'
}

if (-not (Get-Module -Name ScheduledTasks)) {
    Import-Module ScheduledTasks -ErrorAction Stop
}

function Get-HeadlessInstallPaths {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot
    )

    $normalizedRoot = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
        'C:\\Program Files\\marocco-player'
    } else {
        $InstallRoot
    }

    $headlessDir = Join-Path $normalizedRoot 'headless-player'
    return [pscustomobject]@{
        InstallRoot = $normalizedRoot
        HeadlessDir = $headlessDir
        ExePath     = Join-Path $headlessDir 'headless-player.exe'
        CmdPath     = Join-Path $headlessDir 'headless-autostart.cmd'
        LogPath     = Join-Path $headlessDir 'marocco_crash_log.txt'
    }
}

function Set-HeadlessAutostartScript {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [int]$DelaySeconds = 15,
        [string]$TemplatePath
    )

    $paths = Get-HeadlessInstallPaths -InstallRoot $InstallRoot
    if (-not (Test-Path $paths.ExePath)) {
        throw "headless-player.exe non trovato in ${($paths.ExePath)}"
    }
    if (-not (Test-Path $paths.HeadlessDir)) {
        New-Item -ItemType Directory -Path $paths.HeadlessDir -Force | Out-Null
    }

    $delay = [Math]::Max(0, [int]$DelaySeconds)
    $templateToUse = $TemplatePath
    if (-not $templateToUse -and $PSScriptRoot) {
        $templateCandidate = Join-Path $PSScriptRoot 'idealTask\headless-autostart.cmd'
        if (Test-Path $templateCandidate) {
            $templateToUse = $templateCandidate
        }
    }

    if ($templateToUse -and (Test-Path $templateToUse)) {
        try {
            $raw = Get-Content -Path $templateToUse -Raw -ErrorAction Stop
            $normalized = $raw.Replace('C:\Program Files\marocco-player', $paths.InstallRoot)
            if ($delay -ne 15) {
                $normalized = [regex]::Replace($normalized, 'timeout\s+/T\s+\d+', "timeout /T $delay", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
            }
            Set-Content -Path $paths.CmdPath -Value $normalized -Encoding ASCII -Force
            return $paths.CmdPath
        } catch {
            Write-Warning ("Impossibile usare il template $templateToUse per headless-autostart: {0}" -f $_.Exception.Message)
        }
    }

    $content = @"
@echo off
set LOGFILE="{0}"

timeout /T {1} /NOBREAK >NUL 2>&1

pushd "{2}"
start "" "{3}" > %LOGFILE% 2>&1
echo.
echo Codice di uscita (ERRORLEVEL): %ERRORLEVEL% >> %LOGFILE%
popd
"@ -f $paths.LogPath, $delay, $paths.InstallRoot, $paths.ExePath

    Set-Content -Path $paths.CmdPath -Value $content -Encoding ASCII -Force
    return $paths.CmdPath
}

function Register-HeadlessAutostartTask {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [string]$TaskName = 'MaroccosHeadless',
        [ValidateSet('Logon','Startup')][string]$Trigger = 'Logon',
        [int]$DelaySeconds = 15,
        [string]$RunAsUser = 'extra',
        [string]$Description = 'Maroccos headless-player autostart',
        [string]$ScriptTemplatePath,
        [string]$TaskTemplatePath
    )

    if (-not $ScriptTemplatePath -and $PSScriptRoot) {
        $ScriptTemplatePath = Join-Path $PSScriptRoot 'idealTask\headless-autostart.cmd'
    }
    if (-not $TaskTemplatePath -and $PSScriptRoot) {
        $TaskTemplatePath = Join-Path $PSScriptRoot 'idealTask\MaroccosHeadless_ideal_task.xml'
    }

    $cmdPath = Set-HeadlessAutostartScript -InstallRoot $InstallRoot -DelaySeconds $DelaySeconds -TemplatePath $ScriptTemplatePath
    $workingDir = Split-Path $cmdPath -Parent
    $delay = [Math]::Max(0, [int]$DelaySeconds)
    $delayIso = [System.Xml.XmlConvert]::ToString([TimeSpan]::FromSeconds($delay))
    $userAccount = if ($RunAsUser -match '^[^\\]+\\[^\\]+$') { $RunAsUser } else { "${env:COMPUTERNAME}\\$RunAsUser" }
    $userSid = $null
    try {
        $userSid = ([System.Security.Principal.NTAccount]$userAccount).Translate([System.Security.Principal.SecurityIdentifier]).Value
    } catch {}

    if ($TaskTemplatePath -and (Test-Path $TaskTemplatePath)) {
        try {
            [xml]$taskXml = Get-Content -Path $TaskTemplatePath -Raw -ErrorAction Stop
            if ($taskXml.Task.Principals.Principal) {
                if ($userSid) { $taskXml.Task.Principals.Principal.UserId = $userSid }
                if ($taskXml.Task.Principals.Principal.LogonType) { $taskXml.Task.Principals.Principal.LogonType = 'InteractiveToken' }
            }
            if ($taskXml.Task.Triggers.LogonTrigger) {
                $taskXml.Task.Triggers.LogonTrigger.UserId = $userAccount
                $taskXml.Task.Triggers.LogonTrigger.Delay = $delayIso
            }
            if ($taskXml.Task.Actions.Exec) {
                $taskXml.Task.Actions.Exec.Command = '"' + $cmdPath + '"'
                $taskXml.Task.Actions.Exec.WorkingDirectory = $workingDir
            }
            if ($taskXml.Task.RegistrationInfo -and $taskXml.Task.RegistrationInfo.Author) {
                $taskXml.Task.RegistrationInfo.Author = $userAccount
            }
            Register-ScheduledTask -TaskName $TaskName -Xml $taskXml.OuterXml -Force | Out-Null
            return
        } catch {
            # fallback to manual registration below
        }
    }

    if ($Trigger -eq 'Startup') {
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $triggerObj = New-ScheduledTaskTrigger -AtStartup
    } else {
        if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
            throw 'RunAsUser obbligatorio per task con trigger Logon'
        }
        $principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType Interactive -RunLevel Highest
        $triggerObj = New-ScheduledTaskTrigger -AtLogOn -User $RunAsUser
    }
    $triggerObj.Delay = $delayIso

    $action = New-ScheduledTaskAction -Execute $cmdPath -WorkingDirectory $workingDir

    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -DisallowStartIfOnBatteries -StopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Priority 7
    $settings.UseUnifiedSchedulingEngine = $true
    $settings.RunOnlyIfNetworkAvailable = $false
    $settings.StartWhenAvailable = $false
    $settings.WakeToRun = $false
    $settings.Hidden = $false
    $settings.RunOnlyIfIdle = $false
    $settings.AllowStartOnDemand = $true
    if ($settings.IdleSettings) {
        $settings.IdleSettings.StopOnIdleEnd = $true
        $settings.IdleSettings.RestartOnIdle = $false
    }

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggerObj -Principal $principal -Settings $settings -Description $Description -Force
}
