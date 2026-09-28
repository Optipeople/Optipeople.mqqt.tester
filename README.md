# Opti MQTT Tester

A Windows desktop tool for connecting a PLC's MQTT Client block to the
OptiPeople IoT Hub. It generates the exact values the MQTT Client block needs
(broker, client ID, username, SAS token and topic). It also shows the JSON
payload the PLC must publish and sends a test message, so you can check the
result on [portal.optipeople.dk](https://portal.optipeople.dk) before the PLC
is wired up.

## Download

Get the latest installer from the
[Releases page](https://github.com/Optipeople/Optipeople.mqqt.tester/releases/latest):

- `OptiMQTTTester-<version>-setup.exe`: the recommended installer. It installs
  for the current user and needs no admin rights.
- `OptiMQTTTester-<version>.msi`: for central deployment by IT.

Requires Windows 10 or 11.

The installer is not code-signed yet, so Windows SmartScreen may say "Windows
protected your PC". Click **More info → Run anyway**.

## What you need from OptiPeople

- **IoT Hub hostname**, e.g. `IotHubProdOptipeople.azure-devices.net`
- **Device ID**, the `OMxxxxx` ID of the device
- **Device primary key**, the base64 key from the device record

## Using the tool

1. **Device**: enter the hostname, device ID and primary key. *Token validity
   (days)* sets how long the SAS token is accepted. The default of 3650 days
   (about 10 years) suits a PLC that won't be touched again. Use **Test
   connection** to check the credentials without publishing anything.
2. **PLC MQTT Client block**: copy the generated values into the PLC. The
   token expiry is shown under the fields. **Regenerate token** issues a fresh
   one, and **Export config** saves all values to a text file for handover.
3. **Test message**: pick *Machine state*, *Part counter* or *Telemetry*, fill
   in the value and click **Send test message**. The *Payload* panel shows
   the JSON the PLC must publish.
4. **Activity**: check the connect/send result, then verify the data in the
   portal.

## PLC MQTT Client settings

| Field       | Value                                                                  |
|-------------|------------------------------------------------------------------------|
| Broker host | `<hub>` (e.g. `IotHubProdOptipeople.azure-devices.net`)                |
| Port        | `8883` (MQTT 3.1.1 over TLS)                                           |
| Client ID   | `<deviceId>`                                                           |
| Username    | `<hub>/<deviceId>/?api-version=2021-04-12`                             |
| Password    | SAS token: `SharedAccessSignature sr=<hub>%2Fdevices%2F<deviceId>&sig=…&se=<expiry>` |
| Topic       | `devices/<deviceId>/messages/events/$.ct=application%2Fjson&$.ce=utf-8` |

The server certificate chains to the Azure IoT Hub root CAs. The PEM bundle is
included in this repo as [`AzureIoTHub-CA.pem`](AzureIoTHub-CA.pem), with DER
copies in [`der-cer/`](der-cer) for PLCs that need them. The tool ships with
the same bundle. With *Verify against CA file (PLC mode)* turned on, it checks
the server exactly as a PLC would.

## Payload format

All timestamps are UTC ISO 8601. All values are sent as strings.

**MachineState**: `value` is one of `Runtime`, `Downtime`, `Stopped`, `Offline`.

```json
{"time":"2026-09-28T10:00:00.000Z","inputType":"MachineState",
 "functions":[{"deviceId":"OM01001","name":"state","value":"Runtime","time":"2026-09-28T10:00:00.000Z"}]}
```

**PartCounter**: a negative value registers as reject/scrap.

```json
{"time":"2026-09-28T10:00:00.000Z","inputType":"PartCounter",
 "functions":[{"deviceId":"OM01001","name":"counter","value":"10","time":"2026-09-28T10:00:00.000Z"}]}
```

**Telemetry**: `type` is `1` = integer, `2` = decimal, `3` = text.

```json
{"time":"2026-09-28T10:00:00.000Z","inputType":"Telemetry",
 "functions":[{"deviceId":"OM01001","name":"vibration","value":"1.23","type":"2","time":"2026-09-28T10:00:00.000Z"}]}
```

> **Rate limit:** do not publish more than one message per 5 seconds per
> device. Faster senders are throttled or disabled. The tool enforces this
> limit on its own sends.

## Privacy

Settings, including the device key, are stored in plain text in
`%APPDATA%\dk.optipeople.mqtt-tester\settings.json` on your PC. Treat that file
like a password.

## Building from source

The app lives in [`app/`](app) (Rust + Tauri 2, plain HTML/CSS/JS UI). You need
Rust (MSVC toolchain) and Node.js.

```
cd app
npm install
npm run dev      # run with live reload
npm run build    # release exe + installers in src-tauri/target/release/bundle/
cd src-tauri && cargo test
```

See [`app/README.md`](app/README.md) for developer notes.

### Legacy Python version

`opti_mqtt_tester.py` is the original Tkinter tool that the app replaces. Run
it with `run.bat` (Python 3.9+), or build a standalone exe with
`build_exe.bat`. It is no longer maintained.
