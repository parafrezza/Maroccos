# Script per verificare lo stato dell'autopartenza di headless-player su Windows
# Controlla se l'attività pianificata "MaroccosHeadless" esiste ed è configurata correttamente

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

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

Write-Step 'Verifica autopartenza headless-player'

# 1. Verifica attività pianificata
$taskName = 'MaroccosHeadless'
try {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Write-Ok "Attività pianificata '$taskName' trovata"
    
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    if ($taskInfo) {
        Write-Info "Stato: $($task.State)"
        Write-Info "Ultimo avvio: $($taskInfo.LastRunTime)"
        Write-Info "Prossimo avvio: $($taskInfo.NextRunTime)"
        Write-Info "Ultimo risultato: $($taskInfo.LastTaskResult) $(if ($taskInfo.LastTaskResult -eq 0) { '(Successo)' } else { '(Errore)' })"
    }
    
    # Mostra i dettagli dell'azione
    $action = $task.Actions | Select-Object -First 1
    Write-Info "Eseguibile: $($action.Execute)"
    if ($action.Arguments) {
        Write-Info "Argomenti: $($action.Arguments)"
    }
    Write-Info "Directory di lavoro: $($action.WorkingDirectory)"
    
    # Mostra i trigger
    foreach ($trigger in $task.Triggers) {
        Write-Info "Trigger: $($trigger.CimClass.CimClassName)"
        if ($trigger.UserId) {
            Write-Info "  Utente: $($trigger.UserId)"
        }
    }
    
    # Verifica se l'eseguibile esiste
    $exePath = $action.Execute
    if ($exePath -and (Test-Path $exePath)) {
        Write-Ok "Eseguibile trovato: $exePath"
    } elseif ($exePath) {
        Write-Warn "Eseguibile NON trovato: $exePath"
        Write-Warn "L'attività pianificata non funzionerà finché l'eseguibile non viene installato"
    }
    
} catch {
    Write-Warn "Attività pianificata '$taskName' NON trovata"
    Write-Info "L'autopartenza di headless-player non è configurata"
    Write-Info ""
    Write-Info "Per configurarla, esegui l'installer con l'opzione 'Avvia headless all'avvio'"
    Write-Info "oppure crea manualmente l'attività con:"
    Write-Info "  schtasks.exe /Create /TN ""MaroccosHeadless"" /SC ONLOGON /RL HIGHEST /F /TR ""C:\Program Files\marocco-player\headless-player\headless-player.exe"""
}

# 2. Verifica variabile ambiente OFF_AUTOSTART
Write-Step 'Verifica variabile OFF_AUTOSTART'
$offAutostart = [Environment]::GetEnvironmentVariable('OFF_AUTOSTART', 'User')
if ($offAutostart -eq '1') {
    Write-Ok "OFF_AUTOSTART=1 (configurato correttamente)"
} elseif ($offAutostart) {
    Write-Warn "OFF_AUTOSTART=$offAutostart (valore inatteso, dovrebbe essere 1)"
} else {
    Write-Info "OFF_AUTOSTART non impostato (opzionale)"
}

# 3. Verifica auto-login
Write-Step 'Verifica configurazione auto-login'
try {
    $winlogonPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    $autoAdminLogon = Get-ItemProperty -Path $winlogonPath -Name 'AutoAdminLogon' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty AutoAdminLogon
    $defaultUserName = Get-ItemProperty -Path $winlogonPath -Name 'DefaultUserName' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty DefaultUserName
    
    if ($autoAdminLogon -eq '1') {
        Write-Ok "Auto-login abilitato per utente: $defaultUserName"
    } else {
        Write-Info "Auto-login non configurato (richiede login manuale)"
    }
} catch {
    Write-Info "Impossibile verificare configurazione auto-login"
}

# 4. Verifica utente 'extra'
Write-Step 'Verifica utente amministratore extra'
try {
    $extraUser = Get-LocalUser -Name 'extra' -ErrorAction Stop
    Write-Ok "Utente 'extra' presente"
    
    $isAdmin = Get-LocalGroupMember -Group 'Administrators' -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*\extra' }
    if ($isAdmin) {
        Write-Ok "Utente 'extra' è amministratore"
    } else {
        Write-Warn "Utente 'extra' NON è amministratore"
    }
} catch {
    Write-Warn "Utente 'extra' NON trovato"
    Write-Info "Per crearlo, esegui l'installer o usa:"
    Write-Info '  $sec = ConvertTo-SecureString "extra" -AsPlainText -Force'
    Write-Info '  New-LocalUser -Name "extra" -Password $sec -FullName "Extra Admin" -PasswordNeverExpires:$true'
    Write-Info '  Add-LocalGroupMember -Group "Administrators" -Member "extra"'
}

Write-Host ""
Write-Step 'Riepilogo'
Write-Host "Per configurare completamente l'autopartenza:"
Write-Host "1. Assicurati che headless-player.exe sia installato in C:\Program Files\marocco-player\headless-player\"
Write-Host "2. Esegui l'installer con l'opzione 'Avvia headless all'avvio'"
Write-Host "3. Opzionalmente, abilita 'auto logon con utente extra' per avvio automatico senza login manuale"
Write-Host ""
