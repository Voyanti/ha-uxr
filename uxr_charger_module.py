import struct
import can
import time

class UXRChargerModule:
    source_address = 0xF0
    protno = 0x060

    def __init__(self, channel, bitrate=125000):
        self.channel = channel
        self.bitrate = bitrate
        self.bus = can.interface.Bus(channel=channel, interface='slcan', bitrate=bitrate)

    def reconnect(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass
        self.bus = can.interface.Bus(channel=self.channel, interface='slcan', bitrate=self.bitrate)

    def flush_buffer(self):
        """Flush any existing messages in the CAN buffer."""
        while True:
            message = self.bus.recv(timeout=0.1)
            if message is None:
                break

    def generate_can_arbitration_id(self, protno=0x060, ptp=1, dstaddr=0x00, srcaddr=0x00, group=0):
        if not (0 <= protno <= 0x1FF):
            raise ValueError("PROTNO must be a 9-bit value in the range 0-0x1FF.")
        if not (0 <= ptp <= 1):
            raise ValueError("PTP must be either 0 or 1.")
        if not (0 <= dstaddr <= 0xFF):
            raise ValueError("DSTADDR must be an 8-bit value in the range 0-0xFF.")
        if not (0 <= srcaddr <= 0xFF):
            raise ValueError("SRCADDR must be an 8-bit value in the range 0-0xFF.")
        if not (0 <= group <= 7):
            raise ValueError("GROUP must be a 3-bit value in the range 0-7.")
        
        arbitration_id = (protno << 20) | (ptp << 19) | (dstaddr << 11) | (srcaddr << 3) | group
        return arbitration_id

    def send_frame(self, arbitration_id, data):
        self.flush_buffer()
        frame = can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=True)
        self.bus.send(frame)

    def receive_frame(self):
        message = self.bus.recv(timeout=2)
        if message:
            return message.arbitration_id, message.data
        return None, None

    def float_to_bytes(self, value):
        return struct.pack('>f', value)

    def bytes_to_float(self, value_bytes):
        return struct.unpack('>f', value_bytes)[0]

    def read_value(self, register, address, group, is_float=True):
        data = [0x10, 0x00, 0x00, register, 0x00, 0x00, 0x00, 0x00]
        arbitration_id = self.generate_can_arbitration_id(self.protno, 1, address, self.source_address, group)
        self.send_frame(arbitration_id, data)
        _, response_data = self.receive_frame()
        if response_data and response_data[0] == 0x41 and is_float:
            return round(self.bytes_to_float(response_data[4:8]), 2)
        elif response_data and response_data[0] == 0x42 and not is_float:
            return struct.unpack('>I', response_data[4:8])[0]
        return None

    def set_value(self, register, value, address, group, is_float=True):
        """
        Sets a value on the device for the given register.

        Parameters:
            register (int): The register address to set.
            value: The value to set (either float or integer).
            address (int): The destination address for the CAN message.
            group (int): The group ID for the CAN message.
            is_float (bool): If True, the value is treated as a float. If False, as an integer.
        """
        if is_float:
            # Convert the float value to 4 bytes using IEEE 754 format
            value_bytes = list(struct.pack('>f', value))
        else:
            # Convert the integer value to 4 bytes
            value_bytes = list(value.to_bytes(4, byteorder='big'))

        # Construct the data payload
        data = [0x03, 0x00, 0x00, register] + value_bytes
        # print(" ".join(f"0x{byte:02X}" for byte in data))
        # Generate the CAN arbitration ID
        arbitration_id = self.generate_can_arbitration_id(self.protno, 1, address, self.source_address, group)
        # Send the frame
        self.send_frame(arbitration_id, data)

    # Functions to get specific values
    def get_module_voltage(self, address, group):
        return self.read_value(0x01, address, group)

    def get_module_current(self, address, group):
        return self.read_value(0x02, address, group)

    def get_module_current_limit(self, address, group):
        return self.read_value(0x03, address, group)

    def get_temperature_dc_board(self, address, group):
        return self.read_value(0x04, address, group)

    def get_input_phase_voltage(self, address, group):
        return self.read_value(0x05, address, group)

    def get_pfc0_voltage(self, address, group):
        return self.read_value(0x08, address, group)

    def get_pfc1_voltage(self, address, group):
        return self.read_value(0x0A, address, group)

    def get_panel_board_temperature(self, address, group):
        return self.read_value(0x0B, address, group)

    def get_voltage_phase_a(self, address, group):
        return self.read_value(0x0C, address, group)

    def get_voltage_phase_b(self, address, group):
        return self.read_value(0x0D, address, group)

    def get_voltage_phase_c(self, address, group):
        return self.read_value(0x0E, address, group)

    def get_temperature_pfc_board(self, address, group):
        return self.read_value(0x10, address, group)

    def get_rated_output_power(self, address, group):
        return self.read_value(0x11, address, group)

    def get_rated_output_current(self, address, group):
        return self.read_value(0x12, address, group)

    # Functions to set values
    def set_altitude(self, altitude, address, group):
        if 1000 <= altitude <= 5000:
            self.set_value(0x17, altitude, address, group, is_float=False)

    def set_output_current(self, current, address, group):
        current_value = int(current * 1024)
        self.set_value(0x1B, current_value, address, group, is_float=False)

    def set_group_id(self, group_id, address):
        if 0 <= group_id <= 7:
            self.set_value(0x1E, group_id, address, 0, is_float=False)

    def set_method_to_assign_address(self, method, address, group):
        self.set_value(0x1F, method, address, group, is_float=False)

    def set_output_voltage(self, voltage, address, group):
        self.set_value(0x21, voltage, address, group, is_float=True)

    def set_current_limit_fraction(self, current_limit_fraction, address, group):
        self.set_value(0x22, current_limit_fraction, address, group, is_float=True)

    def set_max_voltage_setpoint(self, voltage, address, group):
        self.set_value(0x23, voltage, address, group, is_float=True)

    def power_on_off(self, state, address, group):
        self.set_value(0x30, state, address, group, is_float=False)

    def set_reset_over_voltage(self, reset, address, group):
        self.set_value(0x31, reset, address, group, is_float=False)

    def set_over_voltage_protection(self, enable, address, group):
        self.set_value(0x3E, enable, address, group, is_float=False)

    def set_short_circuit_reset(self, reset, address, group):
        self.set_value(0x44, reset, address, group, is_float=False)

    def set_input_mode(self, mode, address, group):
        self.set_value(0x46, mode, address, group, is_float=False)

    def get_input_power(self, address, group):
        return self.read_value(0x48, address, group, is_float=False)

    def get_current_altitude_value(self, address, group):
        return self.read_value(0x4A, address, group, is_float=False)

    def get_input_working_mode(self, address, group):
        return self.read_value(0x4B, address, group, is_float=False)

    def get_serial_number(self, address, group):
        """
        Reads the low and high fields of the serial number and combines them.

        Parameters:
            address (int): The destination address for the CAN message.
            group (int): The group ID for the CAN message.

        Returns:
            int: The full serial number, or None if either read fails.
        """
        # Read the low field of the serial number
        low_field = self.read_value(0x54, address, group, is_float=False)
        if low_field is None:
            return None

        # Read the high field of the serial number
        high_field = self.read_value(0x55, address, group, is_float=False)
        if high_field is None:
            return None

        # Combine the high and low fields into a single serial number
        serial_number = (high_field << 16) | low_field
        return serial_number

    def get_dcdc_version(self, address, group):
        return self.read_value(0x56, address, group, is_float=False)

    def get_pfc_version(self, address, group):
        return self.read_value(0x57, address, group, is_float=False)

    def get_alarm_status(self, address, group):
        """
        Reads and decodes the alarm/status register.

        Parameters:
            address (int): The destination address for the CAN message.
            group (int): The group ID for the CAN message.

        Returns:
            dict: A dictionary with the status bits and their descriptions.
        """
        # Read the alarm/status value from register 0x0040
        status = self.read_value(0x40, address, group, is_float=False)
        
        if status is None:
            return None

        # Decode the status bits based on Table-2
        status_bits = {
            0: "Module fault (red light)",
            1: "Module protection (yellow light)",
            2: "Reserved",
            3: "Inside SCI communication error",
            4: "Input mode detection error (or input wiring error)",
            5: "Input mode mismatch",
            6: "Reserved",
            7: "DCDC overvoltage",
            8: "PFC voltage exception (unbalanced, overvoltage, or undervoltage)",
            9: "AC overvoltage",
            10: "Reserved",
            11: "Reserved",
            12: "Reserved",
            13: "Reserved",
            14: "AC undervoltage",
            15: "Reserved",
            16: "CAN communication error",
            17: "Unbalanced current",
            18: "Reserved",
            19: "Reserved",
            20: "Reserved",
            21: "Reserved",
            22: "DCDC status of power (0: power on, 1: power off)",
            23: "Module limit power",
            24: "Temperature limit power",
            25: "AC limit power",
            26: "Reserved",
            27: "Fans fault",
            28: "DCDC short-circuit",
            29: "Reserved",
            30: "DCDC overtemperature",
            31: "DCDC output overvoltage"
        }

        # Create a dictionary to store the active alarms
        active_alarms = {}
        for bit, description in status_bits.items():
            if status & (1 << bit):
                active_alarms[bit] = description

        return active_alarms

    # More functions can be added for other registers

    def __del__(self):
        if self.bus:
            self.bus.shutdown()

if __name__ == "__main__":
    module = UXRChargerModule(channel='/dev/ttyACM0')
    address = 0x03
    group = 0x05

    while True:
        # Read and print module voltage
        voltage = module.get_module_voltage(address, group)
        if voltage is not None:
            print(f"Module Voltage: {voltage} V")
        time.sleep(0.2)

        # Read and print module current
        current = module.get_module_current(address, group)
        if current is not None:
            print(f"Module Current: {current} A")
        time.sleep(0.2)

        # Read and print module current limit
        current_limit = module.get_module_current_limit(address, group)
        if current_limit is not None:
            print(f"Module Current Limit: {current_limit} A")
        time.sleep(0.2)

        # Read and print temperature of DC board
        temp_dc_board = module.get_temperature_dc_board(address, group)
        if temp_dc_board is not None:
            print(f"Temperature of DC Board: {temp_dc_board} °C")
        time.sleep(0.2)

        # Read and print input phase voltage (DC input voltage)
        input_voltage = module.get_input_phase_voltage(address, group)
        if input_voltage is not None:
            print(f"Input Phase Voltage: {input_voltage} V")
        time.sleep(0.2)

        # Read and print PFC0 voltage
        pfc0_voltage = module.get_pfc0_voltage(address, group)
        if pfc0_voltage is not None:
            print(f"PFC0 Voltage: {pfc0_voltage} V")
        time.sleep(0.2)

        # Read and print PFC1 voltage
        pfc1_voltage = module.get_pfc1_voltage(address, group)
        if pfc1_voltage is not None:
            print(f"PFC1 Voltage: {pfc1_voltage} V")
        time.sleep(0.2)

        # Read and print ambient temperature of panel board
        panel_temp = module.get_panel_board_temperature(address, group)
        if panel_temp is not None:
            print(f"Panel Board Temperature: {panel_temp} °C")
        time.sleep(0.2)

        # Read and print phase A voltage
        voltage_phase_a = module.get_voltage_phase_a(address, group)
        if voltage_phase_a is not None:
            print(f"Voltage Phase A: {voltage_phase_a} V")
        time.sleep(0.2)

        # Read and print phase B voltage
        voltage_phase_b = module.get_voltage_phase_b(address, group)
        if voltage_phase_b is not None:
            print(f"Voltage Phase B: {voltage_phase_b} V")
        time.sleep(0.2)

        # Read and print phase C voltage
        voltage_phase_c = module.get_voltage_phase_c(address, group)
        if voltage_phase_c is not None:
            print(f"Voltage Phase C: {voltage_phase_c} V")
        time.sleep(0.2)

        # Read and print temperature of PFC board
        temp_pfc_board = module.get_temperature_pfc_board(address, group)
        if temp_pfc_board is not None:
            print(f"Temperature of PFC Board: {temp_pfc_board} °C")
        time.sleep(0.2)

        # Read and print rated output power
        rated_power = module.get_rated_output_power(address, group)
        if rated_power is not None:
            print(f"Rated Output Power: {rated_power} W")
        time.sleep(0.2)

        # Read and print rated output current
        rated_current = module.get_rated_output_current(address, group)
        if rated_current is not None:
            print(f"Rated Output Current: {rated_current} A")
        time.sleep(0.2)

        # Continue looping indefinitely