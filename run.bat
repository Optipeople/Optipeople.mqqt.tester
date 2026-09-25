@echo off
REM Opti MQTT Tester launcher
REM Installs paho-mqtt the first time, then starts the GUI.

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    echo Install Python 3.9+ from https://www.python.org/downloads/ and tick "Add Python to PATH".
    pause
    exit /b 1
)

python -c "import paho.mqtt.client" 2>nul
if errorlevel 1 (
    echo Installing paho-mqtt ...
    python -m pip install --user paho-mqtt
)

python "%~dp0opti_mqtt_tester.py"
pause
