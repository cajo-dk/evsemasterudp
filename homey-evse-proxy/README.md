# Homey EVSE Proxy

Small Homey Pro app that sits on the charger LAN and talks locally to an EVSE Master charger over UDP.

It publishes charger updates to MQTT so Home Assistant can subscribe remotely.

The small Homey app API still exists for the settings page and diagnostics during development, but MQTT is now the intended integration path for Home Assistant.

## MQTT topics

Under the configured base topic, the app publishes retained JSON payloads to:

- `/health`
- `/diagnostics`
- `/metadata`
- `/status`
- `/command/charging` for start and stop control

Example with base topic `homey/evse_master_proxy`:

- `homey/evse_master_proxy/health`
- `homey/evse_master_proxy/diagnostics`
- `homey/evse_master_proxy/metadata`
- `homey/evse_master_proxy/status`
- `homey/evse_master_proxy/command/charging`

The `command/charging` topic accepts `ON`, `OFF`, `true`, `false`, `start`, and `stop`.

## Why this exists

Some EVSE Master chargers answer login attempts by broadcasting on the local LAN instead of unicasting back to the original client. That works on a flat LAN, but it breaks across routed VPNs.

Running this app on a Homey Pro that shares the charger LAN keeps the UDP session local and republishes the results over MQTT.

## What it currently forwards

- EVSE discovery metadata
- EVSE connection and health state
- Pushed real-time status updates received from the charger
- A derived `evse_state` summary for quick reads such as `unplugged_idle`
- MQTT charging control through a Home Assistant switch

## App settings

Use the Homey app settings page to enter:

- charger serial
- charger password
- optional charger host
- charger UDP port
- auto-connect preference
- MQTT broker URL
- MQTT username
- MQTT password
- MQTT base topic
- MQTT client ID

If `host` is empty, the app waits for EVSE discovery broadcasts on the local LAN.
If `host` is set, it uses direct mode.

## Development

Official Homey docs used for this scaffold:

- App manifest and SDK v3
- App settings custom view
- App Web API

Sources:

- https://apps.developer.homey.app/the-basics/app
- https://apps.developer.homey.app/advanced/custom-views/app-settings
- https://apps.developer.homey.app/advanced/web-api

## Notes

- This app is a charger-side proxy component. Home Assistant should subscribe to the MQTT topics instead of talking UDP directly to the charger.
- The UDP protocol support included here focuses on login, heartbeat, metadata, and status monitoring.
- MQTT discovery now exposes:
  - an `EVSE State` sensor with values like `unplugged_idle`, `plugged_idle`, `plugged_charging`, and `error`
  - an `EVSE Charging` switch that publishes `ON` and `OFF` to the charging command topic
