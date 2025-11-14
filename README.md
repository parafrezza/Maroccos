# Maroccos

Player video multi-progetto con GUI, headless-player (Python) e OFF-player (openFrameworks), pensato per girare su macOS, Linux e dispositivi embedded (es. Raspberry Pi).

## Componenti

- GUI (`GUI/`): interfaccia per controllo e monitoraggio.
- headless-player (`headless-player/`): server FastAPI con backend video pluggabili (GStreamer, VLC/cvlc, MPV, OFF).
- OFF-player (`OFF-player/`): player C++ basato su openFrameworks con controllo via UDP e API HTTP integrate.
- Sincronizzazione orologi a livello applicativo (vedi `docs/time_sync.md`).

## Prerequisiti

- macOS o Linux
- Python 3.11+
- openFrameworks 0.12.1 presente in `OFF-ROOT/` (non versionato)
- Strumenti build C++ (Xcode CLT su macOS, build-essential su Linux)

## Avvio rapido

### GUI
```bash
cd GUI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

### Headless-player (Python)
```bash
cd headless-player
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```
Note:
- GStreamer/PyGObject (gi) è opzionale: se non presente, l'app si avvia con shim minimi (alcune funzioni avanzate non saranno disponibili).
- Backend disponibili: `gst`, `cvlc`, `mpv`, `off` (OFF-player via HTTP). Vedi “Integrazione con OFF-player”.

### OFF-player (openFrameworks)
Con VS Code:
- Premi ⌘⇧B e scegli “Build Release” (task predefinito).
- Per eseguire: lancia l'app generata in `OFF-player/bin/` (es. `OFF-player.app` su macOS).

Oppure da terminale:
```bash
cd OFF-player
make Release
open bin/OFF-player.app  # macOS
```

## Condivisione cartella media (Windows)

Per rendere disponibili i contenuti locali alla rete:
- crea (o verifica che esista) una cartella `media` sul Desktop dell'account usato dal player;
- apri le proprietà della cartella, scheda "Condivisione", e attiva la condivisione avanzata con nome `media`;
- concedi a "Everyone" i permessi di modifica (lettura/scrittura) così da poter caricare file da altri PC;
- se il provisioning automatizzato viene eseguito, questi passaggi sono gestiti dallo script, ma è utile conoscerli per controlli manuali.

## Integrazione: usare OFF-player come backend del headless
1) Avvia OFF-player (assicura `httpPort` in `OFF-player/config.json`, default 8080).
2) Avvia headless-player.
3) Cambia backend del headless su “off”:
```bash
curl -X POST http://localhost:8080/change_framework -H 'Content-Type: application/json' -d '{"name":"off"}'
```
4) Ora le API del headless inoltrano a OFF-player. Esempi utili:
```bash
# playback base
curl -X POST http://localhost:8080/play
curl -X POST http://localhost:8080/pause
curl -X POST http://localhost:8080/resume
curl -X POST http://localhost:8080/stop
curl -X POST 'http://localhost:8080/loop?on=1'

# play file assoluto via OFF-player
curl -X POST 'http://localhost:8080/play_file' -d '' --data-urlencode 'path=/percorso/assoluto/video.mp4'

# stato
curl http://localhost:8080/status
```
Limitazioni attuali del backend “off”:
- `play(path=...)`: usa `play_file` se vuoi forzare un file arbitrario; altrimenti gestisci `mediaDir`/playlist.
- Fade/overlay e faststart: non ancora implementati su OFF-player (vedi “Roadmap”).

## API OFF-player (HTTP)
- POST `/play` `/stop` `/pause` `/resume`
- POST `/next` `/prev`
- POST `/set?index=N`
- POST `/dir?path=/nuova/cartella`
- POST `/reload`
- POST `/loop?on=1|0`
- GET  `/status` → `{ count, index, playing, loop, file }`
- GET  `/healthz` → `{ ok: true }`
- POST `/play_file?path=/abs/file.mp4`

## Task di build multi-piattaforma (VS Code)
In `.vscode/tasks.json` trovi:
- macOS: Build Release locale
- Raspberry Pi (SSH): Build Release remoto (richiede host e path; imposta `OF_ROOT=../OFF-ROOT/of_v0.12.1_linuxarmv7l_release` lato RPi)
- Windows (remote): placeholder (consigliato build nativa con Visual Studio o MSYS2/make)

Note cross-compilazione:
- macOS → Windows: sconsigliata per openFrameworks; meglio build nativa.
- macOS → Raspberry Pi: preferibile da Linux con toolchain cross-ARM; da macOS usa RPi via SSH o una VM Linux.

## Troubleshooting
- `ModuleNotFoundError: No module named 'gi'` all'avvio del headless: ok, il server usa fallback senza GStreamer. Installa PyGObject e GStreamer solo se ti serve il backend `gst`.
- Import error su `fastapi`, `uvicorn`, `Pillow`, `netifaces`, `requests`: attiva il venv giusto e `pip install -r headless-player/requirements.txt`.
- Porta 8080 occupata: cambia `APP_PORT` nel headless o `httpPort` in `OFF-player/config.json`.
- OFF-player non parte: verifica `OFF-ROOT/` con openFrameworks 0.12.1 e addons `ofxNetwork`, `ofxPoco`.

## Roadmap
- OFF-player: API per fade/overlay (visual_fade_in/ftb) e faststart (prepare/go)
- Headless backend “off”: usare le nuove API per parità con `gst`/`cvlc`
- Pipeline CI per build Windows/RPi
- GUI: barra di avanzamento per upload multi-device e stato aggregato
- GUI: tooltip con dimensioni/risoluzione per i media disponibili (anche remoti)
- Configurazione: lista di estensioni multimediali consentite configurabile da GUI/Settings

## Licenza
Proprietario del repository: parafrezza. Vedere i singoli file per note specifiche.
