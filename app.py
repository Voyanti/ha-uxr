import time
import os
import json
from dataclasses import dataclass
from typing import Any
import yaml
import atexit
import paho.mqtt.client as mqtt
from uxr_charger_module import UXRChargerModule
import threading
import logging
import sys
import traceback


@dataclass
class Config:
    read_delay: float
    mqtt_host: str
    mqtt_port: int
    mqtt_base_topic: str
    mqtt_ha_discovery_topic: str
    mqtt_user: str
    mqtt_password: str
    scan_interval: float
    mqtt_ha_discovery: bool
    modules: list
    default_current_limit: float
    default_voltage: float
    port: str

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    logging.info("Connected to MQTT broker")
    mqtt_connected = True
    client.publish(BRIDGE_AVAILABILITY_TOPIC, "online", retain=True)
    for uxr_module in cfg.modules:
        serial_no = uxr_module["SERIAL_NR"]
        client.subscribe(
            [
                (f"{cfg.mqtt_base_topic}/{serial_no}/set/group_id", 0),
                (f"{cfg.mqtt_base_topic}/{serial_no}/set/output_voltage", 0),
                (f"{cfg.mqtt_base_topic}/{serial_no}/set/current_limit", 0),
                (f"{cfg.mqtt_base_topic}/{serial_no}/set/current", 0),
                (f"{cfg.mqtt_base_topic}/{serial_no}/set/power", 0),
            ]
        )


def on_disconnect(client, userdata, rc):
    if rc != 0:
        logging.error("Unexpected disconnection.")
    else:
        logging.error("Disconnected successfully.")
    global mqtt_connected
    logging.error("Disconnected from MQTT broker")
    mqtt_connected = False


def on_message(client, userdata, msg):
    with lock:
        topic = msg.topic
        for uxr_module in cfg.modules:
            serial_no = uxr_module["SERIAL_NR"]
            address = uxr_module["CANBUS_ID"]
            group = uxr_module["GROUP_ID"]
            if serial_no not in initialised_device_configs:
                logging.error(
                    f"Cannot set value for {serial_no} since it is not initialised"
                )
                return

            rated_current = initialised_device_configs[serial_no]["rated_current"]
            if topic == f"{cfg.mqtt_base_topic}/{serial_no}/set/altitude":
                payload = float(msg.payload.decode())
                module.set_altitude(payload, address, group)
            elif topic == f"{cfg.mqtt_base_topic}/{serial_no}/set/group_id":
                payload = float(msg.payload.decode())
                module.set_group_id(int(payload), address)
            elif topic == f"{cfg.mqtt_base_topic}/{serial_no}/set/output_voltage":
                payload = float(msg.payload.decode())
                logging.info(f"Setting output voltage for {serial_no} to {payload}")
                module.set_output_voltage(payload, address, group)
            elif topic == f"{cfg.mqtt_base_topic}/{serial_no}/set/current_limit":
                payload = float(msg.payload.decode())
                percentage = payload / rated_current
                logging.info(
                    "Current limit set: {} for {}%".format(percentage, serial_no)
                )
                module.set_current_limit(percentage, address, group)
            elif topic == f"{cfg.mqtt_base_topic}/{serial_no}/set/current":
                payload = float(msg.payload.decode())
                module.set_output_current(payload, address, group)
            elif topic == f"{cfg.mqtt_base_topic}/{serial_no}/set/power":
                payload = int(msg.payload.decode())
                if payload:
                    module.power_on_off(0x00000000, address, group)
                else:
                    module.power_on_off(0x00010000, address, group)
                power_topic = f"{cfg.mqtt_base_topic}/{serial_no}/power"
                client.publish(power_topic, payload)


def keep_all_alive_by_read_poll():
    for uxr_module in cfg.modules:
        module.get_input_power(uxr_module["CANBUS_ID"], uxr_module["GROUP_ID"])
        time.sleep(cfg.read_delay)


def turn_on_all():
    for i in range(0, 5):
        time.sleep(1)
        for uxr_module in cfg.modules:
            serial_no = uxr_module["SERIAL_NR"]
            address = uxr_module["CANBUS_ID"]
            client.publish(f"{cfg.mqtt_base_topic}_{serial_no}/availability", "offline")
            logging.info(f"Switching on Serial: {serial_no} on Canbus ID: {address}")
            module.power_on_off(0x00000000, address, uxr_module["GROUP_ID"])
            time.sleep(cfg.read_delay)


def turn_on_single(serial_no, address, group):
    for i in range(0, 5):
        time.sleep(1)
        client.publish(f"{cfg.mqtt_base_topic}_{serial_no}/availability", "offline")
        logging.info(f"Switching on Serial: {serial_no} on Canbus ID: {address}")
        module.power_on_off(0x00000000, address, group)
        time.sleep(cfg.read_delay)


# Function to read the serial number with retries
def get_serial_number_with_retries(module, address, group):
    for attempt in range(MAX_ATTEMPTS):
        serial_no = module.get_serial_number(address, group)

        if serial_no:  # If the serial number is successfully read
            return str(serial_no)

        # Log the attempt and wait before retrying
        logging.error(f"Attempt {attempt + 1} failed, retrying...")
        time.sleep(cfg.read_delay)
    # If all attempts fail, return None or raise an exception
    logging.error(f"Failed to read serial number after {MAX_ATTEMPTS} attempts.")
    return None


def exit_handler():
    logging.error("Script exiting")
    for uxr_module in cfg.modules:
        serial_no = uxr_module["SERIAL_NR"]
        client.publish(f"{cfg.mqtt_base_topic}_{serial_no}/availability", "offline")
    client.publish(BRIDGE_AVAILABILITY_TOPIC, "offline", retain=True)
    client.loop_stop()


# HA Discovery Function
def ha_discovery(serial_no):
    if cfg.mqtt_ha_discovery:
        logging.info("Publishing HA Discovery topics...")
        device = {
            "manufacturer": "UXR",
            "model": "ChargerModule",
            "identifiers": [f"uxr_charger_{serial_no}"],
            "name": f"UXR Charger {serial_no}",
        }

        availability_topic = f"{cfg.mqtt_base_topic}_{serial_no}/availability"
        availability_block = {
            "availability": [
                {"topic": availability_topic},
                {"topic": BRIDGE_AVAILABILITY_TOPIC},
            ],
            "availability_mode": "all",
        }

        parameters = {
            "Module Voltage": {"device_class": "voltage", "unit": "V"},
            "Module Current": {"device_class": "current", "unit": "A"},
            "Rated Current": {"device_class": "current", "unit": "A"},
            "Rated Power": {"device_class": "current", "unit": "W"},
            "Current Limit": {"device_class": "current", "unit": "A"},
            "Temperature of DC Board": {"device_class": "temperature", "unit": "°C"},
            "Input Phase Voltage": {"device_class": "voltage", "unit": "V"},
            "PFC0 Voltage": {"device_class": "voltage", "unit": "V"},
            "PFC1 Voltage": {"device_class": "voltage", "unit": "V"},
            "Panel Board Temperature": {"device_class": "temperature", "unit": "°C"},
            "Voltage Phase A": {"device_class": "voltage", "unit": "V"},
            "Voltage Phase B": {"device_class": "voltage", "unit": "V"},
            "Voltage Phase C": {"device_class": "voltage", "unit": "V"},
            "Temperature of PFC Board": {"device_class": "temperature", "unit": "°C"},
            "Input Power": {"device_class": "power", "unit": "W"},
            "Current Altitude": {"device_class": "none", "unit": "m"},
            "Input Working Mode": {"device_class": "none", "unit": None},
            "Alarm Status": {"device_class": "none", "unit": None},
        }

        for param, details in parameters.items():
            discovery_payload = {
                "name": param,
                "unique_id": f"uxr_{serial_no}_{param.replace(' ', '_').lower()}",
                "state_topic": f"{cfg.mqtt_base_topic}/{serial_no}/{param.replace(' ', '_').lower()}",
                "device": device,
                "device_class": details.get("device_class"),
                "unit_of_measurement": details.get("unit"),
                **availability_block,
            }
            discovery_topic = f"{cfg.mqtt_ha_discovery_topic}/sensor/uxr_{serial_no}/{param.replace(' ', '_').lower()}/config"
            client.publish(discovery_topic, json.dumps(discovery_payload), retain=True)

        rated_current = initialised_device_configs[serial_no]["rated_current"]
        settable_parameters = {
            "Current Limit": {
                "min": 0,
                "max": rated_current,
                "step": 0.1,
                "unit": "A",
                "command_topic": f"{cfg.mqtt_base_topic}/{serial_no}/set/current_limit",
            },
            "Output Voltage": {
                "min": 735,
                "max": 810,
                "step": 0.1,
                "unit": "V",
                "command_topic": f"{cfg.mqtt_base_topic}/{serial_no}/set/output_voltage",
            },
            "Output Current": {
                "min": 0,
                "max": rated_current,
                "step": 0.1,
                "unit": "A",
                "command_topic": f"{cfg.mqtt_base_topic}/{serial_no}/set/current",
            },
            "Altitude": {
                "min": 0,
                "max": 5000,
                "step": 100,
                "unit": "m",
                "command_topic": f"{cfg.mqtt_base_topic}/{serial_no}/set/altitude",
            },
        }

        for param, details in settable_parameters.items():
            discovery_payload = {
                "name": param,
                "unique_id": f"uxr_{serial_no}_{param.replace(' ', '_').lower()}",
                "command_topic": details["command_topic"],
                "min": details["min"],
                "max": details["max"],
                "step": details["step"],
                "unit_of_measurement": details["unit"],
                "device": device,
                **availability_block,
            }
            discovery_topic = f"{cfg.mqtt_ha_discovery_topic}/number/uxr_{serial_no}/{param.replace(' ', '_').lower()}/config"
            client.publish(discovery_topic, json.dumps(discovery_payload), retain=True)

        switch_name = "power"
        command_topic = f"{cfg.mqtt_base_topic}/{serial_no}/set/{switch_name.lower()}"
        state_topic = f"{cfg.mqtt_base_topic}/{serial_no}/{switch_name.lower()}"
        unique_id = f"uxr_{serial_no}_{switch_name.lower()}"

        discovery_payload = {
            "name": switch_name,
            "unique_id": unique_id,
            "state_topic": state_topic,
            "command_topic": command_topic,
            "payload_on": 1,
            "payload_off": 0,
            "state_on": 1,
            "state_off": 0,
            "device": device,
            **availability_block,
        }

        discovery_topic = f"{cfg.mqtt_ha_discovery_topic}/switch/uxr_{serial_no}/{switch_name.lower()}/config"
        client.publish(discovery_topic, json.dumps(discovery_payload), retain=True)

        state_topic = f"{cfg.mqtt_base_topic}/{serial_no}/{switch_name.lower()}"
        client.publish(state_topic, 1)

        client.publish(availability_topic, "online")


def read_device_defaults(address, group, serial_no):
    rated_power = module.get_rated_output_power(address, group)
    time.sleep(cfg.read_delay)
    rated_current = module.get_rated_output_current(address, group)

    logging.info(f"Serial No: {serial_no}")
    logging.info(f"Address: {address} ")
    logging.info(f"Rated Output Power: {rated_power} W")
    logging.info(f"Rated Output Current: {rated_current} A")

    return {
        "rated_power": rated_power,
        "rated_current": rated_current,
        "serial_no": serial_no,
    }


if __name__ == "__main__":
    atexit.register(exit_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if os.path.exists("/data/options.json"):
        logging.info("Loading options.json")
        with open("/data/options.json") as f:
            cfg = Config.from_dict(json.load(f))
    elif os.path.exists("uxr-dev\\config.yaml"):
        logging.info("Loading config.yaml")
        with open("uxr-dev\\config.yaml") as f:
            cfg = Config.from_dict(yaml.load(f, Loader=yaml.FullLoader)["options"])
    else:
        sys.exit("No config file found")
    logging.info(f"Config: {cfg}")

    BRIDGE_AVAILABILITY_TOPIC = f"{cfg.mqtt_base_topic}/bridge/availability"

    module = UXRChargerModule(channel=cfg.port)
    initialised_device_configs: dict[str, dict[str, Any]] = {}
    mqtt_connected = False

    # Initialize MQTT client
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.username_pw_set(username=cfg.mqtt_user, password=cfg.mqtt_password)
    client.will_set(BRIDGE_AVAILABILITY_TOPIC, "offline", retain=True)
    client.connect(cfg.mqtt_host, cfg.mqtt_port, 60)
    client.loop_start()

    logging.info("Waiting 5 seconds for power stability before switching on chargers")
    time.sleep(5)
    logging.info("Switching on chargers...")
    turn_on_all()
    logging.info("Chargers switched on")

    MAX_ATTEMPTS = 1500

    # Loop through each address in the list and create an entry in the devices dictionary
    for uxr_module in cfg.modules:
        address = uxr_module["CANBUS_ID"]
        group = uxr_module["GROUP_ID"]
        expected_serial_no = uxr_module["SERIAL_NR"]

        serial_no = get_serial_number_with_retries(module, address, group)
        if serial_no is None:
            raise ValueError(f"Failed to read serial num after {MAX_ATTEMPTS=}.")
        if serial_no != expected_serial_no:
            raise ValueError(f"{serial_no=} found. {expected_serial_no=}")

        time.sleep(cfg.read_delay)


        device_defaults = read_device_defaults(address, group, serial_no)
        initialised_device_configs[serial_no] = device_defaults

        time.sleep(cfg.read_delay)

        # set_device_defaults(address, group, read_)
        # set defaults
        if device_defaults["rated_current"]:
            module.set_current_limit(
                cfg.default_current_limit / device_defaults["rated_current"], address, group
            )
        time.sleep(cfg.read_delay)
        logging.info(
            f"Setting default voltage for {serial_no} to {cfg.default_voltage}V"
        )
        module.set_output_voltage(cfg.default_voltage, address, group)

    lock = threading.Lock()

    # Main loop to continuously read parameters
    try:
        for uxr_module in cfg.modules:
            ha_discovery(uxr_module["SERIAL_NR"])
        while True:
            for uxr_module in cfg.modules:
                serial_no = uxr_module["SERIAL_NR"]
                address = uxr_module["CANBUS_ID"]
                group = uxr_module["GROUP_ID"]
                logging.info("====================")
                logging.info(f"Serial: {serial_no}")
                logging.info(f"Address: {address}")
                alive = False
                rated_current = initialised_device_configs[serial_no]["rated_current"]
                rated_power = initialised_device_configs[serial_no]["rated_power"]
                with lock:
                    keep_all_alive_by_read_poll()
                    voltage = module.get_module_voltage(address, group)
                    if voltage is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/module_voltage", voltage
                        )
                        logging.info(f"module_voltage: {voltage}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    current = module.get_module_current(address, group)
                    if current is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/module_current", current
                        )
                        logging.info(f"module_current: {current}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    current_limit = module.get_module_current_limit(address, group)
                    if current_limit is not None:
                        current_limit = round(current_limit * rated_current, 2)
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/current_limit",
                            current_limit,
                        )
                        logging.info(f"current_limit: {current_limit}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    temp_dc_board = module.get_temperature_dc_board(address, group)
                    if temp_dc_board is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/temperature_of_dc_board",
                            temp_dc_board,
                        )
                        logging.info(f"temperature_of_dc_board: {temp_dc_board}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    input_voltage = module.get_input_phase_voltage(address, group)
                    if input_voltage is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/input_phase_voltage",
                            input_voltage,
                        )
                        logging.info(f"input_phase_voltage: {input_voltage}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    pfc0_voltage = module.get_pfc0_voltage(address, group)
                    if pfc0_voltage is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/pfc0_voltage",
                            pfc0_voltage,
                        )
                        logging.info(f"pfc0_voltage: {pfc0_voltage}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    pfc1_voltage = module.get_pfc1_voltage(address, group)
                    if pfc1_voltage is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/pfc1_voltage",
                            pfc1_voltage,
                        )
                        logging.info(f"pfc1_voltage: {pfc1_voltage}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    panel_temp = module.get_panel_board_temperature(address, group)
                    if panel_temp is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/panel_board_temperature",
                            panel_temp,
                        )
                        logging.info(f"panel_board_temperature: {panel_temp}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    voltage_phase_a = module.get_voltage_phase_a(address, group)
                    if voltage_phase_a is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/voltage_phase_a",
                            voltage_phase_a,
                        )
                        logging.info(f"voltage_phase_a: {voltage_phase_a}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    voltage_phase_b = module.get_voltage_phase_b(address, group)
                    if voltage_phase_b is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/voltage_phase_b",
                            voltage_phase_b,
                        )
                        logging.info(f"voltage_phase_b: {voltage_phase_b}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    voltage_phase_c = module.get_voltage_phase_c(address, group)
                    if voltage_phase_c is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/voltage_phase_c",
                            voltage_phase_c,
                        )
                        logging.info(f"voltage_phase_c: {voltage_phase_c}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    temp_pfc_board = module.get_temperature_pfc_board(address, group)
                    if temp_pfc_board is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/temperature_of_pfc_board",
                            temp_pfc_board,
                        )
                        logging.info(f"temperature_of_pfc_board: {temp_pfc_board}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    input_power = module.get_input_power(address, group)
                    if input_power is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/input_power",
                            input_power,
                        )
                        logging.info(f"input_power: {input_power}")
                        power = 1 if input_power > 0 else 0
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/power", power
                        )
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    altitude_value = module.get_current_altitude_value(address, group)
                    if altitude_value is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/current_altitude",
                            altitude_value,
                        )
                        logging.info(f"altitude_value: {altitude_value}")
                        alive = True
                time.sleep(cfg.read_delay)

                with lock:
                    keep_all_alive_by_read_poll()
                    input_mode = module.get_input_working_mode(address, group)
                    if input_mode is not None:
                        client.publish(
                            f"{cfg.mqtt_base_topic}/{serial_no}/input_working_mode",
                            input_mode,
                        )
                        logging.info(f"input_working_mode: {input_mode}")
                        alive = True
                time.sleep(cfg.read_delay)

                client.publish(
                    f"{cfg.mqtt_base_topic}/{serial_no}/rated_current", rated_current
                )
                client.publish(
                    f"{cfg.mqtt_base_topic}/{serial_no}/rated_power", rated_power
                )
                if alive:
                    client.publish(
                        f"{cfg.mqtt_base_topic}_{serial_no}/availability", "online"
                    )
                else:
                    client.publish(
                        f"{cfg.mqtt_base_topic}_{serial_no}/availability", "offline"
                    )
                    with lock:
                        turn_on_single(
                            serial_no=serial_no, address=address, group=group
                        )

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        logging.error("Traceback: %s", traceback.format_exc())
        exit_handler()
    except KeyboardInterrupt:
        logging.error("Stopping script...")
        exit_handler()
