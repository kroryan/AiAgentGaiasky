@echo off
REM Starts Gaia Sky and the AI Agent overlay together, as two independent processes.
REM Does not modify Gaia Sky in any way: it launches the .exe you already have, waits
REM for its REST API to answer, then starts the overlay pointed at it.
REM
REM Usage:
REM   set GAIASKY_BIN=C:\Path\To\Gaiasky.exe
REM   launch-with-gaiasky.bat
REM
REM Configuration (environment variables, all optional):
REM   GAIASKY_BIN   Path to the Gaia Sky executable. Default: gaiasky.exe (must be on PATH)
REM   GAIASKY_URL   REST base URL to wait for and connect to. Default: http://localhost:30007
REM   AGENT_DIR     Path to this repository's checkout. Default: parent of this script

setlocal

if "%GAIASKY_BIN%"=="" set GAIASKY_BIN=gaiasky.exe
if "%GAIASKY_URL%"=="" set GAIASKY_URL=http://localhost:30007
if "%AGENT_DIR%"=="" set AGENT_DIR=%~dp0..

echo Starting Gaia Sky (%GAIASKY_BIN%)...
start "" "%GAIASKY_BIN%"

echo Waiting for Gaia Sky's REST API at %GAIASKY_URL% ...
python "%AGENT_DIR%\scripts\wait_ready.py" "%GAIASKY_URL%"
if errorlevel 1 (
    echo Gaia Sky did not become ready in time.
    exit /b 1
)

echo Gaia Sky is ready. Starting the AI Agent overlay...
cd /d "%AGENT_DIR%"
python run.py --gaiasky "%GAIASKY_URL%"
