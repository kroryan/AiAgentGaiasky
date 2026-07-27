@echo off
REM One-step installer for Windows: creates a virtual environment, installs
REM dependencies, enables Gaia Sky's REST API if it isn't already, and sets the
REM assistant to start automatically at login (a shortcut in the Startup folder).
REM
REM Safe to re-run: every step is skipped if it was already done.
REM
REM Usage:
REM   install.bat
REM   set GAIASKY_PORT=30010 && install.bat

setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if "%GAIASKY_PORT%"=="" set GAIASKY_PORT=30007

echo == Gaia Sky AI Agent installer ==
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo python was not found on PATH. Install Python 3.10+ from python.org and re-run this script.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment ^(.venv^)...
    python -m venv .venv
) else (
    echo Virtual environment already exists.
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

echo.
echo Checking Gaia Sky's REST API...
".venv\Scripts\python.exe" scripts\enable_rest_api.py %GAIASKY_PORT%

echo.
echo Setting up autostart...

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_DIR%\GaiaSkyAIAgent.bat"

if exist "%SHORTCUT%" (
    echo Autostart entry already exists at "%SHORTCUT%".
) else (
    (
        echo @echo off
        echo cd /d "%SCRIPT_DIR%"
        echo start "" "%SCRIPT_DIR%.venv\Scripts\pythonw.exe" "%SCRIPT_DIR%run.py"
    ) > "%SHORTCUT%"
    echo Installed an autostart entry at "%SHORTCUT%".
)

echo.
echo Done. The assistant will start automatically at your next login, and will wait
echo for Gaia Sky whenever you start it.
echo.
echo To start it right now instead of waiting for next login:
echo   ".venv\Scripts\pythonw.exe" run.py

endlocal
