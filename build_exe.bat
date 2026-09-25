@echo off
REM Build OptiMQTTTester.exe (single-file, no console window).
REM Requires: Python 3.9+, paho-mqtt, pyinstaller (auto-installed below).

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found on PATH.
    pause
    exit /b 1
)

python -m pip install --user --quiet pyinstaller paho-mqtt
if errorlevel 1 (
    echo Failed to install build dependencies.
    pause
    exit /b 1
)

python -m PyInstaller --onefile --noconsole --name "OptiMQTTTester" --clean "%~dp0opti_mqtt_tester.py"
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Done. Executable: %~dp0dist\OptiMQTTTester.exe
pause
