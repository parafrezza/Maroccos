# Deploy OFF-player + headless-player (Raspberry Pi)

Questo pacchetto contiene:
- `bin/OFF-player`: l'applicazione openFrameworks (Release)
- `headless-player/`: il servizio Python (FastAPI) che avvia/controlla l'app
- `resources/headless-player.service`: unità systemd da installare

## Requisiti
- Raspberry Pi (Debian Bookworm/Ubuntu) con `python3`, `python3-venv`, `pip` disponibili
- `systemd`

## Installazione rapida
1. Copia il tar.gz sul Raspberry
2. Esegui:

```bash
sudo /path/to/repo/scripts/install_on_pi.sh /percorso/al/tarball.tgz
```

Lo script installerà il pacchetto in `/opt/offplayer` (predefinito), creerà un venv per `headless-player`, installerà le dipendenze, configurerà il servizio `headless-player.service`, lo abiliterà e lo avvierà.

## Variabili utili
- `OFF_BINARY`: sovrascrive il percorso dell'eseguibile OFF-player. Il service lo imposta al percorso installato.
- `LOG_LEVEL`/`LOG_FILE`: per tracciare i log del headless-player su file (default `/var/tmp/headless-player/headless-player.log`).

## Permessi e path
- Media e configurazioni generano file scrivibili da tutti (UMask=000 nel service). Per un ambiente più restrittivo puoi adottare un gruppo dedicato e UMask=0002.

## Troubleshooting
- `systemctl status headless-player`: stato del servizio.
- Log su file: vedi `LOG_FILE` o la directory `/var/tmp/headless-player/`.
- In caso di build statica di OFF (senza .so), il packaging può segnalare un `cp: cannot stat ... *.so`: è atteso e viene ignorato se `PACKAGE_ALLOW_MISSING_OF_SO=1`.
