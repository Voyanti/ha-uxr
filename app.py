import time
import os
import json
from dataclasses import dataclass

import yaml
import atexit
import paho.mqtt.client as mqtt
from uxr_charger_module import UXRChargerModule
import threading
import logging
import sys
import traceback


MAX_ATTEMPTS_SERIAL_READ = 3


@dataclass
class DeviceConfig:
    rated_power: float | None
    rated_current: float | None


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


def load_config() -> Config:
    env_path = os.environ.get("UXR_CONFIG_JSON")
    if env_path and os.path.exists(env_path):
        logging.info(f"Loading config from UXR_CONFIG_JSON={env_path}")
        with open(env_path) as f:
            cfg = Config.from_dict(json.load(f))
    elif os.path.exists("/data/options.json"):
        logging.info("Loading options.json")
        with open("/data/options.json") as f:
            cfg = Config.from_dict(json.load(f))
    elif os.path.exists("uxr-dev\\config.yaml"):
        logging.info("Loading config.yaml")
        with open("uxr-dev\\config.yaml") as f:
            cfg = Config.from_dict(yaml.load(f, Loader=yaml.FullLoader)["options"])
    else:
        sys.exit("No config file found")
    return cfg


# MQTT
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

            rated_current = initialised_device_configs[serial_no].rated_current
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
                if not rated_current:
                    return
                percentage = payload / rated_current
                logging.info(
                    "Current limit set: {} for {}%".format(percentage, serial_no)
                )
                module.set_current_limit_fraction(percentage, address, group)
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


# Helpers
def keep_all_alive_by_read_poll():
    for uxr_module in cfg.modules:
        module.get_input_power(uxr_module["CANBUS_ID"], uxr_module["GROUP_ID"])
        time.sleep(cfg.read_delay)


def read_publish(getter, suffix, serial_no, address, group, transform=None):
    with lock:
        keep_all_alive_by_read_poll()
        value = getter(address, group)
        if value is None:
            return None
        if transform is not None:
            value = transform(value)
        client.publish(f"{cfg.mqtt_base_topic}/{serial_no}/{suffix}", value)
        logging.info(f"{suffix}: {value}")
    return value


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


def get_serial_number_with_retries(module, address, group, num_retries=3):
    for attempt in range(1, num_retries+1):
        serial_no = module.get_serial_number(address, group)

        if serial_no:  # If the serial number is successfully read
            return str(serial_no)

        # Log the attempt and wait before retrying
        logging.error(f"Serial read {attempt=} failed, retrying...")
        time.sleep(cfg.read_delay)
    # If all attempts fail, return None or raise an exception
    logging.error(f"Failed to read serial number after {num_retries} attempts.")
    return None


def read_device_defaults(address, group, serial_no) -> DeviceConfig:
    rated_power = module.get_rated_output_power(address, group)
    time.sleep(cfg.read_delay)
    rated_current = module.get_rated_output_current(address, group)

    logging.info(f"Serial No: {serial_no}")
    logging.info(f"Address: {address} ")
    logging.info(f"Rated Output Power: {rated_power} W")
    logging.info(f"Rated Output Current: {rated_current} A")

    return DeviceConfig(rated_power=rated_power, rated_current=rated_current)


# Program Flow
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

        rated_current = initialised_device_configs[serial_no].rated_current
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


def init_mqtt(user, pwd, host, port) -> mqtt.Client:
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.username_pw_set(username=user, password=pwd)
    client.will_set(BRIDGE_AVAILABILITY_TOPIC, "offline", retain=True)
    client.connect(host, port, 60)
    client.loop_start()
    return client


def startup_sequence(
    turn_on_single,
    get_serial_number_with_retries,
    read_device_defaults,
    cfg,
    module,
    expected_serial_num,
    address,
    group,
) -> DeviceConfig:
    while True:
        logging.info("Switching on charger...")
        turn_on_single(expected_serial_num, address, group)
        logging.info("Switch On command sent")

        serial_no = get_serial_number_with_retries(module, address, group)
        if serial_no is None:
            logging.warning(f"Failed to read serial num for {expected_serial_num}, retrying startup...")
            continue
        if serial_no != expected_serial_num:
            raise ValueError(f"{serial_no=} found. {expected_serial_num=}")

        time.sleep(cfg.read_delay)

        device_defaults = read_device_defaults(address, group, serial_no)

        if device_defaults.rated_current is None:
            logging.error(f"Failed to read rated current for {serial_no}, retrying startup...")
            time.sleep(cfg.scan_interval)
            continue

        time.sleep(cfg.read_delay)

        module.set_current_limit_fraction(
            cfg.default_current_limit / device_defaults.rated_current, address, group
        )
        time.sleep(cfg.read_delay)
        logging.info(f"Setting default voltage for {serial_no} to {cfg.default_voltage}V")
        module.set_output_voltage(cfg.default_voltage, address, group)

        return device_defaults


if __name__ == "__main__":
    atexit.register(exit_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg = load_config()
    logging.info(f"Loaded Config: {cfg}")

    BRIDGE_AVAILABILITY_TOPIC = f"{cfg.mqtt_base_topic}/bridge/availability"

    module = UXRChargerModule(channel=cfg.port)
    initialised_device_configs: dict[str, DeviceConfig] = {}
    mqtt_connected = False

    # Initialize MQTT client
    client = init_mqtt(
        user=cfg.mqtt_user,
        pwd=cfg.mqtt_password,
        host=cfg.mqtt_host,
        port=cfg.mqtt_port,
    )
    logging.info("Done seting up MQTT client")
    logging.info("Waiting 3 seconds for power stability before switching on chargers")
    time.sleep(3)

    for uxr_module in cfg.modules:
        address = uxr_module["CANBUS_ID"]
        group = uxr_module["GROUP_ID"]
        expected_serial_num = uxr_module["SERIAL_NR"]

        initialised_device_configs[expected_serial_num] = startup_sequence(
            turn_on_single,
            get_serial_number_with_retries,
            read_device_defaults,
            cfg,
            module,
            expected_serial_num=expected_serial_num,
            address=address,
            group=group,
        )

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
                rated_current = initialised_device_configs[serial_no].rated_current
                rated_power = initialised_device_configs[serial_no].rated_power

                readings = [
                    (module.get_module_voltage, "module_voltage", None),
                    (module.get_module_current, "module_current", None),
                    (module.get_module_current_limit, "current_limit",
                        lambda v: round(v * rated_current, 2)),
                    (module.get_temperature_dc_board, "temperature_of_dc_board", None),
                    (module.get_input_phase_voltage, "input_phase_voltage", None),
                    (module.get_pfc0_voltage, "pfc0_voltage", None),
                    (module.get_pfc1_voltage, "pfc1_voltage", None),
                    (module.get_panel_board_temperature, "panel_board_temperature", None),
                    (module.get_voltage_phase_a, "voltage_phase_a", None),
                    (module.get_voltage_phase_b, "voltage_phase_b", None),
                    (module.get_voltage_phase_c, "voltage_phase_c", None),
                    (module.get_temperature_pfc_board, "temperature_of_pfc_board", None),
                    (module.get_input_power, "input_power", None),
                    (module.get_current_altitude_value, "current_altitude", None),
                    (module.get_input_working_mode, "input_working_mode", None),
                ]

                alive = False
                failed_reads = 0
                too_many_failures = False
                for getter, suffix, transform in readings:
                    value = read_publish(getter, suffix, serial_no, address, group, transform)
                    if value is not None:
                        alive = True
                        if suffix == "input_power":
                            client.publish(
                                f"{cfg.mqtt_base_topic}/{serial_no}/power",
                                1 if value > 0 else 0,
                            )
                    else:
                        failed_reads += 1
                        if failed_reads > 2:
                            logging.error(
                                f"More than 2 failed reads for {serial_no}, short-circuiting to startup sequence"
                            )
                            too_many_failures = True
                            break
                    time.sleep(cfg.read_delay)

                client.publish(
                    f"{cfg.mqtt_base_topic}/{serial_no}/rated_current", rated_current
                )
                client.publish(
                    f"{cfg.mqtt_base_topic}/{serial_no}/rated_power", rated_power
                )
                if alive and not too_many_failures:
                    client.publish(
                        f"{cfg.mqtt_base_topic}_{serial_no}/availability", "online"
                    )
                else:
                    client.publish(
                        f"{cfg.mqtt_base_topic}_{serial_no}/availability", "offline"
                    )
                    with lock:
                        initialised_device_configs[serial_no] = startup_sequence(
                            turn_on_single,
                            get_serial_number_with_retries,
                            read_device_defaults,
                            cfg,
                            module,
                            serial_no,
                            address,
                            group,
                        )

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        logging.error("Traceback: %s", traceback.format_exc())
        exit_handler()
    except KeyboardInterrupt:
        logging.error("Stopping script...")
        exit_handler()
