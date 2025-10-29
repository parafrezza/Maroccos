@echo off
REM =============================================================================
REM Script di installazione per il player headless su Windows
REM
REM Questo script prepara un ambiente per il backend headless del progetto
REM Maroccos, installando le dipendenze necessarie e opzionali per
REM riprodurre video in modalità senza interfaccia grafica con transizioni.
REM Eseguire questo script dalla radice del repository clonato.
REM =============================================================================

REM Verifica la presenza di Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python non trovato. Installa Python 3.10 o superiore da https://www.python.org/ e riprova.
    exit /b 1
)

REM Crea un ambiente virtuale dedicato al player headless
if not exist headless_venv (
    python -m venv headless_venv
    if %errorlevel% neq 0 (
        echo Errore nel creare l'ambiente virtuale headless_venv.
        exit /b 1
    )
)

REM Attiva l'ambiente virtuale
call headless_venv\Scripts\activate

REM Aggiorna pip
python -m pip install --upgrade pip

REM Installa le dipendenze principali del player headless
REM Su Windows usiamo 'psutil' invece di 'netifaces' per evitare la compilazione di estensioni C
pip install --upgrade fastapi uvicorn[standard] pillow psutil requests

REM Installa i backend video consigliati
REM python-vlc fornisce i binding a libvlc (VLC) e consente la riproduzione video headless
pip install --upgrade python-vlc

REM PyQt5 viene usato come backend per la finestra a schermo intero e per la gestione delle transizioni tramite QGraphicsOpacityEffect
pip install --upgrade PyQt5

REM Facoltativo: installare anche MPV (libmpv) tramite il wrapper python 'pympv' per un backend alternativo
pip install --upgrade pympv || echo "Installazione di pympv non riuscita: potrebbe non essere disponibile per la tua piattaforma"

REM Opzionale: GStreamer (PyGObject/gi)
REM GStreamer su Windows richiede l'installazione del runtime GStreamer (MSI). Questo script
REM non installa automaticamente il runtime: se vuoi usare il backend GStreamer, scegli 'y'
REM quando richiesto e segui le istruzioni. Se scegli 'n' lo skip sarà automatico.
set /p INSTALL_GST="Vuoi installare le dipendenze Python per GStreamer (opzionale)? [y/N] "
if /I "%INSTALL_GST%"=="y" (
    echo Attenzione: installazione del runtime GStreamer non automatica.
    echo Vai a https://gstreamer.freedesktop.org/download/ e installa la versione "MSVC" run-time (Runtime installer).
    echo Dopo aver installato il runtime, premi invio per continuare con l'installazione dei binding Python (PyGObject).
    pause >nul
    REM Tentativo di installare PyGObject (gi) tramite pip. Su Windows potrebbe essere necessario usare pacchetti binari o MSYS/GTK dev.
    pip install --upgrade pygobject==3.42.0 || pip install --upgrade gi || echo "Installazione PyGObject fallita: assicurati di aver installato il runtime GStreamer e i pacchetti di sviluppo necessari."
) else (
    echo Skipping GStreamer/PyGObject installation.
)

REM Messaggi finali
echo.
echo ** Attenzione **
echo Per utilizzare python-vlc e riprodurre video, è necessario avere VLC per Windows installato sul sistema.
echo Puoi scaricare l'ultima versione da: https://www.videolan.org/vlc/ (installer MSI).
echo Dopo l'installazione assicurati che libvlc.dll sia nel PATH oppure copia i file della libreria nella cartella del progetto.
echo.
echo Installazione completata. Per avviare il server headless, attiva l'ambiente con:
echo   headless_venv\Scripts\activate
echo poi avvia Uvicorn per l'applicazione FastAPI:
echo   uvicorn headless-player.app:app --host 0.0.0.0 --port 8080

echo Per registrare il player come servizio su Windows, puoi utilizzare uno strumento come NSSM:
echo   nssm install MaroccosHeadless "C:\percorso\al\python.exe" "C:\percorso\al\headless-player\app.py"
echo quindi configurare la cartella di lavoro e impostare l'avvio automatico.
