"""
Opti MQTT Tester
----------------
A small GUI for PLC technicians to:
  1. Generate the exact values that go into an MQTT Client block on the PLC
     (broker host, port, client ID, username, password/SAS token, topic).
  2. Build a sample JSON payload for MachineState, PartCounter or Telemetry.
  3. Send a test message so the result can be verified in portal.optipeople.dk
     before the PLC is configured.

Requires: Python 3.9+ and paho-mqtt  ( pip install paho-mqtt )
"""

import json
import os
import ssl
import sys
import threading
import tkinter as tk
from base64 import b64decode, b64encode
from datetime import datetime, timezone
from hashlib import sha256
from hmac import HMAC
from time import time
from tkinter import ttk, messagebox, scrolledtext, filedialog
from urllib import parse

import paho.mqtt.client as mqtt


DEFAULT_HUB = "IotHubProdOptipeople.azure-devices.net"
DEFAULT_DEVICE = "OM01001"
DEFAULT_KEY = ""
DEFAULT_EXPIRY_DAYS = 3650  # ~10 years — set high so the PLC token outlives the install

SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0])), "settings.json"
)

DEFAULT_CA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0])), "AzureIoTHub-CA.pem"
)


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------
def current_iso_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def generate_sas_token(uri: str, key: str, expiry_seconds: int) -> tuple[str, int]:
    """Return (token, expiry_epoch_seconds)."""
    ttl = int(time() + expiry_seconds)
    sign_key = f"{parse.quote_plus(uri)}\n{ttl}"
    signature = b64encode(
        HMAC(b64decode(key), sign_key.encode("utf-8"), sha256).digest()
    )
    token = {"sr": uri, "sig": signature, "se": str(ttl)}
    return "SharedAccessSignature " + parse.urlencode(token), ttl


def build_payload(msg_type: str, device_id: str, fields: dict) -> dict:
    now = current_iso_timestamp()
    if msg_type == "MachineState":
        fn = {
            "deviceId": device_id,
            "name": "state",
            "value": fields["state"],
            "time": now,
        }
    elif msg_type == "PartCounter":
        fn = {
            "deviceId": device_id,
            "name": "counter",
            "value": str(fields["counter"]),
            "time": now,
        }
    elif msg_type == "Telemetry":
        fn = {
            "deviceId": device_id,
            "name": fields["tag_name"],
            "value": str(fields["tag_value"]),
            "type": fields["tag_type"],
            "time": now,
        }
    else:
        raise ValueError(f"Unknown message type: {msg_type}")

    return {
        "time": now,
        "inputType": msg_type,
        "functions": [fn],
    }


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Opti MQTT Tester")
        self.geometry("860x820")
        self.minsize(800, 760)

        self.sas_expiry_epoch = 0  # epoch seconds of the currently generated token

        self._build_widgets()
        self._load_into_widgets(load_settings())
        self._on_type_change()
        self._refresh_plc_fields()
        self._tick_expiry()  # start the 1-second expiry countdown timer
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- widgets ----------------------------------------------------------
    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        # ---- Top bar: status pill ----
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(top, text="Opti MQTT Tester", font=("Segoe UI", 11, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="●  Idle")
        self.status_label = tk.Label(
            top, textvariable=self.status_var, fg="white", bg="#888",
            padx=10, pady=2, font=("Segoe UI", 9, "bold"),
        )
        self.status_label.pack(side="right")

        # ---- Device settings ----
        dev = ttk.LabelFrame(self, text="1. Device settings")
        dev.pack(fill="x", **pad)

        ttk.Label(dev, text="IoT Hub hostname:").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.hub_var = tk.StringVar(value=DEFAULT_HUB)
        ttk.Entry(dev, textvariable=self.hub_var, width=55).grid(row=0, column=1, sticky="we", padx=6)

        ttk.Label(dev, text="Device ID:").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.device_var = tk.StringVar(value=DEFAULT_DEVICE)
        ttk.Entry(dev, textvariable=self.device_var, width=55).grid(row=1, column=1, sticky="we", padx=6)

        ttk.Label(dev, text="Device primary key:").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.key_var = tk.StringVar(value=DEFAULT_KEY)
        ttk.Entry(dev, textvariable=self.key_var, width=55, show="*").grid(row=2, column=1, sticky="we", padx=6)

        ttk.Label(dev, text="SAS token validity (days):").grid(row=3, column=0, sticky="e", padx=6, pady=4)
        self.expiry_days_var = tk.StringVar(value=str(DEFAULT_EXPIRY_DAYS))
        exp_frame = ttk.Frame(dev)
        exp_frame.grid(row=3, column=1, sticky="w", padx=6)
        ttk.Entry(exp_frame, textvariable=self.expiry_days_var, width=10).pack(side="left")
        ttk.Label(
            exp_frame,
            text="(default 3650 ≈ 10 years; the PLC keeps using this token until it expires)",
            foreground="#666",
        ).pack(side="left", padx=6)

        ttk.Label(dev, text="CA file (PLC mode):").grid(row=4, column=0, sticky="e", padx=6, pady=4)
        ca_frame = ttk.Frame(dev)
        ca_frame.grid(row=4, column=1, sticky="we", padx=6)
        self.use_ca_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            ca_frame, text="Use CA file", variable=self.use_ca_var,
        ).pack(side="left")
        self.ca_file_var = tk.StringVar(
            value=DEFAULT_CA_FILE if os.path.isfile(DEFAULT_CA_FILE) else ""
        )
        ttk.Entry(ca_frame, textvariable=self.ca_file_var, width=55).pack(
            side="left", padx=6, fill="x", expand=True
        )
        ttk.Button(ca_frame, text="Browse…", command=self._browse_ca).pack(side="left")

        btn_row = ttk.Frame(dev)
        btn_row.grid(row=5, column=1, sticky="w", padx=6, pady=6)
        ttk.Button(btn_row, text="Refresh PLC fields / SAS token", command=self._refresh_plc_fields).pack(side="left")
        ttk.Button(btn_row, text="Test connection", command=self._test_connection_async).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Export PLC config…", command=self._export_config).pack(side="left")
        dev.columnconfigure(1, weight=1)

        # ---- PLC fields ----
        plc = ttk.LabelFrame(self, text="2. Copy these into the PLC MQTT Client block")
        plc.pack(fill="x", **pad)

        self.plc_rows = {}
        for i, label in enumerate(
            ["Broker host", "Port", "Client ID", "Username", "Password (SAS token)", "Publish topic"]
        ):
            ttk.Label(plc, text=label + ":").grid(row=i, column=0, sticky="ne", padx=6, pady=3)
            var = tk.StringVar()
            entry = ttk.Entry(plc, textvariable=var, width=80)
            entry.grid(row=i, column=1, sticky="we", padx=6, pady=3)
            entry.configure(state="readonly")
            ttk.Button(plc, text="Copy", width=6,
                       command=lambda v=var: self._copy(v.get())).grid(row=i, column=2, padx=4)
            self.plc_rows[label] = var
        plc.columnconfigure(1, weight=1)

        # Expiry readout (live)
        self.expiry_var = tk.StringVar(value="Token: (not generated)")
        self.expiry_label = tk.Label(plc, textvariable=self.expiry_var, fg="#666", anchor="w")
        self.expiry_label.grid(row=99, column=0, columnspan=3, sticky="we", padx=6, pady=(2, 6))

        # ---- Message builder ----
        msg = ttk.LabelFrame(self, text="3. Build a test message")
        msg.pack(fill="x", **pad)

        ttk.Label(msg, text="Message type:").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.type_var = tk.StringVar(value="MachineState")
        type_box = ttk.Combobox(
            msg,
            textvariable=self.type_var,
            values=["MachineState", "PartCounter", "Telemetry"],
            state="readonly",
            width=20,
        )
        type_box.grid(row=0, column=1, sticky="w", padx=6)
        type_box.bind("<<ComboboxSelected>>", lambda _e: self._on_type_change())

        self.fields_frame = ttk.Frame(msg)
        self.fields_frame.grid(row=1, column=0, columnspan=4, sticky="we", padx=6, pady=6)
        msg.columnconfigure(1, weight=1)

        btns = ttk.Frame(msg)
        btns.grid(row=2, column=0, columnspan=4, sticky="we", padx=6, pady=6)
        ttk.Button(btns, text="Preview payload", command=self._preview).pack(side="left")
        ttk.Button(btns, text="Send test message", command=self._send_async).pack(side="left", padx=8)
        ttk.Button(btns, text="Copy payload JSON", command=self._copy_payload).pack(side="left")
        self.size_var = tk.StringVar(value="")
        ttk.Label(btns, textvariable=self.size_var, foreground="#666").pack(side="left", padx=12)

        # ---- Output / log ----
        out = ttk.LabelFrame(self, text="4. Result")
        out.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(out, height=12, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- dynamic field rendering ------------------------------------------
    def _on_type_change(self):
        self._render_fields()
        remembered = getattr(self, "_last_msg", {})
        t = self.type_var.get()
        try:
            if t == "MachineState" and "state" in remembered:
                self.state_var.set(remembered["state"])
            elif t == "PartCounter" and "counter" in remembered:
                self.counter_var.set(remembered["counter"])
            elif t == "Telemetry":
                if "tag_name" in remembered:
                    self.tag_name_var.set(remembered["tag_name"])
                if "tag_type" in remembered:
                    self.tag_type_var.set(remembered["tag_type"])
                if "tag_value" in remembered:
                    self.tag_value_var.set(remembered["tag_value"])
        except Exception:
            pass
        self._update_size()

    def _render_fields(self):
        for w in self.fields_frame.winfo_children():
            w.destroy()
        t = self.type_var.get()

        if t == "MachineState":
            ttk.Label(self.fields_frame, text="State:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
            self.state_var = tk.StringVar(value="Runtime")
            ttk.Combobox(
                self.fields_frame,
                textvariable=self.state_var,
                values=["Runtime", "Downtime", "Stopped", "Offline"],
                width=20,
            ).grid(row=0, column=1, sticky="w", padx=4)

        elif t == "PartCounter":
            ttk.Label(self.fields_frame, text="Counter value:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
            self.counter_var = tk.StringVar(value="10")
            ttk.Entry(self.fields_frame, textvariable=self.counter_var, width=12).grid(
                row=0, column=1, sticky="w", padx=4
            )
            ttk.Label(
                self.fields_frame,
                text="Tip: a negative value registers as a reject/scrap in the Opti portal.",
                foreground="#666",
            ).grid(row=1, column=0, columnspan=3, sticky="w", padx=4)

        elif t == "Telemetry":
            ttk.Label(self.fields_frame, text="Tag name:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
            self.tag_name_var = tk.StringVar(value="vibration")
            ttk.Entry(self.fields_frame, textvariable=self.tag_name_var, width=20).grid(
                row=0, column=1, sticky="w", padx=4
            )

            ttk.Label(self.fields_frame, text="Type:").grid(row=0, column=2, sticky="e", padx=4)
            self.tag_type_var = tk.StringVar(value="2")
            ttk.Combobox(
                self.fields_frame,
                textvariable=self.tag_type_var,
                values=["1", "2", "3"],
                width=4,
                state="readonly",
            ).grid(row=0, column=3, sticky="w", padx=4)
            ttk.Label(self.fields_frame, text="(1=int, 2=decimal, 3=text)", foreground="#666").grid(
                row=0, column=4, sticky="w", padx=4
            )

            ttk.Label(self.fields_frame, text="Value:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
            self.tag_value_var = tk.StringVar(value="1.23")
            ttk.Entry(self.fields_frame, textvariable=self.tag_value_var, width=20).grid(
                row=1, column=1, sticky="w", padx=4
            )

        self._update_size()

    # ---- helpers ----------------------------------------------------------
    def _collect_fields(self) -> dict:
        t = self.type_var.get()
        if t == "MachineState":
            return {"state": self.state_var.get()}
        if t == "PartCounter":
            return {"counter": self.counter_var.get()}
        if t == "Telemetry":
            return {
                "tag_name": self.tag_name_var.get(),
                "tag_type": self.tag_type_var.get(),
                "tag_value": self.tag_value_var.get(),
            }
        return {}

    def _current_payload(self) -> dict:
        return build_payload(self.type_var.get(), self.device_var.get(), self._collect_fields())

    def _update_size(self):
        try:
            n = len(json.dumps(self._current_payload()).encode("utf-8"))
            self.size_var.set(f"Payload size: {n} bytes")
        except Exception:
            self.size_var.set("")

    def _get_expiry_seconds(self) -> int:
        try:
            days = int(self.expiry_days_var.get().strip())
            if days < 1:
                raise ValueError
            return days * 86400
        except ValueError:
            raise ValueError("SAS validity must be a whole number of days (>= 1).")

    def _refresh_plc_fields(self):
        hub = self.hub_var.get().strip()
        device = self.device_var.get().strip()
        key = self.key_var.get().strip()
        if not (hub and device and key):
            return
        try:
            expiry_s = self._get_expiry_seconds()
            uri = f"{hub}/devices/{device}"
            sas, ttl = generate_sas_token(uri, key, expiry_s)
        except Exception as e:
            messagebox.showerror("SAS token error", f"Could not generate SAS token:\n{e}")
            return
        self.sas_expiry_epoch = ttl

        username = f"{hub}/{device}/?api-version=2021-04-12"
        topic = f"devices/{device}/messages/events/$.ct=application%2Fjson&$.ce=utf-8"

        self.plc_rows["Broker host"].set(hub)
        self.plc_rows["Port"].set("8883 (TLS)")
        self.plc_rows["Client ID"].set(device)
        self.plc_rows["Username"].set(username)
        self.plc_rows["Password (SAS token)"].set(sas)
        self.plc_rows["Publish topic"].set(topic)
        self._update_expiry_label()

    def _update_expiry_label(self):
        if not self.sas_expiry_epoch:
            self.expiry_var.set("Token: (not generated)")
            self.expiry_label.configure(fg="#666")
            return
        exp_dt = datetime.fromtimestamp(self.sas_expiry_epoch)  # local time
        remaining = self.sas_expiry_epoch - int(time())
        if remaining <= 0:
            self.expiry_var.set(f"Token EXPIRED at {exp_dt:%Y-%m-%d %H:%M} — regenerate.")
            self.expiry_label.configure(fg="#b00020")
        else:
            days = remaining // 86400
            hours = (remaining % 86400) // 3600
            mins = (remaining % 3600) // 60
            if days >= 1:
                rem_text = f"{days} days, {hours} h"
            elif hours >= 1:
                rem_text = f"{hours} h {mins} min"
            else:
                rem_text = f"{mins} min"
            color = "#b00020" if remaining < 7 * 86400 else "#2a7a2a"
            self.expiry_var.set(f"Token expires {exp_dt:%Y-%m-%d %H:%M} local  ·  {rem_text} remaining")
            self.expiry_label.configure(fg=color)

    def _tick_expiry(self):
        self._update_expiry_label()
        self.after(1000, self._tick_expiry)

    def _set_status(self, text: str, color: str):
        self.status_var.set(text)
        self.status_label.configure(bg=color)

    def _copy(self, value: str):
        self.clipboard_clear()
        self.clipboard_append(value)
        self._log(f"Copied to clipboard ({len(value)} chars).")

    def _copy_payload(self):
        try:
            payload = self._current_payload()
        except Exception as e:
            messagebox.showerror("Payload error", str(e))
            return
        self._copy(json.dumps(payload))

    def _preview(self):
        try:
            payload = self._current_payload()
        except Exception as e:
            messagebox.showerror("Payload error", str(e))
            return
        self._update_size()
        self._log("Preview payload:\n" + json.dumps(payload, indent=2))

    def _log(self, text: str):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _browse_ca(self):
        path = filedialog.askopenfilename(
            title="Select CA certificate (PEM)",
            filetypes=[("PEM certificate", "*.pem *.crt *.cer"), ("All files", "*.*")],
        )
        if path:
            self.ca_file_var.set(path)
            self.use_ca_var.set(True)

    # ---- connection helpers ----------------------------------------------
    def _make_client(self) -> tuple[mqtt.Client, str, str]:
        hub = self.hub_var.get().strip()
        device = self.device_var.get().strip()
        key = self.key_var.get().strip()
        if not (hub and device and key):
            raise RuntimeError("Fill in hub, device ID and key first.")
        expiry_s = self._get_expiry_seconds()
        uri = f"{hub}/devices/{device}"
        sas, ttl = generate_sas_token(uri, key, expiry_s)
        self.sas_expiry_epoch = ttl
        self.after(0, self._update_expiry_label)
        username = f"{hub}/{device}/?api-version=2021-04-12"
        topic = f"devices/{device}/messages/events/$.ct=application%2Fjson&$.ce=utf-8"
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=device,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(username=username, password=sas)
        ca_file = self.ca_file_var.get().strip() if self.use_ca_var.get() else ""
        if ca_file:
            if not os.path.isfile(ca_file):
                raise RuntimeError(f"CA file not found: {ca_file}")
            client.tls_set(
                ca_certs=ca_file,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLSv1_2,
            )
            self._log(f"PLC mode — verifying server cert against {os.path.basename(ca_file)} only.")
        else:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
        client.connect(hub, 8883, 60)
        return client, topic, hub

    def _test_connection_async(self):
        threading.Thread(target=self._test_connection, daemon=True).start()

    def _test_connection(self):
        self.after(0, lambda: self._set_status("●  Connecting…", "#c08000"))
        self._log("Testing connection…")
        try:
            client, _topic, hub = self._make_client()
            client.disconnect()
            self._log(f"OK — TLS connection to {hub}:8883 succeeded.\n")
            self.after(0, lambda: self._set_status("●  Connected", "#2a7a2a"))
        except Exception as e:
            self._log(f"ERROR: {e}\n")
            self.after(0, lambda: self._set_status("●  Error", "#b00020"))
            messagebox.showerror("Connection failed", str(e))

    # ---- publish ----------------------------------------------------------
    def _send_async(self):
        threading.Thread(target=self._send, daemon=True).start()

    def _send(self):
        try:
            payload = self._current_payload()
        except Exception as e:
            messagebox.showerror("Payload error", str(e))
            return

        self.after(0, lambda: self._set_status("●  Connecting…", "#c08000"))
        try:
            client, topic, hub = self._make_client()
            self._log(f"Connected to {hub}:8883.")
            self.after(0, lambda: self._set_status("●  Sending…", "#c08000"))

            info = client.publish(topic, json.dumps(payload), qos=1)
            while not info.is_published():
                client.loop(timeout=1.0)

            self._log("Sent:\n" + json.dumps(payload, indent=2))
            self._log("OK — check portal.optipeople.dk for the result.\n")
            client.disconnect()
            self.after(0, lambda: self._set_status("●  Sent", "#2a7a2a"))
        except Exception as e:
            self._log(f"ERROR: {e}\n")
            self.after(0, lambda: self._set_status("●  Error", "#b00020"))
            messagebox.showerror("Send failed", str(e))

    # ---- export -----------------------------------------------------------
    def _export_config(self):
        from tkinter import filedialog
        if not self.plc_rows["Password (SAS token)"].get():
            messagebox.showwarning("Nothing to export", "Generate the SAS token first.")
            return
        device = self.device_var.get().strip() or "device"
        path = filedialog.asksaveasfilename(
            title="Save PLC config",
            defaultextension=".txt",
            initialfile=f"PLC-MQTT-{device}.txt",
            filetypes=[("Text file", "*.txt")],
        )
        if not path:
            return
        exp_dt = datetime.fromtimestamp(self.sas_expiry_epoch) if self.sas_expiry_epoch else None
        lines = [
            "Opti MQTT — PLC Client block configuration",
            "=" * 50,
            f"Generated: {datetime.now():%Y-%m-%d %H:%M} (local)",
            f"SAS token expires: {exp_dt:%Y-%m-%d %H:%M} (local)" if exp_dt else "",
            "",
        ]
        for k, v in self.plc_rows.items():
            lines.append(f"{k}:")
            lines.append(f"  {v.get()}")
            lines.append("")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._log(f"Exported PLC config to {path}")
        except OSError as e:
            messagebox.showerror("Export failed", str(e))

    # ---- settings persistence --------------------------------------------
    def _load_into_widgets(self, s: dict):
        if not s:
            return
        self.hub_var.set(s.get("hub", DEFAULT_HUB))
        self.device_var.set(s.get("device", DEFAULT_DEVICE))
        self.key_var.set(s.get("key", DEFAULT_KEY))
        self.expiry_days_var.set(str(s.get("expiry_days", DEFAULT_EXPIRY_DAYS)))
        if "ca_file" in s:
            self.ca_file_var.set(s["ca_file"])
        self.use_ca_var.set(bool(s.get("use_ca", False)))
        self.type_var.set(s.get("msg_type", "MachineState"))
        # remembered message field values are applied in _on_type_change
        self._last_msg = s.get("msg_fields", {})

    def _on_close(self):
        s = {
            "hub": self.hub_var.get(),
            "device": self.device_var.get(),
            "key": self.key_var.get(),
            "expiry_days": self.expiry_days_var.get(),
            "ca_file": self.ca_file_var.get(),
            "use_ca": self.use_ca_var.get(),
            "msg_type": self.type_var.get(),
            "msg_fields": self._collect_fields(),
        }
        save_settings(s)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
