# Installer Windows (Inno Setup)

Questo installer crea e aggiorna marocco-player (OFF-player + headless-player) su Windows.

## AppId: formato corretto

In Inno Setup, quando si specifica un GUID come `AppId`, occorre raddoppiare le graffe:

- Corretto: `AppId={{E4C5E5A0-9D53-4F7F-9D84-3C82A1C6F0C2}}`
- Errato: `AppId={E4C5...}` (manca la graffa raddoppiata)

Lo script `tools/build_installer.ps1` valida il formato prima di invocare ISCC.

## Quoting per schtasks (/TR)

Per registrare un'attività pianificata che avvii il player, usare una sola coppia di virgolette attorno al percorso finale:

```
/TR "{app}\{#HeadlessDirName}\{#HeadlessExeName}"
```

In Inno Setup, una `"` si scrive raddoppiata nelle stringhe, quindi in `off-player.iss` troverai:

```
Parameters: "/Create ... /TR ""{app}\{#HeadlessDirName}\{#HeadlessExeName}"""
```

Evitare iper-quotature con molte coppie di `"` consecutive, che portano a errori del tipo:
- `Mismatched or misplaced quotes on parameter "Parameters"`

## Disinstallazione versione precedente (UI)

La procedura di setup rimuove eventuali versioni precedenti trovate nel registro (sia HKLM che HKCU, 32/64 bit). In modalità interattiva viene mostrata una pagina con log testuale:
- identificazione del prodotto da rimuovere
- comando di disinstallazione lanciato (`QuietUninstallString` o `UninstallString`)
- esito/exit code

In modalità silenziosa non viene mostrata la pagina; la disinstallazione parte in automatico con flag `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` se non già presenti.

## Log

L'output di ISCC viene salvato in `installer/logs/iscc-<versione>-<timestamp>.log`.
La pagina UI di disinstallazione mostra lo stesso testo (con timestamp) e i messaggi vengono anche scritti nel log di setup.

## Requisiti
- Inno Setup 6 (ISCC)
- .NET SDK (per compilare l'utility `SetResolution`)
- K-Lite Codec Pack (salvato come `installer/assets/KLite_Codec_Pack_Basic.exe`)

## Build da VS Code
Usa la task:
- `build windows installer release`

Oppure via PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_windows_installer.ps1
```

## Note
- In caso di errori di sintassi nel file `.iss`, consultare il log in `installer/logs/` per la riga incriminata.
- Se l'installer non disinstalla in automatico, verificare che il prodotto precedente esponga `QuietUninstallString` o `UninstallString` nel registro.# OFF-player Installer (Windows)

Questo folder contiene lo script Inno Setup per creare un installer Windows dell'app OFF-player insieme a headless-player.
La pipeline di build (task VS Code) gestisce anche:

- ricompila headless-player e OFF-player solo se necessario (smart skip)
- copia le DLL runtime accanto all'eseguibile di OFF-player
- genera automaticamente l'icona personalizzata (.ico) se mancante o più vecchia della sorgente
- invoca Inno Setup con i parametri corretti

## Come generare l'installer

Da VS Code, esegui il task dedicato (Release o Debug). In alternativa da PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\build_windows_installer.ps1
```

L'output viene scritto in `installer/dist/`.

Note:
- La versione è letta da `headless-player/VERSION` ed eventualmente incrementata automaticamente se headless viene ricompilato.
- Se presente `icon-maker\dist\morocco-player\morocco-player.ico`, l'installer e le scorciatoie useranno questa icona.
- Se l'icona non è presente, la build prosegue con le icone di default.

## Icona personalizzata (consigliato)

La pipeline usa l'icona generata da `icon-maker` se disponibile.

- Sorgente icona: `icon-maker/icon.png`
- Generazione manuale (facoltativa):

   ```powershell
   cd .\icon-maker
   ..\..\.venv\Scripts\python.exe build_app_icons.py --src icon.png --name morocco-player --outdir dist
   ```

- Output icona atteso: `icon-maker/dist/morocco-player/morocco-player.ico`
- La pipeline rigenera automaticamente l'icona se l'ICO manca o è più vecchio della PNG di origine.

Le scorciatoie Desktop e Start menu useranno l'icona copiata in `{app}\assets\morocco-player.ico`.

## Opzione: Installer con K-Lite (DirectShow)

1. Installa Inno Setup 6 (https://jrsoftware.org/isdl.php) — assicurati che `ISCC.exe` sia nel percorso:
   - Percorso predefinito: `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`.
2. Scarica l'installer K-Lite Codec Pack Basic e salvalo come `KLite_Codec_Pack_Basic.exe` in `installer/assets/`.
   - Verifica le condizioni di licenza di K-Lite per la ridistribuzione.
3. Da VS Code esegui il task di build installer, oppure da terminale:

```powershell
# facoltativo: se ISCC non è nel PATH, specifica il percorso
$env:ISCC = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
& $env:ISCC 'installer\off-player.iss'
```

4. L'installer sarà generato in `installer/dist/`.
5. Durante l'installazione, seleziona l'opzione "Installa anche i codec K-Lite" se desideri installarli.

Note:
- DirectShow (player di default in openFrameworks su Windows) richiede codec di sistema. K-Lite è una soluzione comune e gratuita; verifica sempre licenze e politiche di ridistribuzione.

## Opzione 2: Codec portabili (senza installazione di sistema)

Per evitare codec di sistema, puoi usare un backend player che porta i codec con sé, come:

- libVLC (VLC): distribuisci `libvlc.dll`, `libvlccore.dll` e la cartella `plugins` accanto all'eseguibile, e usa l'API C di libVLC per il playback. Non richiede installazione globale.
- mpv: simile a VLC; distribuisci le DLL e usa il suo API client.
- GStreamer: distribuisci il runtime e i plugin; configura variabili d'ambiente (`GST_PLUGIN_PATH`, `PATH`) e usa un backend GStreamer.

Queste opzioni richiedono un backend alternativo nel codice (non incluso in questo installer). Se vuoi, possiamo integrare un backend libVLC o mpv per rendere OFF-player auto-contenuto.

## Cosa include l'installer

- OFF-player (`OFF-player\bin\OFF-player.exe`)
- Tutte le DLL richieste accanto all'eseguibile (`OFF-player\bin\*.dll`)
- La cartella dati `OFF-player\bin\data\`
- headless-player preconfezionato (release) sotto `{app}\headless-player`
- (Se disponibile) icona custom in `{app}\assets\morocco-player.ico` usata per le scorciatoie
- (Opzionale) Esecuzione installer K-Lite in modalità silenziosa

## Troubleshooting

- Video nero / errore DirectShow codec: installa K-Lite oppure passa a un backend portabile (libVLC/mpv).
- Mancano DLL all'avvio: esegui il task "Copy DLLs Release (Windows)" per copiare le dipendenze MSYS2 accanto all'eseguibile.
- ISCC non trovato: imposta la variabile d'ambiente `ISCC` con il percorso a `ISCC.exe`.
 - Icona non applicata: verifica che `icon-maker\dist\morocco-player\morocco-player.ico` esista prima della compilazione e che Inno Setup lo rilevi (il log mostrerà la definizione `IcoSrcPath`).
