# Windows provisioning (player)

Questa cartella contiene uno script PowerShell per ottimizzare un Windows 11 "player" con risorse limitate e un semplice setup.bat che copia lo script e ne facilita l'esecuzione.

Contenuti:
- `provision_player.ps1`: provisioning completo, molto documentato e con output passo-passo.
- `setup.bat`: copia lo script in `C:\PlayerSetup` e chiede se eseguirlo subito o al prossimo avvio (RunOnce).
- `headless_task_helpers.ps1`: funzioni condivise usate da provisioning e registrazione task per l'autostart del player headless.
- `rollback_player.ps1`: rollback best‑effort per ripristinare Windows Update/servizi, DHCP su Ethernet e impostazioni visive/energetiche.

## Requisiti
- Windows 11
- Eseguire come Amministratore
- (Consigliato) Winget disponibile per installare TigerVNC automaticamente

## Cosa fa
- Disabilita (best-effort) servizi/feature non essenziali: Windows Update, Delivery Optimization, Search index, telemetria, ads/suggerimenti consumer.
- Ottimizza UX: disabilita animazioni/trasparenze, ripulisce il desktop, sfondo nero.
- Power plan: Prestazioni elevate, no sleep/hibernate.
- Rete: Ethernet IP statico 192.168.10.220/24 (senza gateway), Wi‑Fi in DHCP.
- Firewall: apre porte tipiche del player (TCP 8080, UDP 7777) e GUI UDP 9999.
- Account: garantisce utente locale `extra` (admin) con password `extra`.
- VNC: propone installazione TigerVNC via winget.

Nota IP: impostare lo stesso IP su più device è appropriato solo se ogni device è collegato ad una rete isolata dedicata (punto‑punto). In LAN condivisa provocherebbe conflitti.

## Uso rapido
1. Copia questa cartella `tools/windows` sulla macchina Windows.
2. Click destro su `setup.bat` -> "Esegui come amministratore".
3. Scegli:
   - 1: esegue subito il provisioning
   - 2: programma l'esecuzione al prossimo avvio (RunOnce)
   - 3: solo copia, non esegue

In alternativa:
```
# (Finestra PowerShell come Amministratore)
Set-ExecutionPolicy Bypass -Scope Process -Force
cd C:\PlayerSetup
powershell -ExecutionPolicy Bypass -File .\provision_player.ps1
```

## Rollback e cautele
- Windows Update: lo script imposta NoAutoUpdate. Per ripristinare, riattivare il servizio `wuauserv` e rimuovere la chiave `HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU\\NoAutoUpdate`.
- Servizi: i servizi disabilitati possono essere riattivati con `Set-Service -StartupType Automatic` (o Manual) e `Start-Service`.
- IP statico: per tornare a DHCP su Ethernet: `Set-NetIPInterface -InterfaceAlias "Ethernet" -Dhcp Enabled` e `Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ResetServerAddresses`.
- Se qualcosa fallisce, rieseguire lo script: è idempotente su molti passaggi.

Script di rollback pronto all'uso:
```
# Eseguire in PowerShell come Amministratore
Set-ExecutionPolicy Bypass -Scope Process -Force
cd C:\\PlayerSetup
powershell -ExecutionPolicy Bypass -File .\\rollback_player.ps1 -RemoveFirewallRules
```

## TigerVNC
- Se `winget` non è disponibile o l'installazione fallisce, lo script apre la pagina ufficiale TigerVNC per download manuale.
- Lo script prova a impostare automaticamente la password VNC usando `vncpasswd -f` e scrivendo i dati in `HKLM\Software\TigerVNC\WinVNC4` (best‑effort). Puoi passare `-TigerVNCPassword "<pwd>"` per cambiarla (default `extra`).
- In caso di esito negativo, imposta la password manualmente con l'utility `vncpasswd` o dall'interfaccia di TigerVNC.

## Parametri avanzati
`provision_player.ps1` accetta parametri opzionali:
- `-NonInteractive`: non chiedere prompt interattivi.
- `-EthernetAlias "Ethernet 2"`: forza il nome della scheda ethernet.
- `-StaticIP 192.168.10.220 -PrefixLength 24`: cambia IP/prefix.
- `-DnsServers 8.8.8.8,1.1.1.1`: imposta DNS custom.
- `-SkipTigerVNC`: non installare TigerVNC.

Esempio:
```
powershell -ExecutionPolicy Bypass -File C:\PlayerSetup\provision_player.ps1 -NonInteractive -SkipTigerVNC
```
