@echo off
set LOGFILE="C:\Program Files\marocco-player\headless-player\marocco_crash_log.txt"

timeout /T 15 /NOBREAK >NUL 2>&1

pushd "C:\Program Files\marocco-player\"
start "" "headless-player\headless-player.exe" > %LOGFILE% 2>&1
echo.
echo Codice di uscita (ERRORLEVEL): %ERRORLEVEL% >> %LOGFILE%
popd