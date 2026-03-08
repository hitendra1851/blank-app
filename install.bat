@echo off
:: ============================================================
:: install.bat
:: Registers the Teams Chat Export Service as a Windows Task
:: that runs every 30 minutes (or whatever is in config.json).
::
:: Run this script ONCE as Administrator.
:: ============================================================

setlocal EnableDelayedExpansion

:: ---- Paths ----
set SCRIPT_DIR=%~dp0
set PYTHON_EXE=python
set SERVICE_SCRIPT=%SCRIPT_DIR%service.py
set TASK_NAME=TeamsChatExportService

echo.
echo =====================================================
echo  Microsoft Teams Chat Export - Task Scheduler Setup
echo =====================================================
echo.

:: Check Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Please install Python 3.8+ from https://python.org and add it to PATH.
    pause
    exit /b 1
)

:: Install Python dependencies
echo [1/3] Installing Python dependencies...
pip install plyvel-wheels schedule >nul 2>&1
if errorlevel 1 (
    echo [WARN] pip install had errors - continuing anyway.
) else (
    echo       Done.
)

:: Remove old task if it exists
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Create the scheduled task
echo [2/3] Registering Windows Scheduled Task: %TASK_NAME%
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON_EXE%\" \"%SERVICE_SCRIPT%\"" ^
    /sc MINUTE ^
    /mo 30 ^
    /ru "%USERNAME%" ^
    /rl HIGHEST ^
    /f

if errorlevel 1 (
    echo [ERROR] Failed to create scheduled task.
    echo Make sure you are running this script as Administrator.
    pause
    exit /b 1
)

echo [3/3] Running first export now...
"%PYTHON_EXE%" "%SERVICE_SCRIPT%"

echo.
echo =====================================================
echo  Setup complete!
echo  The service will run every 30 minutes automatically.
echo.
echo  Useful commands:
echo    python service.py --status   (show last run info)
echo    python service.py --reset    (clear state / re-export all)
echo    python service.py            (run manually once)
echo.
echo  To uninstall:
echo    schtasks /delete /tn %TASK_NAME% /f
echo =====================================================
pause
