# Script per riparare o registrare l'attività pianificata MaroccosHeadless
# Usa il modulo ScheduledTasks per definire correttamente il path (niente più /TR spezzato)

[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\Program Files\marocco-player',
    [string]$TaskName = 'MaroccosHeadless',
    [string]$RunAsUser,
    [string]$RunAsPassword,
    [ValidateSet('Logon','Startup')]
    [string]$Trigger = 'Logon',
    [switch]$RunAsSystem
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}
function Write-Ok([string]$Message) {
    Write-Host "[OK]  $Message" -ForegroundColor Green
}
function Write-Warn([string]$Message) {
    Write-Warning $Message
}
function Write-Info([string]$Message) {
    Write-Host "[INFO] $Message" -ForegroundColor Gray
}

# Verifica privilegi amministrativi
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Esegui questo script come Amministratore."
    exit 1
}

Write-Step "Riparazione attività pianificata $TaskName ($Trigger)"

$exePath = Join-Path $InstallRoot 'headless-player\headless-player.exe'
$workDir = Split-Path $exePath -Parent

if (-not (Test-Path $exePath)) {
    Write-Warn "Eseguibile non trovato: $exePath"
    Write-Info 'Verifica che headless-player sia installato correttamente'
    exit 2
}

Write-Ok "Eseguibile trovato: $exePath"

try {
    Import-Module ScheduledTasks -ErrorAction Stop | Out-Null
} catch {
    Write-Error "Modulo ScheduledTasks non disponibile: $($_.Exception.Message)"
    exit 3
}

# Rimuovi attività esistente
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Info 'Rimozione attività esistente...'
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok 'Attività esistente rimossa'
    }
} catch {
    Write-Warn "Impossibile rimuovere l'attività corrente: $($_.Exception.Message)"
}

$action = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $workDir

$principalUser = $RunAsUser
if ($RunAsSystem) {
    $principalUser = 'SYSTEM'
} elseif (-not $principalUser) {
    $principalUser = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
} elseif ($principalUser -notmatch '\\|@') {
    $principalUser = "${env:COMPUTERNAME}\$principalUser"
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

switch ($Trigger) {
    'Startup' { $trigger = New-ScheduledTaskTrigger -AtStartup }
    default   {
        if ($principalUser -and -not $RunAsSystem) {
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $principalUser
        } else {
            $trigger = New-ScheduledTaskTrigger -AtLogOn
        }
    }
}

$logonType = 'Interactive'
if ($RunAsSystem) {
    $logonType = 'ServiceAccount'
} elseif ($RunAsPassword) {
    $logonType = 'Password'
}

if ($Trigger -eq 'Startup' -and -not $RunAsSystem -and -not $RunAsPassword) {
    Write-Warn 'AtStartup richiede credenziali memorizzate o esecuzione come SYSTEM; uso account SYSTEM.'
    $principalUser = 'SYSTEM'
    $logonType = 'ServiceAccount'
    $RunAsSystem = $true
}

$principal = New-ScheduledTaskPrincipal -UserId $principalUser -LogonType $logonType -RunLevel Highest

Write-Info "Registrazione nuova attività (utente: $principalUser, logonType: $logonType)"

$taskDefinition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal

try {
    if ($RunAsPassword -and -not $RunAsSystem) {
        Register-ScheduledTask -TaskName $TaskName -InputObject $taskDefinition -Force -User $principalUser -Password $RunAsPassword | Out-Null
    } else {
        Register-ScheduledTask -TaskName $TaskName -InputObject $taskDefinition -Force | Out-Null
    }
    Write-Ok 'Attività pianificata creata con successo'
} catch {
    Write-Error "Registrazione attività fallita: $($_.Exception.Message)"
    exit 4
}

# Verifica finale
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $action = $task.Actions | Select-Object -First 1
    Write-Info 'Verifica configurazione:'
    Write-Info "  Eseguibile: $($action.Execute)"
    if ($action.WorkingDirectory) { Write-Info "  WorkingDir: $($action.WorkingDirectory)" }
    if ($action.Arguments) { Write-Info "  Argomenti: $($action.Arguments)" }

    if ($action.Execute -ieq $exePath) {
        Write-Ok 'Path configurato correttamente'
    } else {
        Write-Warn 'Il path non coincide con il percorso previsto'
    }
} catch {
    Write-Warn "Impossibile verificare l'attività: $($_.Exception.Message)"
}

Write-Step 'Completato'
Write-Ok "L'attività $TaskName è stata riparata"
Write-Info "Trigger $Trigger avvierà headless-player.exe automaticamente"
Write-Host ''
Write-Info "Per testare subito, esegui: Start-ScheduledTask -TaskName $TaskName"
