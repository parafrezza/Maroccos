# OFF-player - VS Code Configuration

Il progetto OFF-player nel workspace Maroccos è ora configurato per essere compilato e debuggato direttamente in VS Code.

## Workspace Structure

Questo è un workspace VS Code multi-progetto che include:
- **GUI** - Interfaccia Python/PySide6
- **headless-player** - Player Python headless
- **OFF-player** - Player openFrameworks C++ (questo documento)

I file di configurazione VS Code sono nella root del workspace (`Maroccos/.vscode/`).

## Prerequisiti

1. **Xcode Command Line Tools** (già installato)
2. **VS Code Extensions richieste**:
   - `C/C++` (ms-vscode.cpptools) - già installata ✓
   - `CodeLLDB` (vadimcn.vscode-lldb) - raccomandata per debugging

Per installare CodeLLDB, clicca su questo link o cercala nel marketplace:
```
vadimcn.vscode-lldb
```

## Compilazione

### Da VS Code

#### Tramite Command Palette (⌘+Shift+P):
1. Premi `⌘+Shift+P`
2. Digita "Tasks: Run Build Task"
3. Seleziona "Build Release" (è il task predefinito)

#### Tramite Shortcut:
- **Build Release**: `⌘+Shift+B` (shortcut predefinito per build)
- Altri task disponibili tramite Command Palette > "Tasks: Run Task"

#### Task Disponibili:
- **Build OFF-player Release** - Compila in modalità release ottimizzata
- **Clean OFF-player** - Pulisce i file compilati
- **Clean and Build OFF-player Release** - Pulisce e ricompila
- **Run OFF-player Release** - Esegue l'applicazione release

### Da Terminale

Puoi sempre usare i comandi make tradizionali dalla cartella `OFF-player`:
```bash
cd OFF-player
make Release        # Compila in release
make clean          # Pulisce
make RunRelease     # Esegue release build
```

## Debugging

### Configurazioni Disponibili:

1. **Debug OFF-player** (raccomandata)
   - Compila automaticamente prima di debuggare
   - Avvia in modalità debug con breakpoint
   - Premi `F5` per avviare

2. **Run OFF-player (no build)**
   - Esegue senza ricompilare
   - Utile per test rapidi
   - Seleziona dalla sidebar Debug

3. **Release OFF-player**
   - Compila e esegue versione release
   - Per test di performance

### Come Debuggare:

1. Apri un file sorgente (es. `src/ofApp.cpp`)
2. Clicca sul margine sinistro per aggiungere breakpoint
3. Premi `F5` o vai su Run > Start Debugging
4. L'applicazione si fermerà ai breakpoint

### Shortcut Utili durante Debug:

- `F5` - Continue
- `F10` - Step Over
- `F11` - Step Into
- `Shift+F11` - Step Out
- `⌘+Shift+F5` - Restart
- `Shift+F5` - Stop

## IntelliSense

L'IntelliSense C++ è configurato con tutti i path corretti per:
- openFrameworks 0.12.1
- Addon ofxNetwork (UDP)
- Addon ofxPoco (HTTP server Poco)
- Tutte le librerie macOS frameworks

Dovresti vedere:
- ✅ Autocompletamento
- ✅ Definizioni al passaggio del mouse
- ✅ Vai a definizione (⌘+Click)
- ✅ Errori di sintassi in tempo reale

## Struttura File di Configurazione

```
Maroccos/
├── .vscode/
│   ├── c_cpp_properties.json  # Configurazione IntelliSense C++
│   ├── launch.json            # Configurazioni di debug
│   ├── settings.json          # Impostazioni workspace (Python + C++)
│   └── tasks.json             # Task di compilazione OFF-player
├── Maroccos.code-workspace    # File workspace VS Code
├── OFF-player/                # Progetto openFrameworks
├── GUI/                       # Progetto Python GUI
└── headless-player/           # Progetto Python headless
```

## Risoluzione Problemi

### IntelliSense non funziona
- Premi `⌘+Shift+P` > "C/C++: Reset IntelliSense Database"
- Riavvia VS Code

### Debugging non funziona
- Installa l'estensione CodeLLDB (vadimcn.vscode-lldb)
- Verifica che il progetto sia stato compilato: `make Release`

### Errori di compilazione
- Pulisci e ricompila: Task "Clean and Build OFF-player Release"
- Verifica che openFrameworks sia nella posizione corretta: `OFF-ROOT/of_v0.12.1_osx_release/`
- Assicurati di essere nella directory corretta del workspace Maroccos

## Addons Utilizzati

- **ofxNetwork** - Comunicazione UDP
- **ofxPoco** - Server HTTP con Poco C++ Libraries

⚠️ **Nota**: Gli addon `ofxIO` e `ofxHTTP` sono stati rimossi per incompatibilità con C++17/23.

## Modifiche Applicate per Compatibilità

Per far funzionare il progetto con C++23 su macOS sono state necessarie queste modifiche:

1. **HttpControlServer** - Implementazione completa con Poco
2. **ofApp.cpp** - Custom `endsWith()` e manual JSON escaping
3. **ofxPoco addon_config.mk** - Esclusi file sorgente da `libs/poco/lib/`
4. **addons.make** - Rimossi ofxIO e ofxHTTP, aggiunti ofxNetwork e ofxPoco

## Esecuzione Manuale

Se preferisci eseguire senza VS Code (macOS bundle):
```bash
cd OFF-player/bin/OFF-player.app/Contents/MacOS/
./OFF-player
```

O semplicemente dalla cartella OFF-player:
```bash
cd OFF-player
make RunDebug
```
