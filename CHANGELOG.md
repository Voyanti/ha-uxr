# Changelog

## 1.0.8 (unreleased)

- Reworked the offline-recovery path: when a charger stops responding, the CAN bus connection is now reset (`reconnect()`) and the startup sequence re-runs for **all** configured chargers, not just the failed one. Turn-on commands are sent to all chargers up front before serials and defaults are read, since per-charger turn-on proved unreliable due to timing.
