@echo off
REM Reverses install.bat: stops the running assistant and removes the Startup folder
REM shortcut, and optionally removes the virtual environment.
REM Does NOT touch Gaia Sky's config.yaml; restPort is left as it is.
REM
REM Usage:
REM   uninstall.bat            (removes autostart entry only)
REM   uninstall.bat --purge    (also deletes the .venv directory)

setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo == Gaia Sky AI Agent uninstaller ==
echo.

set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GaiaSkyAIAgent.bat"
if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo Removed "%SHORTCUT%".
) else (
    echo No autostart entry found.
)

taskkill /F /FI "IMAGENAME eq pythonw.exe" /FI "WINDOWTITLE eq Gaia Sky AI Agent*" >nul 2>nul

if "%1"=="--purge" (
    if exist ".venv" (
        rmdir /s /q ".venv"
        echo Removed the virtual environment ^(.venv^).
    )
)

echo.
echo Done. The assistant will no longer start automatically.
if not "%1"=="--purge" (
    echo The virtual environment ^(.venv^) was left in place; re-run with --purge to remove it too.
)
echo Gaia Sky's config.yaml was left untouched ^(restPort is still enabled^).

endlocal
