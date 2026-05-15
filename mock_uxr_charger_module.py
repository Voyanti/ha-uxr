"""Drop-in mock of uxr_charger_module for tests/dev without CAN hardware.

The mock exposes the same `UXRChargerModule` class. Getters return plausible
canned values; setters are no-ops that record calls on `self.calls`.

Serial number is derived from the CAN address as `address + 1`, matching a
config where SERIAL_NR "1","2","3" map to CANBUS_ID 0,1,2.
"""

import logging


class UXRChargerModule:
    def __init__(self, channel, bitrate=125000):
        self.channel = channel
        self.bitrate = bitrate
        self.calls: list[tuple] = []
        logging.info(f"[mock UXR] init channel={channel} bitrate={bitrate}")

    def _record(self, name, *args):
        self.calls.append((name, args))

    # --- readings ----------------------------------------------------------
    def get_module_voltage(self, address, group):
        return 780.12

    def get_module_current(self, address, group):
        return 5.43

    def get_module_current_limit(self, address, group):
        return 0.5  # fraction; app multiplies by rated_current

    def get_temperature_dc_board(self, address, group):
        return 42.0

    def get_input_phase_voltage(self, address, group):
        return 230.1

    def get_pfc0_voltage(self, address, group):
        return 400.0

    def get_pfc1_voltage(self, address, group):
        return 400.5

    def get_panel_board_temperature(self, address, group):
        return 35.2

    def get_voltage_phase_a(self, address, group):
        return 230.0

    def get_voltage_phase_b(self, address, group):
        return 230.5

    def get_voltage_phase_c(self, address, group):
        return 229.8

    def get_temperature_pfc_board(self, address, group):
        return 38.7

    def get_rated_output_power(self, address, group):
        return 30000.0

    def get_rated_output_current(self, address, group):
        return 40.0

    def get_input_power(self, address, group):
        return 1500  # int register

    def get_current_altitude_value(self, address, group):
        return 100

    def get_input_working_mode(self, address, group):
        return 1

    def get_serial_number(self, address, group):
        return address + 1

    def get_alarm_status(self, address, group):
        return {}

    # --- setters (no-op, recorded) ----------------------------------------
    def set_altitude(self, altitude, address, group):
        self._record("set_altitude", altitude, address, group)

    def set_output_current(self, current, address, group):
        self._record("set_output_current", current, address, group)

    def set_group_id(self, group_id, address):
        self._record("set_group_id", group_id, address)

    def set_output_voltage(self, voltage, address, group):
        self._record("set_output_voltage", voltage, address, group)

    def set_current_limit_fraction(self, fraction, address, group):
        self._record("set_current_limit_fraction", fraction, address, group)

    def set_max_voltage_setpoint(self, voltage, address, group):
        self._record("set_max_voltage_setpoint", voltage, address, group)

    def power_on_off(self, state, address, group):
        self._record("power_on_off", state, address, group)

    def set_reset_over_voltage(self, reset, address, group):
        self._record("set_reset_over_voltage", reset, address, group)

    def set_over_voltage_protection(self, enable, address, group):
        self._record("set_over_voltage_protection", enable, address, group)

    def set_short_circuit_reset(self, reset, address, group):
        self._record("set_short_circuit_reset", reset, address, group)

    def set_input_mode(self, mode, address, group):
        self._record("set_input_mode", mode, address, group)

    def flush_buffer(self):
        pass

    def __del__(self):
        pass
