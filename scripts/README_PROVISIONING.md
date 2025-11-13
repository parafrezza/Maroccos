# Provisioning Scripts

Questi script configurano il sistema operativo per l'esecuzione ottimale di OFF Player in modalità headless/kiosk.

## Windows: `provision_player.ps1`

**Percorso**: `tools/windows/provision_player.ps1`

Configura Windows 11 per uso dedicato come player:

- **Disabilita servizi non necessari**: Windows Update, telemetria, indicizzazione, ads
- **Ottimizza grafica**: No animazioni, trasparenze, desktop pulito, sfondo nero
- **Piano alimentazione**: Prestazioni elevate, no sleep/hibernate
- **Rete**: IP statico Ethernet (192.168.10.220/24), Wi-Fi in DHCP
- **Account**: Crea utente locale "extra" amministratore (password: "extra")
- **Auto-logon**: Opzionale, per partenza automatica
- **TigerVNC**: Installazione opzionale per controllo remoto
- **Risoluzione**: Forza 1280x720@50Hz se supportata

### Uso

```powershell
# Esecuzione interattiva (con conferme)
powershell -ExecutionPolicy Bypass -File .\tools\windows\provision_player.ps1

# Esecuzione automatica (no conferme)
powershell -ExecutionPolicy Bypass -File .\tools\windows\provision_player.ps1 -NonInteractive

# Con parametri personalizzati
powershell -ExecutionPolicy Bypass -File .\tools\windows\provision_player.ps1 `
  -NonInteractive `
  -StaticIP "192.168.10.221" `
  -SkipTigerVNC
```

### Integrazione con Installer

L'installer Inno Setup (`installer/off-player.iss`) offre queste opzioni:

1. **Esegui ottimizzazioni subito**: Provisioning durante l'installazione
2. **Pianifica al prossimo avvio**: Crea attività pianificata
3. **Salta**: Non applicare provisioning

## Raspberry Pi: `provision_pi.sh`

**Percorso**: `scripts/provision_pi.sh`

Configura Raspberry Pi OS per uso dedicato come player headless:

- **Disabilita desktop grafico**: Imposta `multi-user.target` (console only)
- **Autologin console**: Login automatico come root
- **Screensaver/Blanking**: Completamente disabilitati
- **Desktop nero**: Se X11 è presente, configura sfondo nero e nasconde cursore
- **CPU Governor**: Impostato su "performance"
- **Servizi**: Disabilita servizi non necessari (bluetooth, avahi, cups, ecc.)
- **HDMI**: Configura 1280x720@50Hz
- **Rete**: Wi-Fi e IP statico Ethernet (192.168.10.220/24)
- **Firewall**: Regole base per headless-player
- **Aggiornamenti automatici**: Disabilitati

### Uso

```bash
# Esecuzione standard
sudo ./scripts/provision_pi.sh

# Con parametri personalizzati
sudo WIFI_SSID="mywifi" WIFI_PSK="mypass" ETH_STATIC_IP="192.168.10.221/24" ./scripts/provision_pi.sh

# Solo alcune configurazioni
sudo AUTOLOGIN_USER="pi" ./scripts/provision_pi.sh
```

### Variabili d'ambiente

- `WIFI_SSID`: Nome rete Wi-Fi (default: "extratech")
- `WIFI_PSK`: Password Wi-Fi (default: "eXtratech")
- `ETH_STATIC_IP`: IP statico Ethernet (default: "192.168.10.220/24")
- `ETH_IFACE`: Interfaccia Ethernet (default: "eth0")
- `HDMI_WIDTH`: Larghezza HDMI (default: 1280)
- `HDMI_HEIGHT`: Altezza HDMI (default: 720)
- `HDMI_GROUP`: Gruppo HDMI (default: 1 = CEA)
- `HDMI_MODE`: Modo HDMI (default: 19 = 1280x720@50Hz)
- `AUTOLOGIN_USER`: Utente per autologin (default: "root")

### Integrazione con Installazione

Il provisioning viene **automaticamente eseguito** da `install_on_pi.sh` alla fine dell'installazione.

Per eseguirlo manualmente:

```bash
# Da una installazione esistente
sudo /opt/offplayer/scripts/provision_pi.sh

# Dal repository di sviluppo
sudo ./scripts/provision_pi.sh
```

## Verifica Post-Provisioning

### Windows

```powershell
# Verifica servizio headless-player
Get-ScheduledTask -TaskName "MaroccosHeadless"

# Verifica utente extra
Get-LocalUser -Name "extra"

# Verifica IP statico
Get-NetIPAddress | Where-Object { $_.InterfaceAlias -like "*Ethernet*" }
```

### Raspberry Pi

```bash
# Verifica target systemd
systemctl get-default
# Dovrebbe essere: multi-user.target

# Verifica servizio headless-player
systemctl status headless-player.service

# Verifica autologin
cat /etc/systemd/system/getty@tty1.service.d/autologin.conf

# Verifica blanking disabilitato
cat /boot/firmware/cmdline.txt | grep consoleblank

# Verifica IP statico
ip addr show eth0
```

## Ripristino

### Windows

Se necessario ripristinare le impostazioni originali:

1. Usa il punto di ripristino creato automaticamente
2. Riabilita manualmente i servizi:
   ```powershell
   Set-Service wuauserv -StartupType Manual
   Set-Service WSearch -StartupType Automatic
   ```

### Raspberry Pi

Per ripristinare il desktop grafico:

```bash
sudo systemctl set-default graphical.target
sudo systemctl reboot
```

Per riabilitare servizi:

```bash
sudo systemctl enable bluetooth avahi-daemon
```

## Note Importanti

### IP Statico Condiviso

⚠️ **ATTENZIONE**: Lo stesso IP statico (192.168.10.220) è configurato di default su tutti i player.

Questo è corretto **SOLO** se:
- Ogni player è su una rete isolata punto-punto
- Esempio: Player → Ethernet dedicata → Ricevitore 433MHz

Se più player sono sulla stessa LAN, **cambiare l'IP** per ciascuno:

**Windows**:
```powershell
provision_player.ps1 -StaticIP "192.168.10.221"
```

**Raspberry**:
```bash
sudo ETH_STATIC_IP="192.168.10.221/24" ./scripts/provision_pi.sh
```

### Sicurezza

Gli script disabilitano aggiornamenti automatici per prestazioni ottimali.

**Raccomandazioni**:
- Usa questi script SOLO su dispositivi dedicati in reti isolate
- Pianifica cicli di manutenzione offline periodici
- Non esporre i player direttamente su Internet
- Cambia password di default ("extra") in ambienti di produzione

### Desktop Nero vs Console

**Windows**: Desktop grafico con sfondo nero e icone nascoste

**Raspberry Pi**: 
- Console testuale (no X11) per massime prestazioni
- Se serve X11, lo script configura sfondo nero automaticamente
- headless-player usa EGL/KMS (rendering diretto, no X11 necessario)

## Testing

### Test Provisioning Windows

```powershell
# Dry-run (mostra cosa verrebbe fatto, senza modifiche)
# Non supportato - usare -WhatIf con comandi individuali

# Verifica con utente test
provision_player.ps1 -NonInteractive -SkipTigerVNC
```

### Test Provisioning Raspberry

```bash
# Verifica sintassi
bash -n ./scripts/provision_pi.sh

# Esecuzione con output verboso
sudo bash -x ./scripts/provision_pi.sh 2>&1 | tee provision.log
```

## Troubleshooting

### Windows

**Problema**: Installer non esegue provisioning

**Soluzione**: Verifica che sia selezionata l'opzione durante l'installazione o esegui manualmente:
```powershell
cd "C:\Program Files\marocco-player"
powershell -ExecutionPolicy Bypass -File .\tools\windows\provision_player.ps1
```

**Problema**: TigerVNC non si installa

**Soluzione**: 
- Verifica connettività Internet
- Scarica manualmente da https://tigervnc.org/
- Posiziona installer in `installer/assets/tigervnc64-winvnc-1.15.0.exe`

### Raspberry Pi

**Problema**: Provisioning non eseguito dopo install

**Soluzione**: 
```bash
sudo /opt/offplayer/scripts/provision_pi.sh
```

**Problema**: Schermo rimane acceso/va in blanking

**Soluzione**: Verifica cmdline e riavvia
```bash
cat /boot/firmware/cmdline.txt | grep consoleblank
# Dovrebbe contenere: consoleblank=0
sudo reboot
```

**Problema**: Desktop ancora visibile

**Soluzione**:
```bash
sudo systemctl get-default
# Se non è "multi-user.target":
sudo systemctl set-default multi-user.target
sudo reboot
```

## Riferimenti

- [Raspberry Pi Config](https://www.raspberrypi.com/documentation/computers/config_txt.html)
- [Systemd Targets](https://www.freedesktop.org/software/systemd/man/systemd.special.html)
- [TigerVNC Documentation](https://tigervnc.org/)
- [Windows Performance Tuning](https://docs.microsoft.com/en-us/windows-server/administration/performance-tuning/)
