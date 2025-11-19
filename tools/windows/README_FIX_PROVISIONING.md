# Fix Windows Provisioning - Desktop Nero e Auto-partenza

## Problema Identificato

Il provisioning Windows aveva questi problemi:

1. **Impostazioni solo per utente corrente**: Le impostazioni desktop (sfondo nero, icone nascoste) venivano applicate solo a `HKCU` dell'utente che eseguiva l'installer
2. **Utente "extra" non configurato**: L'utente "extra" (che fa autologin) veniva creato DOPO il provisioning, quindi non riceveva le impostazioni
3. **Ordine errato nell'installer**: Il provisioning era eseguito prima della creazione dell'utente "extra"
4. **Nessun fallback**: Se il provisioning non riusciva ad applicare le impostazioni, non c'era modo di recuperare

## Soluzioni Implementate

### 1. **Modifica `provision_player.ps1`**

#### `Clean-Desktop` migliorata
- ✅ Applica `HideIcons` anche per utente "extra" (tramite registry HKU)
- ✅ Carica NTUSER.DAT se l'utente non è loggato
- ✅ Gestione errori migliorata

#### `Set-BlackWallpaper` migliorata  
- ✅ Applica sfondo nero per utente corrente
- ✅ Applica sfondo nero per utente "extra" (tramite registry HKU)
- ✅ Applica sfondo nero per profilo Default (nuovi utenti)
- ✅ Crea BMP in percorso condiviso (`$ProgramData`)

### 2. **Nuovo script `apply_user_settings.ps1`**

Script leggero che applica le impostazioni desktop per l'utente corrente:
- Sfondo nero
- Icone nascoste
- Animazioni disabilitate
- Trasparenze disabilitate
- Tema scuro
- Desktop pulito
- Riavvio Explorer automatico

**Uso:**
```powershell
powershell -ExecutionPolicy Bypass -File apply_user_settings.ps1
```

### 3. **Installer `off-player.iss` modificato**

#### Ordine corretto nella sezione `[Run]`:
1. **K-Lite** (se selezionato)
2. **Crea utente "extra"** ← **SEMPRE, non solo con task**
3. **Provisioning** (ora l'utente esiste già)
4. **Auto-logon** (se selezionato)
5. **Scheduled Task** per headless-player

#### Nuova entry nel registry:
```ini
[Registry]
Root: HKLM; 
Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; 
ValueName: "MaroccosUserSettings"; 
ValueData: "powershell.exe ... apply_user_settings.ps1"
```

Questo garantisce che **ogni utente** al primo login riceva le impostazioni corrette.

### 4. **Files inclusi nell'installer**

Aggiunti:
- `tools\windows\provision_player.ps1` (già presente, ora migliorato)
- `tools\windows\apply_user_settings.ps1` ← **NUOVO**

## Come Funziona Ora

### Scenario 1: Installazione con "Esegui ottimizzazioni subito"

1. Installer crea utente "extra"
2. Installer esegue `provision_player.ps1` come Administrator
3. `provision_player.ps1`:
   - Configura sistema (servizi, firewall, rete, ecc.)
   - Applica sfondo nero e icone nascoste per:
     - Utente corrente (Administrator)
     - Utente "extra" (tramite registry HKU)
     - Profilo Default (nuovi utenti)
   - Tenta di eseguire `apply_user_settings.ps1` come utente "extra"
4. Viene creata Run key in HKLM per eseguire `apply_user_settings.ps1` al login
5. Al primo login di "extra", lo script applica le impostazioni utente

### Scenario 2: Installazione con "Pianifica ottimizzazioni al prossimo avvio"

1. Installer crea utente "extra"
2. Installer crea Scheduled Task "MaroccosProvision"
3. Al prossimo login (Administrator o extra):
   - Task esegue `provision_player.ps1`
   - Il resto come Scenario 1
   - Task si auto-rimuove

### Scenario 3: Nessun provisioning selezionato

1. Installer crea comunque utente "extra"
2. Installer crea Run key per `apply_user_settings.ps1`
3. Al primo login di "extra":
   - `apply_user_settings.ps1` applica le impostazioni base
   - Desktop nero, icone nascoste, ecc.
4. Per provisioning completo, eseguire manualmente:
   ```powershell
   cd "C:\Program Files\marocco-player"
   powershell -ExecutionPolicy Bypass -File .\tools\windows\provision_player.ps1
   ```

## Verifica Post-Installazione

### 1. Verifica utente "extra" creato
```powershell
Get-LocalUser -Name "extra"
```

### 2. Verifica impostazioni desktop
```powershell
# Sfondo nero
Get-ItemProperty "HKCU:\Control Panel\Desktop" -Name Wallpaper

# Icone nascoste
Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name HideIcons

# Run key per apply_user_settings
Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name MaroccosUserSettings
```

### 3. Verifica auto-logon (se configurato)
```powershell
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" | 
    Select-Object DefaultUserName, AutoAdminLogon, ForceAutoLogon
```

### 4. Verifica scheduled task headless
```powershell
Get-ScheduledTask -TaskName "MaroccosHeadless" -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "MaroccosHeadlessBoot" -ErrorAction SilentlyContinue
```

## Test su Sistema Pulito

### Test completo:
1. Installa su VM Windows 11 pulita
2. Seleziona tutte le opzioni:
   - ✅ Installa K-Lite
   - ✅ Avvia headless all'avvio
   - ✅ Imposta OFF_AUTOSTART=1
   - ✅ Esegui ottimizzazioni subito
   - ✅ Configura auto logon utente extra
3. Dopo installazione:
   - Logout
   - Login automatico come "extra"
   - Verificare:
     - ✅ Desktop nero
     - ✅ Nessuna icona visibile (tranne marocco-player)
     - ✅ headless-player si avvia automaticamente
     - ✅ Nessuna animazione Windows

### Test ripristino:
Se le impostazioni non si applicano al primo login:
```powershell
# Esegui manualmente come utente loggato
cd "C:\Program Files\marocco-player"
powershell -ExecutionPolicy Bypass -File .\tools\windows\apply_user_settings.ps1
```

## Troubleshooting

### Problema: Desktop non nero dopo login "extra"

**Causa**: Registry HKU non caricato durante provisioning

**Soluzione**:
```powershell
cd "C:\Program Files\marocco-player"
powershell -ExecutionPolicy Bypass -File .\tools\windows\apply_user_settings.ps1
```

### Problema: Icone desktop ancora visibili

**Causa**: Explorer non riavviato

**Soluzione**:
```powershell
Stop-Process -Name explorer -Force
Start-Process explorer.exe
# oppure riavvia il sistema
```

### Problema: Provisioning non eseguito

**Verifica**: Controlla se task schedulato esiste
```powershell
Get-ScheduledTask -TaskName "MaroccosProvision" -ErrorAction SilentlyContinue
```

**Soluzione**: Esegui manualmente
```powershell
cd "C:\Program Files\marocco-player"
powershell -ExecutionPolicy Bypass -File .\tools\windows\provision_player.ps1 -NonInteractive
```

### Problema: headless-player non si avvia

**Verifica**: Controlla scheduled task
```powershell
Get-ScheduledTask -TaskName "MaroccosHeadless"
Get-ScheduledTask -TaskName "MaroccosHeadlessBoot"
schtasks /Query /TN "MaroccosHeadless" /V /FO LIST
schtasks /Query /TN "MaroccosHeadlessBoot" /V /FO LIST
```

**Verifica**: Controlla se eseguibile esiste
```powershell
Test-Path "C:\Program Files\marocco-player\headless-player\headless-player.exe"
```

**Soluzione**: Ricrea task manualmente
```powershell
schtasks /Create /TN "MaroccosHeadless" /SC ONLOGON /RL HIGHEST /F /RU "extra" /RP "extra" /TR "C:\Program Files\marocco-player\headless-player\headless-player.exe"
schtasks /Create /TN "MaroccosHeadlessBoot" /SC ONSTART /RL HIGHEST /RU SYSTEM /F /TR "C:\Program Files\marocco-player\headless-player\headless-player.exe"
```

## Cleanup Manuale (per test ripetuti)

```powershell
# Rimuovi utente extra
Remove-LocalUser -Name "extra" -Confirm:$false

# Rimuovi auto-logon
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon /t REG_SZ /d 0 /f
reg delete "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword /f
reg delete "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v ForceAutoLogon /f

# Rimuovi tasks
schtasks /Delete /TN "MaroccosHeadless" /F
schtasks /Delete /TN "MaroccosHeadlessBoot" /F
schtasks /Delete /TN "MaroccosProvision" /F

# Rimuovi Run key
reg delete "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v MaroccosUserSettings /f

# Ripristina desktop normale
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" /v HideIcons /t REG_DWORD /d 0 /f
Stop-Process -Name explorer -Force; Start-Process explorer.exe
```

## Changelog

### v1.1 (7 novembre 2025)
- ✅ Fix ordine esecuzione installer (utente prima, provisioning dopo)
- ✅ Applicazione impostazioni desktop anche per utente "extra"
- ✅ Nuovo script `apply_user_settings.ps1` per failsafe
- ✅ Run key HKLM per applicare impostazioni a tutti gli utenti
- ✅ Supporto profilo Default per nuovi utenti
- ✅ Migliorata gestione errori e logging
- ✅ Documentazione completa troubleshooting

### v1.0 (precedente)
- Provisioning base (servizi, rete, firewall)
- Desktop nero solo per utente corrente
- Auto-logon opzionale
