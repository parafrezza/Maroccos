# Maroccos Workspace - VS Code Setup

Questo è un workspace VS Code multi-progetto che gestisce diversi componenti:

## Progetti

### 🎨 GUI
Interfaccia grafica Python/PySide6 per il controllo del player.
- Path: `GUI/`
- Linguaggio: Python
- Framework: PySide6

### 🎬 headless-player
Player video headless con backend VLC/GStreamer.
- Path: `headless-player/`
- Linguaggio: Python
- Backend: VLC, GStreamer, MPV

### 🖼️ OFF-player
Player video openFrameworks con controllo UDP e HTTP.
- Path: `OFF-player/`
- Linguaggio: C++
- Framework: openFrameworks 0.12.1
- Addons: ofxNetwork, ofxPoco

## Configurazione VS Code

I file di configurazione sono centralizzati in `.vscode/`:

```
.vscode/
├── c_cpp_properties.json  # IntelliSense per C++ e Python
├── launch.json            # Debug configurations per OFF-player
├── settings.json          # Settings unificati per tutto il workspace
└── tasks.json             # Build tasks per OFF-player
```

## Quick Start

### Compilare OFF-player
1. Apri il workspace in VS Code: `Maroccos.code-workspace`
2. Premi `⌘+Shift+B` per compilare
3. Seleziona "Build OFF-player Debug"

### Debuggare OFF-player
1. Apri un file C++ in `OFF-player/src/`
2. Aggiungi breakpoint
3. Premi `F5`
4. Seleziona "Debug OFF-player (cppdbg)" o "(LLDB)"

### Eseguire Python Projects
```bash
# GUI
cd GUI
python3 main.py

# Headless Player
cd headless-player
python3 app.py
```

## Documentazione Dettagliata

- **OFF-player Setup**: Vedi [VSCODE_OFF_PLAYER.md](./VSCODE_OFF_PLAYER.md)
- **GUI**: Vedi `GUI/README.md`
- **Headless Player**: Vedi `headless-player/README.md`

## Estensioni Raccomandate

### Già Installate ✅
- C/C++ (ms-vscode.cpptools)
- CMake Tools
- Python

### Consigliate
```vscode-extensions
vadimcn.vscode-lldb
```
CodeLLDB - Per debugging nativo migliorato su macOS

## Struttura del Workspace

```
Maroccos/
├── .vscode/              # Configurazione VS Code centralizzata
├── GUI/                  # Interfaccia grafica Python
├── headless-player/      # Player headless Python
├── OFF-player/           # Player openFrameworks C++
├── OFF-ROOT/             # openFrameworks SDK (escluso da git)
├── tools/                # Utility varie
└── Maroccos.code-workspace  # File workspace VS Code
```

## Note

- `OFF-ROOT/` contiene openFrameworks e non è versionato
- I progetti Python usano virtual environment separati
- OFF-player è configurato per C++23 su macOS

## Build multi-piattaforma

Il workspace include task per compilare su più piattaforme:

- macOS: build locale tramite `make` in `OFF-player` (Debug/Release).
- Raspberry Pi (ARM): build remota via SSH. Alla prima esecuzione ti verrà chiesto l'host (es. `pi@raspberrypi.local`) e il percorso della repo sul RPi. Assicurati che sul dispositivo siano presenti toolchain e openFrameworks per ARM (`OFF-ROOT/of_v0.12.1_linuxarmv7l_release`). Il task imposta `OF_ROOT` a quel percorso (adattalo se diverso).
- Windows: task placeholder per build remota. Su Windows sono consigliate due strade:
	1) Generare la soluzione Visual Studio con il Project Generator di openFrameworks e compilare con `msbuild`.
	2) Usare MSYS2/MinGW e `make`. In entrambi i casi, la build nativa su Windows è la via più lineare.

Note su cross-compilazione:

- macOS → Windows: poco pratico con openFrameworks; richiede toolchain MinGW-w64 e patch. Consigliata build nativa.
- macOS → Raspberry Pi: supportata da Linux tramite toolchain cross-ARM fornite da openFrameworks. Da macOS è più semplice usare un RPi remoto o una VM Linux.

Suggerimenti:

- Valuta VS Code Remote SSH per edit/build direttamente su RPi/Windows.
- In alternativa, configura workflow CI (es. GitHub Actions) con job separati per OS/architettura.
