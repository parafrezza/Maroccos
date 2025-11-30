# Script per verificare lo stato dell'autopartenza di headless-player su Windows
# Controlla se l'attività pianificata "MaroccosHeadless" esiste ed è configurata correttamente

[CmdletBinding()]

    } catch {
        if ($taskDef.Required) {
            Write-Warn "Attività pianificata '$taskName' NON trovata"
            Write-Info "Per configurarla puoi eseguire:"
            Write-Info "  powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\Program Files\marocco-player\tools\windows\register_headless_task.ps1'"
            Write-Info "oppure creare manualmente l'attività con:"
            Write-Info "  schtasks.exe /Create /TN ""MaroccosHeadless"" /SC ONLOGON /RL HIGHEST /F /DELAY 0000:15 /RU extra /TR ""C:\Program Files\marocco-player\headless-player\headless-autostart.cmd"""
        } else {
            Write-Info "Attività opzionale '$taskName' non presente (avvio al boot non configurato)"
        }
    }
    @{ Name = 'MaroccosHeadlessBoot'; Required = $false }
)
foreach ($taskDef in $taskDefinitions) {
    $taskName = $taskDef.Name
    Write-Info "--- Attività '$taskName' ---"
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

        $action = $task.Actions | Select-Object -First 1
        Write-Info "Eseguibile: $($action.Execute)"
        if ($action.Arguments) { Write-Info "Argomenti: $($action.Arguments)" }
        Write-Info "Directory di lavoro: $($action.WorkingDirectory)"

        foreach ($trigger in $task.Triggers) {
            Write-Info "Trigger: $($trigger.CimClass.CimClassName)"
            if ($trigger.UserId) { Write-Info "  Utente: $($trigger.UserId)" }
        }

        $exePath = $action.Execute
        if ($exePath -and (Test-Path $exePath)) {
            Write-Ok "Eseguibile trovato: $exePath"
        } elseif ($exePath) {
            Write-Warn "Eseguibile NON trovato: $exePath"
            Write-Warn "L'attività pianificata non funzionerà finché l'eseguibile non viene installato"
        }

    } catch {
        if ($taskDef.Required) {
            Write-Warn "Attività pianificata '$taskName' NON trovata"
            Write-Info "Per configurarla, esegui l'installer con l'opzione 'Avvia headless all'avvio'"
            Write-Info "oppure crea manualmente l'attività con:"
            Write-Info "  schtasks.exe /Create /TN ""MaroccosHeadless"" /SC ONLOGON /RL HIGHEST /F /TR ""C:\Program Files\marocco-player\headless-player\headless-player.exe"""
        } else {
            Write-Info "Attività opzionale '$taskName' non presente (avvio al boot non configurato)"
        }
    }
    } catch {
        if ($taskDef.Required) {
            Write-Warn "Attività pianificata '$taskName' NON trovata"
            Write-Info "Per configurarla puoi eseguire:"
            Write-Info "  powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\\Program Files\\marocco-player\\tools\\windows\\register_headless_task.ps1'"
            Write-Info "oppure creare manualmente l'attività con:"
            Write-Info "  schtasks.exe /Create /TN ""MaroccosHeadless"" /SC ONLOGON /RL HIGHEST /F /DELAY 0000:15 /RU extra /TR ""C:\\Program Files\\marocco-player\\headless-autostart.cmd"""
        } else {
            Write-Info "Attività opzionale '$taskName' non presente (avvio al boot non configurato)"
        }
    }
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
