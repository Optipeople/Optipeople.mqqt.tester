# Opti MQTT Tester 2

Windows desktop tool for PLC technicians: generates the values for the PLC's
MQTT Client block (broker, client ID, username, SAS token, topic), builds a
sample MachineState / PartCounter / Telemetry payload, and sends a test message
to the OptiPeople Azure IoT Hub so the result can be checked on
portal.optipeople.dk.

Replaces the Python/Tkinter tool in the parent folder.

## Stack

- **Rust + Tauri 2**: native exe using the WebView2 runtime that ships with Windows 10/11
- **rumqttc** (MQTT 3.1.1 over TLS 8883, rustls), **hmac/sha2** for the SAS token
- **UI**: plain HTML/CSS/JS in `ui/`, no bundler. OptiPeople product design language
  (Hanken Grotesk, midnight green, 9px brand lines)
- **Installer**: NSIS `setup.exe` (per-user, no admin) and an MSI

## Develop

Needs Rust (MSVC toolchain) and Node.js.

```
npm install
npm run dev        # run with live reload of ui/
npm run build      # release exe + installers
cd src-tauri && cargo test
```

Output: `src-tauri/target/release/bundle/nsis/Opti MQTT Tester_<ver>_x64-setup.exe`
and `.../bundle/msi/*.msi`.

Opening `ui/index.html` directly in a browser shows the UI with mocked data,
which is handy for design work.

## Notes

- Settings are stored in `%APPDATA%\dk.optipeople.mqtt-tester\settings.json`. On first
  run, a legacy `settings.json` next to the exe is imported.
- The device key is stored in plain text in that file, as in the old tool.
- `AzureIoTHub-CA.pem` is bundled, and "Verify against CA file (PLC mode)" is on by default.
  Unticked, the server certificate is verified against the Windows certificate store.
- Sends are limited to one per 5 seconds, matching the platform's telemetry limit.
- App icon source: `src-tauri/app-icon.svg` (`npm run icon` regenerates the icon set).
