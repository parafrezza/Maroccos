## Selezione bundle per OS

Il backend ora espone nel JSON di `/status` i campi:

- `os`: uno tra `windows`, `linux`, `raspberrypi`, `darwin`, `unknown`
- `arch`: architettura restituita da `platform.machine()` (es. `x86_64`, `armv7l`, `aarch64`)

La GUI deve usare questa informazione per proporre e/o scaricare il pacchetto corretto:

1. Se `os == "windows"` e l'update disponibile è un installer (`.exe` / `.msi`), usare quel file; il backend lo lancerà in modo silenzioso e farà exit.
2. Se `os == "raspberrypi"` usare il bundle zip/tar generato da `make_bundle.py` (o il pacchetto `.tar.gz` prodotto da `package_release.sh`).
3. Per `linux` generico (non Raspberry) usare il bundle zip (stessa logica Raspberry ma senza ottimizzazioni HW specifiche).
4. Per `darwin` (macOS) in assenza di un installer dedicato usare il bundle zip.

Strategia suggerita lato GUI:

```
status = GET /status
os = status.os
if os == 'windows': mostra pulsante "Scarica installer"; al download POST /update (il server lancerà l'exe)
else: mostra pulsante "Aggiorna" che scarica il bundle zip e poi POST /update
```

Nel JSON `/status` trovate inoltre:

- `version_current`
- `version_available`
- `update_status` (idle|downloading|applying|ok|error)
- `last_apply_http` (HTTP dell'ultimo tentativo di apply)
- `error_type` se presente

Questi campi permettono alla GUI di distinguere fallimenti di rete, permessi, o formato pacchetto. In caso di `error_type == PermissionError` suggerire all'utente di disabilitare antivirus / eseguire come amministratore.

# 🚀 Massive Updater - Sistema di Aggiornamento Massivo Player

Sistema automatico per l'aggiornamento massivo dei player sulla rete locale. Rileva automaticamente i player, costruisce i bundle di aggiornamento e applica gli update con monitoraggio in tempo reale.

## 📋 Caratteristiche

- ✅ **Discovery automatica** dei player sulla rete
- ✅ **Build automatica** dei bundle di aggiornamento  
- ✅ **Update a 2 fasi** (download + apply) con riavvio automatico
- ✅ **Monitoraggio progresso** in tempo reale
- ✅ **Verbosità configurabile** per debug
- ✅ **Gestione errori completa** con fallback
- ✅ **Verifica post-update** delle versioni

## 🔧 Requisiti

```bash
pip install requests
```

## 🎯 Uso Basico

### Comando più semplice (tutto automatico)
```bash
python massive_update.py
```
Questo comando:
1. Rileva automaticamente le reti locali
2. Scansiona i player sulla porta 8080
3. Costruisce il bundle più recente
4. Chiede conferma prima dell'update
5. Applica l'update a tutti i player trovati

### Update con conferma automatica
```bash
python massive_update.py --auto-yes
```

### Update con verbosità per debug
```bash
python massive_update.py --verbose
```

## 📡 Opzioni di Discovery

### Scansione rete specifica
```bash
# Singola rete
python massive_update.py --scan "192.168.1.0/24"

# Multiple reti
python massive_update.py --scan "192.168.1.0/24,10.0.0.0/24"
```

### Target specifici (salta discovery)
```bash
# IP singolo
python massive_update.py --no-scan --targets "192.168.1.100"

# Multiple IP
python massive_update.py --no-scan --targets "192.168.1.100,192.168.1.101,192.168.1.102"
```

### Porta personalizzata
```bash
python massive_update.py --port-player 8081
```

## 📦 Opzioni Bundle

### Usa bundle esistente (salta build)
```bash
python massive_update.py --no-build
```

### Bundle da file specifico
```bash
python massive_update.py --bundle-path "headless-player/releases/bundle_v0.1.46_abc123.zip"
```

### Bundle da URL remoto
```bash
python massive_update.py --bundle-url "http://server.com/updates/bundle_v0.1.47_def456.zip"
```

## ⚙️ Configurazione Avanzata

### Timeout e performance
```bash
# Timeout più lungo per reti lente
python massive_update.py --timeout 5.0

# Più thread per reti veloci
python massive_update.py --threads 100

# Porta server locale personalizzata
python massive_update.py --serve-port 9000
```

### Con API Key (se i player la richiedono)
```bash
python massive_update.py --api-key "your-secret-key"
```

### Verifica post-update
```bash
# Verifica che tutti i player abbiano la nuova versione
python massive_update.py --verify

# Timeout più lungo per la verifica
python massive_update.py --verify --verify-timeout 180
```

## 🔍 Debug e Troubleshooting

### Verbosità completa
```bash
python massive_update.py --verbose
```
Mostra:
- Discovery dettagliato di ogni IP
- Richieste HTTP complete (headers, payload, response)
- Stato di download e apply per ogni player
- Errori dettagliati con tipo e causa

### Test singolo player
```bash
python massive_update.py --verbose --no-scan --targets "192.168.1.100" --auto-yes
```

### Solo discovery (senza update)
```bash
python massive_update.py --no-build --no-scan --targets "192.168.1.100"
# Interrompi con Ctrl+C dopo la discovery
```

## 📊 Esempi Pratici

### Scenario 1: Update di produzione
```bash
# Discovery automatica, build bundle, conferma manuale
python massive_update.py --verify
```

### Scenario 2: Test su ambiente specifico
```bash
# Target specifici con debug completo
python massive_update.py --verbose --no-scan --targets "192.168.1.10,192.168.1.11" --auto-yes
```

### Scenario 3: Update urgente con bundle preesistente
```bash
# Usa ultimo bundle senza rebuild
python massive_update.py --no-build --auto-yes --verify
```

### Scenario 4: Update rete lenta
```bash
# Timeout più lunghi, meno thread
python massive_update.py --timeout 10.0 --threads 20 --verify-timeout 300
```

## 🔄 Flusso di Update

Il sistema implementa un update a **2 fasi**:

### Fase 1: Download Bundle
- **Endpoint**: `POST /download_update`
- **Payload**: `{"version": "v0.1.47", "url": "http://...", "sha256": "..."}`
- **Risultato**: Il player scarica il bundle e lo salva come `update_pkg.bin`

### Fase 2: Apply Update
- **Endpoint**: `POST /update`  
- **Payload**: `{"restart": true}`
- **Risultato**: Il player applica l'update e si riavvia automaticamente

## 📈 Monitoraggio Progresso

Durante l'update viene mostrata una tabella in tempo reale:

```
PROGRESSO UPDATE (polling)
IP             | %   | PHASE      | MSG
--------------------------------------------------------------------
  192.168.1.42 |  50% | download   | Downloading bundle...
 192.168.1.204 | 100% | install    | Installing update...
```

**Fasi possibili:**
- `idle`: Player in attesa
- `download`: Download del bundle in corso
- `install`: Installazione in corso  
- `ok`: Update completato con successo
- `error`: Errore durante l'update
- `unreachable`: Player non raggiungibile

## ❌ Gestione Errori

### Errori comuni e soluzioni

#### "Nessun player trovato"
```bash
# Verifica rete e porta
python massive_update.py --verbose --scan "192.168.1.0/24" --port-player 8080

# Test manuale endpoint
curl http://192.168.1.100:8080/status
```

#### "Bundle non trovato"
```bash
# Verifica percorso
ls -la headless-player/releases/

# Usa bundle specifico
python massive_update.py --bundle-path "path/completo/bundle.zip"
```

#### "Connection timeout"
```bash
# Aumenta timeout
python massive_update.py --timeout 5.0

# Riduci thread per evitare sovraccarico rete
python massive_update.py --threads 30
```

#### "Player si sta riavviando (connessione chiusa durante apply)"
✅ **Questo è normale!** Indica che l'update ha avuto successo e il player si sta riavviando.

## 🏗️ Struttura File

```
massive_update.py          # Script principale
make_bundle.py            # Builder dei bundle
headless-player/          # Directory dell'applicazione
├── app.py               # Applicazione player
├── releases/            # Bundle di release
│   ├── bundle_v*.zip   # Bundle con hash
│   └── latest.zip      # Link al più recente
└── VERSION             # File versione
```

## 🔧 Parametri Completi

| Parametro | Default | Descrizione |
|-----------|---------|-------------|
| `--port-player` | 8080 | Porta HTTP dei player |
| `--scan` | auto | Reti da scansionare (CIDR) |
| `--api-key` | - | API key per endpoint admin |
| `--serve-port` | 8000 | Porta server locale bundle |
| `--timeout` | 2.0 | Timeout richieste (secondi) |
| `--threads` | 80 | Thread concorrenti per scan |
| `--no-scan` | false | Salta discovery automatica |
| `--targets` | - | Lista IP manuali |
| `--auto-yes` | false | Non chiedere conferma |
| `--no-build` | false | Non eseguire build bundle |
| `--bundle-path` | - | Percorso bundle locale |
| `--bundle-url` | - | URL bundle remoto |
| `--verify` | false | Verifica versioni post-update |
| `--verify-timeout` | 120 | Timeout verifica per player |
| `--verbose` | false | Output dettagliato debug |

## 🎨 Output Esempi

### Discovery automatica
```
[15:30:01] Espansione delle reti ['192.168.1.0/24'] in indirizzi IP...
[15:30:01] Espandendo rete 192.168.1.0/24 -> 192.168.1.0/24 (256 indirizzi)
[15:30:01] Inizio discovery su 254 indirizzi (porta 8080) threads=80 timeout=2.0s
[15:30:03] ✓ Trovato player 192.168.1.42 versione=v0.1.46
[15:30:04] ✓ Trovato player 192.168.1.204 versione=v0.1.46
[15:30:06] Discovery completata: 2 player trovati in 5.2s
```

### Update con successo
```
[15:30:15] Avvio update a 2 fasi per 2 player (version=v0.1.47)
[15:30:15] FASE 1: Download del bundle da http://192.168.1.44:8000/bundle_v0.1.47_abc123.zip
[15:30:16] ✓ Download 192.168.1.42: OK - Bundle scaricato
[15:30:16] ✓ Download 192.168.1.204: OK - Bundle scaricato
[15:30:17] ✓ Update 192.168.1.42: OK - Player si sta riavviando
[15:30:17] ✓ Update 192.168.1.204: OK - Player si sta riavviando
```

### Risultato finale
```
==================================================
RISULTATO UPDATE: 2/2 successi
==================================================
VERIFICA: 2/2 allineati a version=v0.1.47
```

---

## 📞 Supporto

Per problemi o domande:
1. Usa `--verbose` per debug dettagliato
2. Controlla la connettività di rete
3. Verifica che i player siano sulla porta corretta
4. Controlla i log del player per errori specifici

**Happy updating! 🚀**
