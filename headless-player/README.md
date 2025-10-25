# Headless Video Player (Raspberry Pi 3B)

Riproduttore headless controllato via API FastAPI, backend video pluggable (GStreamer / CVLC / PyQt / MPV) + controlli remoti HTTP e UDP.

## Novità principali
- Backend modulare: `mpv` (default), `gst`, `cvlc`, `pyqt` selezionabile via `/change_framework` e persistito in `config.json`.
- Parametro temporale `in_time` (epoch futuro o delay in secondi) per sincronizzare: `/play`, `/ping`, `/change_framework` (e comandi UDP equivalenti).
- Fade software coerente su tutti i backend (GStreamer: alpha/brightness, CVLC: preferibilmente overlay; PyQt: opacity finestra).
- Overlay KMS opzionale (abilitato via setup) per Fade-To-Black e fade-in cross-backend.
- Configurazione persistente (`config.json`) di: framework, risoluzione/caps, ultima media, flag UDP/autoplay, device_name.
- Comandi remoti via UDP (porta 7777 JSON), abilitati di default (disattivabili via `/settings/reload`).
- Always-black: quando il player è idle/stopped, con `cvlc` viene mostrato un frame nero persistente (nessun prompt visibile).
- Massive updater robusto (download → apply) con sincronizzazione e logging progresso.
- Performance: setup configura 720p50, governor CPU "performance", overlay attivo, e argomenti VLC coerenti (incluso `--image-duration`).

## Funzionalità principali (sintesi)
- Splash iniziale con IP (eth/wifi) + versione (GStreamer `appsrc`) o nero (`SPLASH_BLACK=1`).
- Riproduzione file video con validazione header (MP4, MKV, AVI).
- Fade in/out e ping visivo (flash) senza disturbi persistenti.
- Playlist automatica di tutti i file in `media/` + next/prev + preload pipeline per ridurre gap.
- Download asset remoto con controllo header e prevenzione HTML/404.
- Sistema update con tracking bytes + apply e restart.
- Riconfigurazione runtime di caps/sink (`/settings/reload`).

### Avvio e idle nero
- All’avvio, appare lo splash (o un nero pieno se `SPLASH_BLACK=1`).
- Con backend `cvlc`, quando lo splash è nascosto e il player è idle/stopped, viene preparata e messa in pausa un’immagine nera; grazie a `--image-duration` resta visibile, evitando che compaia il prompt.

## Installazione rapida
```bash
git clone <repo> headless-player
cd headless-player
sudo chmod +x setup.sh
sudo ./setup.sh   # configura overlay KMS, CVLC, 720p50, governor performance, permessi persistenti
sudo systemctl start headless-player
```

## Backend video
Endpoint:
  - `in_time`: se presente pianifica lo switch futuro (epoch o delay secondi).
  - Persistenza automatica.

Nota: playlist completa solo su backend `gst`. CVLC/PyQt/MPV gestiscono un singolo file (con `loop`).
Nota: playlist completa solo su backend `gst`. CVLC/PyQt/MPV gestiscono un singolo file (con `loop`).

Backend `mpv`:
- Richiede `mpv` installato nel sistema (installato da `setup.sh`).
- Supporta play/stop/pause/resume/loop via IPC JSON. Nessun fast-start/preload nativo; per i fade visivi si usa l'overlay KMS dell'app se attivo.

## Parametro temporale `in_time`
Accettato da `/play`, `/ping`, `/change_framework` e dai comandi UDP (`play`, `ping`, `change_framework`).
- Se valore >= 1e9: trattato come epoch (UTC seconds).
- Se valore < 1e9: delay relativo in secondi.
- Scheduling preciso con GLib (timeout millisecondi). Il fade_out, se richiesto, viene anticipato per finire esattamente all'`in_time` di start.

Comando UDP dedicato `play_at` (JSON `{"cmd":"play_at", "at": <epoch|delay>, ...}`) è equivalente a `/play` con `in_time`.

## API essenziali
```
POST /play { filename|path, loop, fade_in_seconds, fade_out_seconds, in_time }
POST /ping?duration_ms=120&in_time=...
GET  /framework
POST /change_framework { name, in_time }
POST /settings/reload { USE_KMS, TARGET_WIDTH, TARGET_HEIGHT, TARGET_FPS, SPLASH_BLACK, UDP_ENABLED }
POST /overlay/show?alpha=1.0
POST /overlay/hide
POST /overlay/fade?target=1.0&seconds=1.0
POST /faststart/prepare { filename|path }
POST /faststart/go?seconds=1.0
GET  /device/name
POST /device/name { name }
POST /test/on
POST /test/off
```
Altre utili:
- `GET /status` → stato completo del player e dell'update
- `POST /pause`, `POST /resume`, `POST /stop` → controlli di playback
- `POST /fade_in?seconds=...`, `POST /fade_out?seconds=...` → fade audio
- `POST /overlay/show|hide|fade` → overlay KMS
- `POST /faststart/prepare`, `POST /faststart/go` → fast-start
- `POST /autoplay` → abilita/disabilita autoplay playlist
- `POST /media/playlist` → crea playlist da `media/`
- `GET /playlist/status`, `POST /playlist/next`, `POST /playlist/prev`, `POST /playlist/loop`
- `POST /download_asset` → scarica un file in `media/`
- `POST /download_update`, `POST /update` → gestione aggiornamento
- `POST /maintenance/run_setup` → esegue `setup.sh` con log tracciati
- `GET /device/name`, `POST /device/name` → naming persistente del device
- `POST /settings/reload` → ricarica impostazioni runtime (abilita/disabilita UDP, ecc.)
Altre: `/pause`, `/resume`, `/loop`, `/fade_in`, `/fade_out`, `/media`, `/media/playlist`, `/playlist/next`, `/playlist/prev`, `/download_asset`, `/download_update`, `/update`, `/maintenance/run_setup`.

## UDP (porta 7777)
Abilitazione: di default è ON. Puoi disattivare/riattivare con `POST /settings/reload { "UDP_ENABLED": false|true }` (persistito in `config.json`).

Formato JSON (UTF-8) via datagram (quick reference):
- Ping: `{"cmd":"ping","duration_ms":180,"in_time":5}`
- Play: `{"cmd":"play","filename":"clip.mp4","loop":false,"fade_in_seconds":0.5,"fade_out_seconds":0.4,"in_time":1730400000}`
- Play programmato: `{"cmd":"play_at","filename":"clip.mp4","at":1730400000,"fade_in_seconds":0.5}`
- Pause/Resume/Stop: `{"cmd":"pause"}` / `{"cmd":"resume"}` / `{"cmd":"stop"}`
- Overlay: `{"cmd":"overlay_show","alpha":1}`, `{"cmd":"overlay_hide"}`, `{"cmd":"overlay_fade","target":0,"seconds":1}`, `{"cmd":"overlay_fade_at","target":1,"seconds":1,"at":<epoch|delay>}`
- Fast-start: `{"cmd":"faststart_prepare","filename":"clip.mp4"}` poi `{"cmd":"faststart_go","seconds":1}`
- Framework: `{"cmd":"change_framework","name":"cvlc","in_time":2.0}`
- Naming: `{"cmd":"device_name_set","name":"Player-01"}`
- Splash/Shutdown: `{"cmd":"hide_splash"}`, `{"cmd":"shutdown"}`

Note:
- `in_time` e `at` accettano epoch (>= 1e9) o delay relativo in secondi.
- `play_at` è uno shorthand UDP di `/play` con `in_time`.

### Troubleshooting UDP
- Verifica che il servizio sia in esecuzione e che UDP sia abilitato: `POST /settings/reload { "UDP_ENABLED": true }` e controlla `GET /status` (`udp_enabled` in `config.json`).
- Assicurati di inviare in UDP (non TCP), porta 7777, verso l’IP corretto del device.
- Il payload deve essere un JSON valido in UTF-8 con doppi apici (es.: `{"cmd":"play","filename":"testbars_10s.mp4"}`), non in HEX.
- Con Packet Sender:
  - Protocollo: UDP
  - Porta: 7777
  - Dati: testo semplice (disabilita modalità HEX), encoding UTF-8
  - Messaggio di test: `{"cmd":"ping","duration_ms":180}` oppure `{"cmd":"play","filename":"testbars_10s.mp4","fade_in_seconds":0.5}`
- Non riceverai una risposta su UDP: verifica l’effetto con `GET /status` (campo `player_state`, `current_media`, `last_logs`) o osservando lo schermo.
- Se il filename non esiste in `media/`, il player prova un fallback su un file valido presente. In assenza di media validi, la richiesta verrà ignorata: carica un file o usa un filename esistente (es.: `testbars_10s.mp4`).

## Fade
- Backend `gst`: preferenza canale alpha (`alpha` element) fallback brightness `videobalance`.
- Backend `vlc`: brightness su 0..1 via video adjust.
- Backend `cvlc`: preferire overlay KMS per FTB/fade-in (i comandi RC per "adjust" non sono affidabili su tutte le build).
- Backend `pyqt`: opacity finestra 0..1.
- Fade-out programmato prima dell'avvio nuovo contenuto se `in_time` futuro.

## Ping visivo
`POST /ping?duration_ms=120` o via UDP. Su splash usa frame bianco + ripristino testo; su video forza saturazione / alpha / brightness temporaneo.

## Configurazione persistente (`config.json`)
Chiavi: `framework`, `USE_KMS`, `USE_HW_DECODER`, `TARGET_WIDTH`, `TARGET_HEIGHT`, `TARGET_FPS`, `SPLASH_BLACK`, `last_media`, `udp_enabled`, `autoplay_enabled`, `device_name`.

## Overlay KMS (plane separato)
Consente FTB/fade-in indipendentemente dal backend (utile con `cvlc`).

Variabili d'ambiente:
- `OVERLAY_ENABLED=1`
- `OVERLAY_USE_KMS=1`
- `OVERLAY_KMS_PLANE_ID=<id plane overlay>`
- `OVERLAY_ZPOS=3` (o superiore al plane video)

Endpoint:
- `POST /overlay/show?alpha=1.0` → mostra overlay nero con alpha [0..1]
- `POST /overlay/hide` → rimuove overlay
- `POST /overlay/fade?target=0.0&seconds=1.0` → anima alpha

Suggerimenti:
- Identificare un plane overlay con `modetest` (supporto ARGB e zpos alto).
- L'utente del servizio deve essere nel gruppo `render` per accedere a `/dev/dri/*`.
- Se overlay non disponibile, gli endpoint `/visual/*` ripiegano su backend o fade audio.

### Cosa fa il setup.sh (prestazioni e coerenza)
- Imposta HDMI 720p50 (mode 19) e forzatura 50 Hz nel cmdline video (fluidità a 50 fps).
- Abilita governor CPU "performance" via `cpufrequtils`.
- Abilita di default `OVERLAY_ENABLED=1`, `OVERLAY_USE_KMS=1` nel servizio systemd.
- Imposta `VLC_EXTRA_ARGS` (es. `--no-video-title-show --no-sub-autodetect-file --image-duration=36000`).
- Rende persistenti i permessi su `/opt/headless-player` via ACL default e `UMask=0002` nel servizio.
 - Installa `mpv` via APT (oltre a VLC, PyQt5) per il backend dedicato.

## Fast-start e "sempre nero" con CVLC
Riduce la latenza all'avvio coordinando preload e overlay nero. Con `cvlc` il player mantiene sempre uno schermo nero quando è idle.

Flusso fast-start:
1. `POST /faststart/prepare { filename|path }` → porta overlay a 1.0; su `gst` crea pipeline in PAUSED; su `cvlc` prepara il primo frame.
2. `POST /faststart/go?seconds=1.0` → start immediato (adotta pipeline) e overlay fade-out/hide in `seconds`.

Play sincronizzato via UDP:
- `{"cmd":"play_at","filename":"clip.mp4","at":<epoch|delay>,"fade_in_seconds":0.5}` → equivalente a `/play` con `in_time`.

Sempre nero (idle):
- Quando lo splash è nascosto e non c'è playback attivo, `cvlc` carica un PNG nero (bundle include `media/black_1280_720.png` e `media/black.png`), lo prepara e lo mette in pausa sul primo frame. Grazie a `VLC_EXTRA_ARGS --image-duration=36000`, l'immagine resta visibile senza scadere.
- Allo stop/fine clip, un monitor del backend ripristina il frame nero, evitando che compaia il prompt della console.

## Naming dispositivo
Consente di assegnare un nome persistente al player (utile se l'IP cambia):
- `GET /device/name` → `{ name }`
- `POST /device/name { name }` → salva in `config.json` (`device_name`) e restituisce `{ name }`.

Il nome è esposto anche su `GET /status`.

## Modalità TEST visiva
Esegue una pipeline che genera un pattern diagnostico (riga bianca orizzontale e riga rossa verticale in movimento):
- `POST /test/on`
- `POST /test/off`

Suggerimenti GUI: aggiungere un toggle "TEST" per ogni device.

## Timing e sincronizzazione
`GET /status` espone un blocco `timing` (stub): `{ synced, method, offset_ms }`, utile per un LED stato in GUI.
Per sincronizzazioni multi-device precise, mantenere l'orologio allineato (NTP/PTP). In roadmap: handshake di clock con GUI e/o trigger RF (LoRa) broadcast.

### Campi esposti da `GET /status` (principali)
- `version_current` / `version_available` → versione installata e disponibile (se nota).
- `player_state` → `playing` | `paused` | `stopped`.
- `splash_active` / `overlay_active` → stato UI splash e overlay KMS.
- `device_name` → nome configurato del device.
- `update_status` → `idle` | `downloading` | `downloaded` | `applying` | `ok` | `error`.
- `update_progress` → percentuale (0..100) e `update_bytes_done/total`.
- `update_error` → messaggio errore in caso di failure.
- `maintenance_status` / `maintenance_last_logs` → stato esecuzione `setup.sh` remoto.
- `faststart.prepared` / `faststart.path` → stato preload traccia.
- `scheduled.play_at` / `scheduled.overlay_fade_at` → timestamp programmati (epoch secondi).

## Massive Updater
Strumento `tools/massive_updater.py` (PC) per:
1. Build pacchetto e `latest.zip`.
2. Scansione device (`/healthz`).
3. Download parallelo `/download_update` sincronizzato.
4. Apply `/update`.
5. (Opzionale) `/maintenance/run_setup`.
6. Schedulazione sincronizzata di `/play` e `/change_framework` via `in_time`.

## Limitazioni e note
- Playlist e preload solo su `gst`.
- Overlay splash/diagnostica esclusivo GStreamer (gli altri backend chiudono direttamente lo splash).
- Nessun mixing audio/video multiplo nei backend alternativi.
- Precisione scheduling dipende da clock di sistema (sincronizzare via NTP per sincronizzazioni multi-device).
- VLC/PyQt: fade basato su brightness/opacity non gamma-correct.
 - MPV: backend leggero; niente fast-start/preload nativo. Per i fade visivi usa l'overlay dove disponibile.

## Roadmap possibile
- Statistiche latenza effettiva start vs `in_time`.
- Hash SHA256 pacchetti update.
- Watchdog refresh UI su PyQt.

## Sicurezza
- Validazione header media.
- Troncamento log.
- Download atomico `.part` → rename.

## Variabili d'ambiente
`USE_KMS`, `USE_HW_DECODER`, `TARGET_WIDTH`, `TARGET_HEIGHT`, `TARGET_FPS`, `SPLASH_BLACK`, `APP_PORT`, `OVERLAY_ENABLED`, `OVERLAY_USE_KMS`, `OVERLAY_KMS_PLANE_ID`, `OVERLAY_ZPOS`, `VLC_EXTRA_ARGS`.

## Windows (PowerShell) Quickstart
Questa sezione fornisce comandi pronti su Windows (PowerShell v5.1) per provare le API. Richiede Python 3.11+.

- Crea venv e installa dipendenze minime:

```powershell
cd "g:\My Drive\Lavori\2025\Maroccos\headless-player"
python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; python -m pip install --upgrade pip
pip install -r requirements.txt
```

- Avvia il server FastAPI locale (su Windows i backend GStreamer non sono disponibili via pacchetti di sistema; puoi comunque testare le API):

```powershell
python .\app.py
```

- Verifica salute e stato:

```powershell
Invoke-RestMethod -Method GET http://127.0.0.1:8080/healthz | ConvertTo-Json -Depth 3
Invoke-RestMethod -Method GET http://127.0.0.1:8080/status | ConvertTo-Json -Depth 5
```

- Elenco e pulizia media:

```powershell
Invoke-RestMethod -Method GET http://127.0.0.1:8080/media | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method POST "http://127.0.0.1:8080/media/clear?confirm=1"
```

- Download di un asset da un server HTTP:

```powershell
$url = "http://192.168.1.10:8000/headless-player/media/s1.mp4"
Invoke-RestMethod -Method POST http://127.0.0.1:8080/download_asset -ContentType application/json -Body (@{ url=$url; filename="s1.mp4" } | ConvertTo-Json)
```

- Play immediato con fade-in 0.5s e loop:

```powershell
Invoke-RestMethod -Method POST http://127.0.0.1:8080/play -ContentType application/json -Body (@{ filename="s1.mp4"; loop=$true; fade_in_seconds=0.5 } | ConvertTo-Json)
```

- Ping visivo tra 2s (in_time relativo) per 180ms:

```powershell
Invoke-RestMethod -Method POST "http://127.0.0.1:8080/ping?duration_ms=180&in_time=2"
```

- Play sincronizzato con epoch (tra 5 secondi):

```powershell
$epoch = [math]::Round((Get-Date -Date (Get-Date).ToUniversalTime()).AddSeconds(5).Subtract([datetime]::UnixEpoch).TotalSeconds)
Invoke-RestMethod -Method POST http://127.0.0.1:8080/play -ContentType application/json -Body (@{ filename="s1.mp4"; in_time=$epoch; fade_out_seconds=0.4; fade_in_seconds=0.5 } | ConvertTo-Json)
```

### Esempi PowerShell: Overlay / Fast-start / Naming / Test

- Overlay: show/hide/fade

```powershell
# Overlay ON (nero pieno)
Invoke-RestMethod -Method POST "http://127.0.0.1:8080/overlay/show?alpha=1"

# Overlay OFF
Invoke-RestMethod -Method POST "http://127.0.0.1:8080/overlay/hide"

# Fade a 0 (trasparente) in 1s
Invoke-RestMethod -Method POST "http://127.0.0.1:8080/overlay/fade?target=0&seconds=1"

# Fade a 1 (nero) in 1s
Invoke-RestMethod -Method POST "http://127.0.0.1:8080/overlay/fade?target=1&seconds=1"
```

- Fast-start: prepare + go

```powershell
# Prepara la traccia (overlay nero a 1.0). Usa filename presente in media/
Invoke-RestMethod -Method POST http://127.0.0.1:8080/faststart/prepare -ContentType application/json -Body (@{ filename="s1.mp4" } | ConvertTo-Json)

# GO con fade-out overlay 1s
Invoke-RestMethod -Method POST "http://127.0.0.1:8080/faststart/go?seconds=1"
```

- Naming dispositivo (persistente)

```powershell
# Leggi nome
Invoke-RestMethod -Method GET http://127.0.0.1:8080/device/name | ConvertTo-Json -Depth 3

# Imposta nome
Invoke-RestMethod -Method POST http://127.0.0.1:8080/device/name -ContentType application/json -Body (@{ name="Player-01" } | ConvertTo-Json)
```

- Modalità TEST visiva

```powershell
# Avvia pattern diagnostico (ferma playback/splash e genera linee in movimento)
Invoke-RestMethod -Method POST http://127.0.0.1:8080/test/on

# Ferma TEST e ripristina splash
Invoke-RestMethod -Method POST http://127.0.0.1:8080/test/off
```

- Framework corrente e switch programmato (se backend disponibili):

```powershell
Invoke-RestMethod -Method GET http://127.0.0.1:8080/framework | ConvertTo-Json -Depth 3
Invoke-RestMethod -Method POST http://127.0.0.1:8080/change_framework -ContentType application/json -Body (@{ name="mpv"; in_time=2 } | ConvertTo-Json)
```

- Abilita UDP a runtime (persistito in config.json):

```powershell
Invoke-RestMethod -Method POST http://127.0.0.1:8080/settings/reload -ContentType application/json -Body (@{ UDP_ENABLED=$true } | ConvertTo-Json)
```

- Invio comando UDP (porta 7777) da PowerShell:

```powershell
$client = [System.Net.Sockets.UdpClient]::new()
$ep = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse("127.0.0.1"), 7777)
$json = '{"cmd":"play","filename":"s1.mp4","fade_in_seconds":0.5,"in_time":2}'
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
$client.Send($bytes, $bytes.Length, $ep) | Out-Null
$client.Close()
```

Esempi UDP aggiuntivi (PowerShell):

```powershell
# play_at tra 3s (delay relativo) con fade-in 0.5s
$json = '{"cmd":"play_at","filename":"s1.mp4","at":3,"fade_in_seconds":0.5}'
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
$client = [System.Net.Sockets.UdpClient]::new()
$ep = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse("127.0.0.1"), 7777)
$client.Send($bytes, $bytes.Length, $ep) | Out-Null
$client.Close()

# fast-start: prepare + go
$client = [System.Net.Sockets.UdpClient]::new()
$ep = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse("192.168.1.167"), 7777)
$client.Send([System.Text.Encoding]::UTF8.GetBytes('{"cmd":"faststart_prepare","filename":"s1.mp4"}'), 1000, $ep) | Out-Null
Start-Sleep -Milliseconds 200
$client.Send([System.Text.Encoding]::UTF8.GetBytes('{"cmd":"faststart_go","seconds":1}'), 1000, $ep) | Out-Null
$client.Close()
```

Nota: molte funzionalità video richiedono Linux con GStreamer installato via pacchetti di sistema. Su Windows si testano principalmente le API.

## Esempi curl rapidi (Linux/macOS)

Play immediato (filename presente in `media/`):

```bash
curl -sS -X POST http://<PLAYER_IP>:8080/play \
  -H 'Content-Type: application/json' \
  -d '{"filename":"testbars_10s.mp4","fade_in_seconds":0.5}'
```

Play sincronizzato tra 5 secondi (epoch):

```bash
EPOCH=$(date -u +%s); EPOCH=$((EPOCH+5))
curl -sS -X POST http://<PLAYER_IP>:8080/play \
  -H 'Content-Type: application/json' \
  -d '{"filename":"testbars_10s.mp4","in_time":'"$EPOCH"',"fade_in_seconds":0.5}'
```

Overlay fade a 0 in 1s:

```bash
curl -sS -X POST "http://<PLAYER_IP>:8080/overlay/fade?target=0&seconds=1"
```

## Massive Updater: uso rapido (Windows PowerShell)
Serve la repo via HTTP e orchestra update/sync sui device.

```powershell
cd "g:\My Drive\Lavori\2025\Maroccos\headless-player"
python .\tools\massive_updater.py
```

Suggerimenti:
- Conferma la sincronizzazione media alla prima esecuzione per allineare i file.
- Lo switch backend può essere coordinato (Y) e viene programmato con in_time.
- L’URL update tipico è `http://<IP_PC>:8000/releases/latest.zip`.

Esempio: download update sincronizzato tra 3s, poi apply e riavvio tra 10s:

```powershell
$pcIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '127|169\.254' } | Select-Object -First 1 -ExpandProperty IPAddress)
$updateUrl = "http://$pcIp:8000/releases/latest.zip"
Invoke-RestMethod -Method POST "http://<PLAYER_IP>:8080/download_update" -ContentType application/json -Body (@{ url=$updateUrl; version="vX.Y.Z"; start_at=([math]::Round((Get-Date).ToUniversalTime().AddSeconds(3).Subtract([datetime]::UnixEpoch).TotalSeconds)) } | ConvertTo-Json)
Invoke-RestMethod -Method POST "http://<PLAYER_IP>:8080/update" -ContentType application/json -Body (@{ restart=$true; start_at=([math]::Round((Get-Date).ToUniversalTime().AddSeconds(10).Subtract([datetime]::UnixEpoch).TotalSeconds)) } | ConvertTo-Json)
```

### API Download/Update del player (dettaglio)

- `POST /download_update` avvia lo scaricamento del pacchetto nella directory temporanea del device.
  - Body JSON: `{ "url": "http://PC:8000/releases/latest.zip", "version": "vX.Y.Z", "callback_url": null, "start_at": <epoch|delay>|null }`
  - Effettua controlli base (anti-HTML) e aggiorna i campi di stato: `update_status=downloading`, `update_progress`, `update_bytes_*`.
  - Se `start_at` è fornito, attende prima di iniziare lo scaricamento.

- `POST /update` applica il pacchetto precedentemente scaricato.
  - Body JSON: `{ "restart": true|false, "start_at": <epoch|delay>|null }`
  - Stato: `update_status=applying` → `ok` oppure `error` con `update_error`.
  - Se `restart=true`, il servizio si riavvia al termine (riappare lo splash e, se idle con CVLC, il nero).

- `GET /status` espone i campi di tracking (vedi sezione dedicata) per orchestrare download/apply da strumenti esterni.

### Troubleshooting update (apply)

- Errore: `Apply FAIL (HTTP n/a - [Errno 1] Operation not permitted)` oppure `update_error: Operation not permitted`
  - Causa probabile: il pacchetto includeva l'ambiente virtuale locale (`headless_venv/`) o file non scrivibili dall'utente del servizio sul device. Aggiornare un venv non è supportato e spesso fallisce per permessi/lock.
  - Fix implementato: il builder e l'applicatore ora ESCLUDONO `headless_venv/` dal pacchetto e dall'apply. Rigenera il bundle e riprova l'update.
  - Se l'errore persiste: verifica i permessi della cartella dell'app sul device (l'utente che esegue il servizio deve poter scrivere in `APP_DIR`, escluso il venv). Puoi lanciare una sistemazione automatica (se configurato sudoers) via:
    - `POST /maintenance/run_setup` per eseguire `setup.sh` con privilegi e correggere owner/permessi.
  - Best practice: non versionare né distribuire venv. L'ambiente deve essere creato/aggiornato on-device da `setup.sh`.

Esempio PowerShell (singolo device):

```powershell
$pcIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '127|169\.254' } | Select-Object -First 1 -ExpandProperty IPAddress)
$updateUrl = "http://$pcIp:8000/releases/latest.zip"
Invoke-RestMethod -Method POST "http://<PLAYER_IP>:8080/download_update" -ContentType application/json -Body (@{ url=$updateUrl; version="vX.Y.Z" } | ConvertTo-Json)
Start-Sleep -Seconds 1
Invoke-RestMethod -Method POST "http://<PLAYER_IP>:8080/update" -ContentType application/json -Body (@{ restart=$true } | ConvertTo-Json)
```

## Troubleshooting rapido
- Porta API: 8080. Se non risponde, verifica firewall o che l’app sia in esecuzione.
- Su Raspberry/Linux: se `kmssink` fallisce, il player fa fallback ad `autovideosink` e logga il motivo.
- Se un asset scaricato è HTML (404), i log mostreranno un avviso; verifica l’URL.
- Per sincronizzazioni multi-device, mantieni l’orologio sincronizzato (NTP).

Extra:
- Se l’immagine nera non appare in idle con `cvlc`, verifica che `VLC_EXTRA_ARGS` includa `--image-duration=36000` (iniettato automaticamente dal servizio) e che l’asset `media/black_1280_720.png` sia presente (viene generato automaticamente se assente).

## Sincronizzazione multi-device (linee guida)
- Sincronizza i clock dei device con NTP o PTP (necessario per `play_at` preciso tra device).
- Schedula con epoch futuro (>= 1e9). Calcola lo stesso epoch su un sistema “referenza” e invialo a tutti i player.
- Usa `play_at` via UDP o `/play` con `in_time`. Esempio (PowerShell già incluso sopra) o con curl: calcola EPOCH+5 e invia a tutti.
- Se serve un fade-out sul contenuto attuale prima dello start, passa `fade_out_seconds` nel body: verrà anticipato per concludere all’orario esatto.

## Deploy sicuro (best practice)
- Due fasi: 1) `POST /download_update` su tutti i device; 2) attendi che ciascuno riporti `update_status=downloaded` o `update_progress=100`; 3) `POST /update` (eventualmente con `start_at` comune) e, se necessario, `restart=true`.
- In caso di orchestrazione distribuita, preferisci pianificare download/apply con `start_at` epoch condiviso per ridurre jitter.

## License
Progetto interno / uso controllato.
