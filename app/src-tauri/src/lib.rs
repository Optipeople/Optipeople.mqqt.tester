//! Opti MQTT Tester backend.
//!
//! Generates the values a PLC MQTT Client block needs to publish to the
//! OptiPeople Azure IoT Hub (host, client ID, username, SAS token, topic), and
//! connects / publishes test messages over MQTT 3.1.1 + TLS on port 8883.

use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine;
use hmac::{Hmac, Mac};
use rumqttc::{
    AsyncClient, ConnectReturnCode, Event, EventLoop, MqttOptions, Packet, QoS, TlsConfiguration,
    Transport,
};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

const PORT: u16 = 8883;
const API_VERSION: &str = "2021-04-12";
const NETWORK_TIMEOUT: Duration = Duration::from_secs(20);
const SETTINGS_FILE: &str = "settings.json";
const CA_RESOURCE: &str = "resources/AzureIoTHub-CA.pem";

// ---------------------------------------------------------------------------
// SAS token + PLC field generation
// ---------------------------------------------------------------------------

fn quote_plus(s: &str) -> String {
    form_urlencoded::byte_serialize(s.as_bytes()).collect()
}

/// Returns (token, expiry epoch seconds). Same format as the Azure IoT SDKs.
fn generate_sas_token(uri: &str, key_b64: &str, ttl_secs: u64) -> Result<(String, u64), String> {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    let expiry = now + ttl_secs;
    Ok((sign_sas(uri, key_b64, expiry)?, expiry))
}

fn sign_sas(uri: &str, key_b64: &str, expiry: u64) -> Result<String, String> {
    let key = B64
        .decode(key_b64.trim())
        .map_err(|_| "The device primary key is not valid base64.".to_string())?;
    let to_sign = format!("{}\n{}", quote_plus(uri), expiry);

    let mut mac = Hmac::<Sha256>::new_from_slice(&key).map_err(|e| e.to_string())?;
    mac.update(to_sign.as_bytes());
    let sig = B64.encode(mac.finalize().into_bytes());

    let query = form_urlencoded::Serializer::new(String::new())
        .append_pair("sr", uri)
        .append_pair("sig", &sig)
        .append_pair("se", &expiry.to_string())
        .finish();
    Ok(format!("SharedAccessSignature {query}"))
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DeviceParams {
    hub: String,
    device: String,
    key: String,
    expiry_days: u64,
    ca_file: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PlcConfig {
    broker_host: String,
    port: u16,
    client_id: String,
    username: String,
    password: String,
    topic: String,
    expires_at: u64,
}

fn plc_config(p: &DeviceParams) -> Result<PlcConfig, String> {
    let hub = p.hub.trim();
    let device = p.device.trim();
    if hub.is_empty() || device.is_empty() || p.key.trim().is_empty() {
        return Err("Fill in IoT Hub hostname, device ID and primary key first.".into());
    }
    if p.expiry_days < 1 {
        return Err("SAS token validity must be at least 1 day.".into());
    }
    let (password, expires_at) =
        generate_sas_token(&format!("{hub}/devices/{device}"), &p.key, p.expiry_days * 86_400)?;
    Ok(PlcConfig {
        broker_host: hub.into(),
        port: PORT,
        client_id: device.into(),
        username: format!("{hub}/{device}/?api-version={API_VERSION}"),
        password,
        topic: format!("devices/{device}/messages/events/$.ct=application%2Fjson&$.ce=utf-8"),
        expires_at,
    })
}

#[tauri::command]
fn generate_config(params: DeviceParams) -> Result<PlcConfig, String> {
    plc_config(&params)
}

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------

async fn connect(p: &DeviceParams) -> Result<(AsyncClient, EventLoop, PlcConfig), String> {
    let cfg = plc_config(p)?;

    let mut opts = MqttOptions::new(&cfg.client_id, &cfg.broker_host, PORT);
    opts.set_credentials(&cfg.username, &cfg.password);
    opts.set_keep_alive(Duration::from_secs(60));

    let tls = match p.ca_file.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        Some(path) => {
            let ca = std::fs::read(path).map_err(|e| format!("Could not read CA file {path}: {e}"))?;
            TlsConfiguration::Simple { ca, alpn: None, client_auth: None }
        }
        None => TlsConfiguration::default(), // Windows certificate store
    };
    opts.set_transport(Transport::tls_with_config(tls));

    let (client, mut eventloop) = AsyncClient::new(opts, 10);

    let wait_connack = async {
        loop {
            match eventloop.poll().await {
                Ok(Event::Incoming(Packet::ConnAck(ack))) => {
                    return match ack.code {
                        ConnectReturnCode::Success => Ok(()),
                        code => Err(format!("Broker refused the connection: {code:?}")),
                    };
                }
                Ok(_) => continue,
                Err(e) => return Err(describe_error(e)),
            }
        }
    };
    tokio::time::timeout(NETWORK_TIMEOUT, wait_connack)
        .await
        .map_err(|_| format!("Timed out connecting to {}:{PORT}.", cfg.broker_host))??;

    Ok((client, eventloop, cfg))
}

fn describe_error(e: rumqttc::ConnectionError) -> String {
    use rumqttc::ConnectionError::*;
    match e {
        ConnectionRefused(ConnectReturnCode::BadUserNamePassword)
        | ConnectionRefused(ConnectReturnCode::NotAuthorized) => {
            "Broker refused the credentials. Check device ID and primary key.".into()
        }
        other => other.to_string(),
    }
}

async fn disconnect(client: AsyncClient, mut eventloop: EventLoop) {
    let _ = client.disconnect().await;
    let _ = tokio::time::timeout(Duration::from_secs(2), async {
        while eventloop.poll().await.is_ok() {}
    })
    .await;
}

#[tauri::command]
async fn test_connection(params: DeviceParams) -> Result<String, String> {
    let (client, eventloop, cfg) = connect(&params).await?;
    disconnect(client, eventloop).await;
    Ok(format!("TLS connection to {}:{PORT} accepted.", cfg.broker_host))
}

#[tauri::command]
async fn send_message(params: DeviceParams, payload: String) -> Result<String, String> {
    let (client, mut eventloop, cfg) = connect(&params).await?;
    client
        .publish(&cfg.topic, QoS::AtLeastOnce, false, payload.into_bytes())
        .await
        .map_err(|e| e.to_string())?;

    let wait_puback = async {
        loop {
            match eventloop.poll().await {
                Ok(Event::Incoming(Packet::PubAck(_))) => return Ok(()),
                Ok(_) => continue,
                Err(e) => return Err(describe_error(e)),
            }
        }
    };
    tokio::time::timeout(NETWORK_TIMEOUT, wait_puback)
        .await
        .map_err(|_| "Timed out waiting for the broker to acknowledge the message.".to_string())??;

    disconnect(client, eventloop).await;
    Ok(format!("Message acknowledged by {}.", cfg.broker_host))
}

// ---------------------------------------------------------------------------
// Settings, files and dialogs
// ---------------------------------------------------------------------------

fn settings_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    Ok(dir.join(SETTINGS_FILE))
}

/// Settings live in %APPDATA%. On first run, pick up a settings.json left next
/// to the exe by the old Python tool.
#[tauri::command]
fn load_settings(app: AppHandle) -> serde_json::Value {
    let mut candidates = vec![];
    if let Ok(p) = settings_path(&app) {
        candidates.push(p);
    }
    if let Some(dir) = std::env::current_exe().ok().and_then(|p| p.parent().map(PathBuf::from)) {
        candidates.push(dir.join(SETTINGS_FILE));
    }
    candidates
        .iter()
        .find_map(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(serde_json::Value::Null)
}

#[tauri::command]
fn save_settings(app: AppHandle, settings: serde_json::Value) -> Result<(), String> {
    let path = settings_path(&app)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&settings).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

#[tauri::command]
fn default_ca_file(app: AppHandle) -> Option<String> {
    let path = app.path().resource_dir().ok()?.join(CA_RESOURCE);
    path.is_file().then(|| path.to_string_lossy().into_owned())
}

/// Opens in the folder of the currently selected CA file, if it still exists.
#[tauri::command]
async fn pick_ca_file(app: AppHandle, current: Option<String>) -> Option<String> {
    let mut dialog = app
        .dialog()
        .file()
        .set_title("Select CA certificate (PEM)")
        .add_filter("PEM certificate", &["pem", "crt", "cer"])
        .add_filter("All files", &["*"]);

    let current = current.map(PathBuf::from).filter(|p| !p.as_os_str().is_empty());
    if let Some(path) = current {
        let dir = if path.is_dir() { Some(path.as_path()) } else { path.parent() };
        if let Some(dir) = dir.filter(|d| d.is_dir()) {
            dialog = dialog.set_directory(dir);
        }
        if path.is_file() {
            if let Some(name) = path.file_name() {
                dialog = dialog.set_file_name(name.to_string_lossy());
            }
        }
    }

    dialog.blocking_pick_file().map(|p| p.to_string())
}

#[tauri::command]
async fn export_config(app: AppHandle, file_name: String, contents: String) -> Result<Option<String>, String> {
    let Some(path) = app
        .dialog()
        .file()
        .set_title("Save PLC config")
        .set_file_name(&file_name)
        .add_filter("Text file", &["txt"])
        .blocking_save_file()
    else {
        return Ok(None);
    };
    let path = path.into_path().map_err(|e| e.to_string())?;
    std::fs::write(&path, contents).map_err(|e| e.to_string())?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            generate_config,
            test_connection,
            send_message,
            load_settings,
            save_settings,
            default_ca_file,
            pick_ca_file,
            export_config,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Opti MQTT Tester");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sas_token_shape() {
        let (tok, exp) = generate_sas_token("hub.azure-devices.net/devices/OM01001", "c2VjcmV0", 60).unwrap();
        assert!(tok.starts_with("SharedAccessSignature sr=hub.azure-devices.net%2Fdevices%2FOM01001&sig="));
        assert!(tok.ends_with(&format!("&se={exp}")));
    }

    /// Reference value produced by the original Python tool.
    #[test]
    fn matches_python_tool() {
        let tok = sign_sas(
            "IotHubProdOptipeople.azure-devices.net/devices/OM01001",
            "q6RWrm0crVX47qm6/8jeDEnpn2VfdTMccA6zvw53NjI=",
            2_000_000_000,
        )
        .unwrap();
        assert_eq!(
            tok,
            "SharedAccessSignature sr=IotHubProdOptipeople.azure-devices.net%2Fdevices%2FOM01001\
             &sig=vKYwZrIosA%2FxM%2FVFa4T9nmQttQsrCmFT3K%2FwoD3Q98w%3D&se=2000000000"
        );
    }

    #[test]
    fn rejects_bad_key() {
        assert!(generate_sas_token("x", "not base64!!", 60).is_err());
    }
}
