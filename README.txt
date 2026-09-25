Opti MQTT Tester
================

A small Windows tool for testing what the PLC's MQTT Client block needs to
publish to the Opti IoT Hub. It generates the SAS token (password), shows the
exact strings to type into the MQTT Client block, and lets you publish a test
message so you can verify the result on portal.optipeople.dk before the PLC
is wired up.

How to run
----------
1. Make sure Python 3.9 or newer is installed
   (https://www.python.org/downloads/, tick "Add Python to PATH").
2. Double-click run.bat.
   The first run installs the paho-mqtt library automatically.

Using it
--------
1. Device settings
   - IoT Hub hostname: provided by Opti (e.g. IotHubProdOptipeople.azure-devices.net).
   - Device ID: the OMxxxxx ID Opti gave you.
   - Device primary key: the base64 key from the device record.
   Click "Refresh PLC fields / SAS token" — the box below fills in.

2. PLC MQTT Client block
   Copy the six values into the MQTT Client block in the PLC:
     Broker host, Port (8883 with TLS), Client ID, Username,
     Password (SAS token), Publish topic.
   The "SAS token validity (days)" field controls how long the token is
   accepted by Azure IoT Hub. Default is 3650 days (~10 years), high enough
   for a PLC install that won't be touched again. The expiry date is shown
   live under the PLC fields; it turns red when the token has less than a
   week left or has expired.

   Use "Test connection" to verify the broker accepts the credentials
   without publishing anything. Use "Export PLC config…" to save all six
   fields to a text file (handy for handover or printing).

3. Test message
   Pick MachineState, PartCounter or Telemetry, fill in the value,
   then "Send test message". The payload is the JSON the PLC must
   publish on the topic.

4. Result
   Watch the log at the bottom for connect / send confirmation, then
   open portal.optipeople.dk to verify.

Payload format (for the PLC)
----------------------------
MachineState:
  {"time":"<UTC ISO>","inputType":"MachineState",
   "functions":[{"deviceId":"OM01001","name":"state","value":"Runtime","time":"<UTC ISO>"}]}

PartCounter (negative value = reject/scrap):
  {"time":"<UTC ISO>","inputType":"PartCounter",
   "functions":[{"deviceId":"OM01001","name":"counter","value":"10","time":"<UTC ISO>"}]}

Telemetry (type: 1=int, 2=decimal, 3=text). Do NOT send faster than 1 message
per 5 seconds — Opti will throttle or disable the device.
  {"time":"<UTC ISO>","inputType":"Telemetry",
   "functions":[{"deviceId":"OM01001","name":"vibration","value":"1.23","type":"2","time":"<UTC ISO>"}]}
