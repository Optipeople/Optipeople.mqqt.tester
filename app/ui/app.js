"use strict";

const DEFAULTS = {
  hub: "IotHubProdOptipeople.azure-devices.net",
  device: "OM01001",
  key: "",
  expiryDays: 3650,
  useCa: true,
  caFile: "",
  msgType: "MachineState",
  fields: { state: "Runtime", counter: "10", tagName: "vibration", tagType: "2", tagValue: "1.23", partInfo: "OrderX;ItemY;PartZ", expectedSpeed: "100" },
};
const STATES = ["Runtime", "Downtime", "Stopped", "Offline"];
const MSG_TYPES = ["MachineState", "PartCounter", "Telemetry", "AddPartInformation"];
const PLC_ROWS = [
  ["brokerHost", "Broker host"],
  ["port", "Port"],
  ["clientId", "Client ID"],
  ["username", "Username"],
  ["password", "Password (SAS token)"],
  ["topic", "Publish topic"],
];
const SEND_COOLDOWN_MS = 5000;

const $ = (id) => document.getElementById(id);
const invoke = window.__TAURI__ ? window.__TAURI__.core.invoke : previewInvoke;

let plc = null;          // last generated PLC config
let busy = false;
let lastSend = 0;
let msgType = DEFAULTS.msgType;

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
function normalise(s) {
  if (!s || typeof s !== "object") return structuredClone(DEFAULTS);
  // Accept both the new shape and the legacy Python settings.json.
  const f = s.fields || s.msg_fields || {};
  return {
    hub: s.hub ?? DEFAULTS.hub,
    device: s.device ?? DEFAULTS.device,
    key: s.key ?? "",
    expiryDays: Number(s.expiryDays ?? s.expiry_days ?? DEFAULTS.expiryDays) || DEFAULTS.expiryDays,
    useCa: Boolean(s.useCa ?? s.use_ca ?? DEFAULTS.useCa),
    caFile: s.caFile ?? s.ca_file ?? "",
    msgType: s.msgType ?? s.msg_type ?? DEFAULTS.msgType,
    fields: {
      ...DEFAULTS.fields,
      ...pick(f, ["state", "counter", "tagName", "tagType", "tagValue", "partInfo", "expectedSpeed"]),
      ...(f.tag_name != null && { tagName: f.tag_name }),
      ...(f.tag_type != null && { tagType: f.tag_type }),
      ...(f.tag_value != null && { tagValue: f.tag_value }),
    },
  };
}
const pick = (o, keys) => Object.fromEntries(keys.filter((k) => o[k] != null).map((k) => [k, String(o[k])]));

function collect() {
  return {
    hub: $("hub").value.trim(),
    device: $("device").value.trim(),
    key: $("key").value.trim(),
    expiryDays: Number($("expiryDays").value),
    useCa: $("useCa").checked,
    caFile: $("caFile").value.trim(),
    msgType,
    fields: {
      state: $("state").value,
      counter: $("counter").value,
      tagName: $("tagName").value,
      tagType: $("tagType").value,
      tagValue: $("tagValue").value,
      partInfo: $("partInfo").value,
      expectedSpeed: $("expectedSpeed").value,
    },
  };
}

function apply(s) {
  $("hub").value = s.hub;
  $("device").value = s.device;
  $("key").value = s.key;
  $("expiryDays").value = s.expiryDays;
  $("useCa").checked = s.useCa;
  $("caFile").value = s.caFile;
  $("state").value = s.fields.state;
  $("counter").value = s.fields.counter;
  $("tagName").value = s.fields.tagName;
  $("tagType").value = s.fields.tagType;
  $("tagValue").value = s.fields.tagValue;
  $("partInfo").value = s.fields.partInfo;
  $("expectedSpeed").value = s.fields.expectedSpeed;
  setType(MSG_TYPES.includes(s.msgType) ? s.msgType : DEFAULTS.msgType);
}

const saveSoon = debounce(() => invoke("save_settings", { settings: collect() }).catch(() => {}), 400);

function deviceParams() {
  const s = collect();
  return {
    hub: s.hub,
    device: s.device,
    key: s.key,
    expiryDays: Math.max(0, Math.floor(s.expiryDays || 0)),
    caFile: s.useCa ? s.caFile : null,
  };
}

// ---------------------------------------------------------------------------
// PLC fields + token expiry
// ---------------------------------------------------------------------------
function renderPlc() {
  const dl = $("plc");
  dl.replaceChildren();
  for (const [k, label] of PLC_ROWS) {
    const raw = plc ? plc[k] : "";
    const value = k === "port" && plc ? `${raw} (TLS)` : String(raw ?? "");
    const copyValue = k === "port" ? String(raw ?? "") : value;
    const row = el("div", { class: "plc__row" });
    const lab = el("div", { class: "plc__label" }, label);
    const val = el("div", { class: "plc__val" + (k === "password" ? " is-secret" : "") + (value ? "" : " is-empty"), title: value }, value || "Not generated");
    const btn = el("button", { class: "icon-btn", type: "button", title: `Copy ${label}`, "aria-label": `Copy ${label}` });
    btn.innerHTML = ICON_COPY;
    btn.disabled = !value;
    btn.addEventListener("click", () => copy(copyValue, label, btn));
    row.append(lab, val, btn);
    dl.append(row);
  }
  renderExpiry();
}

function renderExpiryHint() {
  const d = Number($("expiryDays").value);
  const y = d / 365;
  $("expiryHint").textContent = d >= 1 ? (y >= 1 ? `≈ ${+y.toFixed(1)} years` : `≈ ${Math.max(1, Math.round(d / 7))} weeks`) : "";
}

function renderExpiry() {
  const box = $("expiry");
  if (!plc) {
    box.dataset.state = "none";
    $("expiryTag").textContent = "No token";
    $("expiryText").textContent = "Fill in the device details to generate a token.";
    return;
  }
  const exp = new Date(plc.expiresAt * 1000);
  const remaining = plc.expiresAt - Math.floor(Date.now() / 1000);
  const when = fmtDateTime(exp);
  if (remaining <= 0) {
    box.dataset.state = "expired";
    $("expiryTag").textContent = "Expired";
    $("expiryText").textContent = `Token expired ${when}. Regenerate it.`;
    return;
  }
  box.dataset.state = remaining < 7 * 86400 ? "soon" : "ok";
  $("expiryTag").textContent = remaining < 7 * 86400 ? "Expires soon" : "Valid";
  $("expiryText").textContent = `Expires ${when} local · ${fmtRemaining(remaining)} left`;
}

let genSeq = 0;
async function regenerate({ quiet = true } = {}) {
  const p = deviceParams();
  const seq = ++genSeq;
  if (!p.hub || !p.device || !p.key) {
    plc = null;
    renderPlc();
    return;
  }
  try {
    const cfg = await invoke("generate_config", { params: p });
    if (seq !== genSeq) return;
    plc = cfg;
    renderPlc();
    if (!quiet) log("ok", "New SAS token generated.");
  } catch (e) {
    if (seq !== genSeq) return;
    plc = null;
    renderPlc();
    if (!quiet) log("error", String(e));
    $("expiryText").textContent = String(e);
  }
}
const regenerateSoon = debounce(regenerate, 300);

// ---------------------------------------------------------------------------
// Message builder
// ---------------------------------------------------------------------------
function setType(t) {
  msgType = t;
  for (const b of $("msgType").querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.type === t));
  for (const p of document.querySelectorAll("[data-panel]")) p.classList.toggle("is-active", p.dataset.panel === t);
  renderPayload();
}

function buildPayload() {
  const s = collect();
  const now = new Date().toISOString();
  const deviceId = s.device;
  let fn;
  if (msgType === "MachineState") {
    fn = { deviceId, name: "state", value: s.fields.state, time: now };
  } else if (msgType === "PartCounter") {
    fn = { deviceId, name: "counter", value: String(s.fields.counter), time: now };
  } else if (msgType === "AddPartInformation") {
    fn = { deviceId, name: s.fields.partInfo, value: "null", expectedSpeed: String(s.fields.expectedSpeed), time: now };
  } else {
    fn = { deviceId, name: s.fields.tagName, value: String(s.fields.tagValue), type: s.fields.tagType, time: now };
  }
  return { time: now, inputType: msgType, functions: [fn] };
}

function renderPayload() {
  const json = JSON.stringify(buildPayload());
  $("payload").innerHTML = highlight(JSON.stringify(JSON.parse(json), null, 2));
  $("payloadSize").textContent = `${new TextEncoder().encode(json).length} bytes`;
  for (const c of $("stateChips").children) c.setAttribute("aria-pressed", String(c.textContent === $("state").value));
}

function highlight(src) {
  return escapeHtml(src).replace(
    /(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(-?\d+(?:\.\d+)?)\b/g,
    (m, str, colon, num) => (str ? `<span class="${colon ? "k" : "s"}">${str}</span>${colon || ""}` : `<span class="n">${num}</span>`)
  );
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
async function run(label, fn) {
  if (busy) return;
  busy = true;
  setBusyUi(true);
  setStatus("busy", label);
  try {
    await fn();
  } finally {
    busy = false;
    setBusyUi(false);
  }
}

function setBusyUi(on) {
  $("testConn").disabled = on;
  $("send").disabled = on;
}

function testConnection() {
  const p = deviceParams();
  run("Connecting", async () => {
    log("busy", `Connecting to ${p.hub}:8883${p.caFile ? " (verifying against CA file)" : ""}`);
    try {
      const msg = await invoke("test_connection", { params: p });
      log("ok", msg);
      setStatus("ok", "Connected");
    } catch (e) {
      log("error", String(e));
      setStatus("error", "Connection failed");
    }
  });
}

function send() {
  const wait = lastSend + SEND_COOLDOWN_MS - Date.now();
  if (wait > 0) {
    log("info", `Wait ${Math.ceil(wait / 1000)} s before sending again (max one message per 5 seconds).`);
    return;
  }
  const p = deviceParams();
  const payload = buildPayload();
  run("Sending", async () => {
    log("busy", `Publishing ${msgType} to ${p.hub}`);
    try {
      const msg = await invoke("send_message", { params: p, payload: JSON.stringify(payload) });
      lastSend = Date.now();
      log("ok", `${msg} Check portal.optipeople.dk for the result.`, JSON.stringify(payload, null, 2));
      setStatus("ok", "Sent");
      regenerate();
    } catch (e) {
      log("error", String(e));
      setStatus("error", "Send failed");
    }
  });
}

async function exportConfig() {
  if (!plc) {
    log("error", "Nothing to export. Fill in the device details first.");
    return;
  }
  const lines = [
    "Opti MQTT · PLC Client block configuration",
    "=".repeat(50),
    `Generated: ${fmtDateTime(new Date())} (local)`,
    `SAS token expires: ${fmtDateTime(new Date(plc.expiresAt * 1000))} (local)`,
    "",
  ];
  for (const [k, label] of PLC_ROWS) lines.push(`${label}:`, `  ${k === "port" ? `${plc.port} (TLS)` : plc[k]}`, "");
  try {
    const path = await invoke("export_config", { fileName: `PLC-MQTT-${plc.clientId}.txt`, contents: lines.join("\r\n") });
    if (path) log("ok", `Exported PLC config to ${path}`);
  } catch (e) {
    log("error", `Export failed: ${e}`);
  }
}

async function copy(text, label, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = el("textarea", {}, text);
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  log("info", `Copied ${label} (${text.length} chars).`);
  if (btn) {
    btn.innerHTML = ICON_CHECK;
    btn.classList.add("is-done");
    setTimeout(() => { btn.innerHTML = ICON_COPY; btn.classList.remove("is-done"); }, 1400);
  }
}

function setStatus(state, text) {
  $("status").dataset.state = state;
  $("statusText").textContent = text;
}

function log(level, message, detail) {
  const ol = $("log");
  ol.querySelector(".empty")?.remove();
  const li = el("li", { class: level });
  li.append(el("time", {}, fmtTime(new Date())), el("span", { class: "lvl" }));
  const msg = el("div", { class: "msg" }, message);
  if (detail) msg.append(el("pre", {}, detail));
  li.append(msg);
  ol.append(li);
  ol.scrollTop = ol.scrollHeight;
}

function clearLog() {
  $("log").replaceChildren(el("li", { class: "empty" }, "Connection tests, sent messages and errors show up here."));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function el(tag, attrs = {}, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
const pad = (n) => String(n).padStart(2, "0");
const fmtDateTime = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
function fmtRemaining(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d >= 1) return `${d.toLocaleString("en-US")} days ${h} h`;
  if (h >= 1) return `${h} h ${m} min`;
  return `${m} min`;
}
const escapeHtml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const ICON_COPY = '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>';
const ICON_CHECK = '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
const ICON_EYE = '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
const ICON_EYE_OFF = '<svg viewBox="0 0 24 24"><path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

/** Used only when the page is opened in a plain browser (design preview). */
async function previewInvoke(cmd, args) {
  if (cmd === "load_settings") return { ...DEFAULTS, key: "c2VjcmV0LWtleS1mb3ItcHJldmlldw==" };
  if (cmd === "default_ca_file") return "C:\\Program Files\\Opti MQTT Tester\\resources\\AzureIoTHub-CA.pem";
  if (cmd === "generate_config") {
    const { hub, device, expiryDays } = args.params;
    const se = Math.floor(Date.now() / 1000) + expiryDays * 86400;
    return {
      brokerHost: hub, port: 8883, clientId: device,
      username: `${hub}/${device}/?api-version=2021-04-12`,
      password: `SharedAccessSignature sr=${encodeURIComponent(hub + "/devices/" + device)}&sig=vKYwZrIosA%2FxM%2FVFa4T9nmQttQsrCmFT3K%2FwoD3Q98w%3D&se=${se}`,
      topic: `devices/${device}/messages/events/$.ct=application%2Fjson&$.ce=utf-8`,
      expiresAt: se,
    };
  }
  if (cmd === "test_connection") return `TLS connection to ${args.params.hub}:8883 accepted.`;
  if (cmd === "send_message") return `Message acknowledged by ${args.params.hub}.`;
  return null;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function init() {
  for (const s of STATES) {
    $("states").append(el("option", { value: s }));
    const chip = el("button", { class: "chip", type: "button" }, s);
    chip.addEventListener("click", () => { $("state").value = s; renderPayload(); saveSoon(); });
    $("stateChips").append(chip);
  }

  const settings = normalise(await invoke("load_settings").catch(() => null));
  if (!settings.caFile) settings.caFile = (await invoke("default_ca_file").catch(() => null)) || "";
  apply(settings);
  syncCa();
  renderExpiryHint();
  clearLog();

  for (const id of ["hub", "device", "key", "expiryDays"]) {
    $(id).addEventListener("input", () => { regenerateSoon(); renderPayload(); renderExpiryHint(); saveSoon(); });
  }
  for (const id of ["state", "counter", "tagName", "tagType", "tagValue", "partInfo", "expectedSpeed"]) {
    $(id).addEventListener("input", () => { renderPayload(); saveSoon(); });
  }
  $("caFile").addEventListener("input", saveSoon);
  $("useCa").addEventListener("change", () => { syncCa(); saveSoon(); });
  $("msgType").addEventListener("click", (e) => {
    const t = e.target.closest("button")?.dataset.type;
    if (t) { setType(t); saveSoon(); }
  });
  $("toggleKey").addEventListener("click", () => {
    const show = $("key").type === "password";
    $("key").type = show ? "text" : "password";
    $("toggleKey").innerHTML = show ? ICON_EYE_OFF : ICON_EYE;
    $("toggleKey").title = show ? "Hide key" : "Show key";
  });
  $("browseCa").addEventListener("click", async () => {
    const path = await invoke("pick_ca_file", { current: $("caFile").value.trim() || null }).catch(() => null);
    if (path) { $("caFile").value = path; $("useCa").checked = true; syncCa(); saveSoon(); }
  });
  $("testConn").addEventListener("click", testConnection);
  $("send").addEventListener("click", send);
  $("regen").addEventListener("click", () => regenerate({ quiet: false }));
  $("export").addEventListener("click", exportConfig);
  $("copyPayload").addEventListener("click", (e) => copy(JSON.stringify(buildPayload()), "payload JSON"));
  $("clearLog").addEventListener("click", clearLog);

  await regenerate();
  setInterval(renderExpiry, 1000);
  setInterval(renderPayload, 1000); // keep the timestamp in the preview current
}

function syncCa() {
  const on = $("useCa").checked;
  $("caFile").disabled = !on;
  $("caGroup").classList.toggle("is-disabled", !on);
}

init();
