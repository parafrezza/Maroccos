GUI Morocco Player Manager
==========================

Novità
------

- Bottone "Seleziona/Upload…" nel riquadro "Media disponibili": apre un selettore file filtrato per immagini e video e invia direttamente l'upload ai device selezionati.
- La lista dei media (sia locali che sul device) mostra solo formati immagine/video supportati (es. mp4, mov, mkv, webm, png, jpg, jpeg, bmp, gif, webp, tiff).

Note
----

- Per usare l'upload è necessario avere almeno un device selezionato nella colonna di sinistra.
- La cartella media configurata in Settings viene usata come directory iniziale del selettore file quando disponibile.

Time sync (app-level)
---------------------
- La GUI avvia, per ogni player scoperto, un worker in background che stima lo skew dell'orologio interrogando il comando UDP `time` del device.
- Lo `skew_ms` misurato viene inserito nel payload di `/status` (`payload["timing"]`) prima che la UI lo utilizzi, quindi non servono modifiche alla UI.
- Porta UDP: `7777` (default). Nessuna configurazione richiesta.

Packaging the GUI
------------------

You can produce a one-file Windows executable with PyInstaller. From the repo root run the helper:

```powershell
powershell.exe -File tools/build_gui_exe.ps1 -ProjectRoot ".."
```

It installs PyInstaller (if needed), injects `settings.json` and `vncviewer64-1.15.0.exe`, and drops `dist\Maroccos-Player.exe`. The helper accepts parameters such as `-Entry` or `-Name` if you want to customize the target.

Alternatively, from `GUI/` you can run `python -m pyinstaller --noconfirm --clean --windowed --onefile --name marocco-manager --add-data "settings.json;." --add-data "vncviewer64-1.15.0.exe;." main.py`.

Optionally disable the automatic discovery at launch (which can open many pop-up windows when the app starts) by setting `GUI_SKIP_STARTUP_DISCOVERY=1` in the environment before launching the executable. This causes the GUI to skip the network scan until you manually hit “Scansiona”.
