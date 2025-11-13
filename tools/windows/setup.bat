@echo off
setlocal enableextensions

echo ==============================================
echo  Morocco Player - Setup script (Windows)
echo  - Copia lo script di provisioning su C:\PlayerSetup
echo  - Opzionale: esegue ora oppure al prossimo avvio
echo ==============================================

:: Verifica privilegi amministrativi
whoami /groups | find "S-1-16-12288" >nul
if errorlevel 1 (
  echo [ERRORE] Eseguire come Amministratore.
  pause
  exit /b 1
)

set DEST=C:\PlayerSetup
if not exist "%DEST%" mkdir "%DEST%"

:: Copia lo script PowerShell nel target
set SRC=%~dp0
copy /Y "%SRC%provision_player.ps1" "%DEST%\provision_player.ps1" >nul
if errorlevel 1 (
  echo [ERRORE] Copia di provision_player.ps1 fallita
  pause
  exit /b 1
)

echo.
echo Script copiato in %DEST%\provision_player.ps1
echo.
echo Scegli un'opzione:
echo   [1] Esegui ORA il provisioning
echo   [2] Esegui al PROSSIMO AVVIO (RunOnce)
echo   [3] Solo copia (non eseguire)
choice /c 123 /n /m "Seleziona 1,2,3: "
set SEL=%errorlevel%

if "%SEL%"=="1" goto RUN_NOW
if "%SEL%"=="2" goto RUN_ONCE
goto END

:RUN_NOW
powershell -ExecutionPolicy Bypass -NoProfile -File "%DEST%\provision_player.ps1"
goto END

:RUN_ONCE
:: Imposta chiave RunOnce per avviare PowerShell con il provisioning al prossimo logon
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" /v PlayerProvision /t REG_SZ /d "powershell -ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File \"%DEST%\\provision_player.ps1\"" /f >nul
echo Impostato avvio al prossimo logon.
echo Consigliato: RIAVVIARE ora.
pause
goto END

:END
echo Fatto.
pause
endlocal
