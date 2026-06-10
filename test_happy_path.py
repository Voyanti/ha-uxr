"""End-to-end happy-path test for app.py.

Starts a local mosquitto broker on a free port, writes a temp config JSON,
spawns app.py in a subprocess with `uxr_charger_module` swapped for the mock,
subscribes to expected MQTT topics, waits until the per-module read cycle
has published every expected topic, then SIGINTs the app and reports results.

Requires:
  - mosquitto on PATH
  - paho-mqtt installed in the active environment
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import paho.mqtt.client as mqtt


HERE = Path(__file__).resolve().parent
APP_PATH = HERE / "app.py"
MOCK_PATH = HERE / "mock_uxr_charger_module.py"

# Test config: three modules, fast read cadence so the test finishes quickly.
MODULES = [
    {"SERIAL_NR": "1", "HA_PREFIX": "GRID", "CANBUS_ID": 0, "GROUP_ID": 5},
    {"SERIAL_NR": "2", "HA_PREFIX": "GRID", "CANBUS_ID": 1, "GROUP_ID": 5},
    {"SERIAL_NR": "3", "HA_PREFIX": "GRID", "CANBUS_ID": 2, "GROUP_ID": 5},
]
BASE_TOPIC = "uxr"

# Topics published by the happy-path read loop, per serial.
READ_TOPIC_SUFFIXES = [
    "module_voltage",
    "module_current",
    "current_limit",
    "temperature_of_dc_board",
    "input_phase_voltage",
    "pfc0_voltage",
    "pfc1_voltage",
    "panel_board_temperature",
    "voltage_phase_a",
    "voltage_phase_b",
    "voltage_phase_c",
    "temperature_of_pfc_board",
    "input_power",
    "current_altitude",
    "input_working_mode",
    "power",
    "rated_current",
    "rated_power",
]


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_mosquitto(port: int, workdir: Path) -> subprocess.Popen:
    conf = workdir / "mosquitto.conf"
    conf.write_text(
        f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n"
    )
    proc = subprocess.Popen(
        ["mosquitto", "-c", str(conf)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for broker to accept connections.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return proc
        except OSError:
            time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("mosquitto did not start in time")


def write_config(workdir: Path, port: int) -> Path:
    cfg = {
        "read_delay": 0.01,
        "mqtt_host": "127.0.0.1",
        "mqtt_port": port,
        "mqtt_base_topic": BASE_TOPIC,
        "mqtt_ha_discovery_topic": "homeassistant",
        "mqtt_user": "test",
        "mqtt_password": "test",
        "scan_interval": 1,
        "mqtt_ha_discovery": True,
        "modules": MODULES,
        "default_current_limit": 30,
        "default_voltage": 775,
        "port": "/dev/null",
    }
    path = workdir / "options.json"
    path.write_text(json.dumps(cfg))
    return path


BOOTSTRAP = """
import sys, importlib.util, runpy
spec = importlib.util.spec_from_file_location('uxr_charger_module', {mock!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sys.modules['uxr_charger_module'] = m
runpy.run_path({app!r}, run_name='__main__')
"""


def start_app(config_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["UXR_CONFIG_JSON"] = str(config_path)
    code = BOOTSTRAP.format(mock=str(MOCK_PATH), app=str(APP_PATH))
    return subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def expected_topics() -> set[str]:
    topics = set()
    for m in MODULES:
        s = m["SERIAL_NR"]
        for suf in READ_TOPIC_SUFFIXES:
            topics.add(f"{BASE_TOPIC}/{s}/{suf}")
        topics.add(f"{BASE_TOPIC}_{s}/availability")
    topics.add(f"{BASE_TOPIC}/bridge/availability")
    return topics


def run() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="uxr-test-"))
    print(f"[test] workdir: {workdir}")

    port = find_free_port()
    print(f"[test] broker port: {port}")

    broker = start_mosquitto(port, workdir)
    app_proc = None
    rc = 1
    try:
        config_path = write_config(workdir, port)

        received: dict[str, str] = {}

        sub = mqtt.Client(client_id="test-sub")
        sub.connect("127.0.0.1", port, 30)

        def on_message(_c, _u, msg):
            received[msg.topic] = msg.payload.decode(errors="replace")

        sub.on_message = on_message
        sub.subscribe("#")
        sub.loop_start()

        app_proc = start_app(config_path)
        print(f"[test] app pid: {app_proc.pid}")

        want = expected_topics()
        deadline = time.time() + 30
        while time.time() < deadline:
            missing = want - received.keys()
            if not missing:
                break
            if app_proc.poll() is not None:
                print(f"[test] app exited early rc={app_proc.returncode}")
                out, _ = app_proc.communicate()
                if out:
                    print(f"[test] app output:\n{out}")
                break
            time.sleep(0.2)

        sub.loop_stop()
        sub.disconnect()

        missing = want - received.keys()
        if missing:
            print(f"[test] FAIL — {len(missing)} topic(s) missing:")
            for t in sorted(missing):
                print(f"  - {t}")
            rc = 1
        else:
            print(f"[test] OK — all {len(want)} expected topics received")
            avail = received.get(f"{BASE_TOPIC}_1/availability")
            bridge = received.get(f"{BASE_TOPIC}/bridge/availability")
            print(f"[test]   {BASE_TOPIC}_1/availability = {avail!r}")
            print(f"[test]   {BASE_TOPIC}/bridge/availability = {bridge!r}")
            print(f"[test]   {BASE_TOPIC}/1/module_voltage = "
                  f"{received.get(f'{BASE_TOPIC}/1/module_voltage')!r}")
            rc = 0
    finally:
        if app_proc and app_proc.poll() is None:
            app_proc.send_signal(signal.SIGINT)
            try:
                out, _ = app_proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                app_proc.kill()
                out, _ = app_proc.communicate()
            if out:
                tail = "\n".join(out.splitlines())
                print(f"[test] app output:\n{tail}")
        broker.terminate()
        try:
            broker.wait(timeout=3)
        except subprocess.TimeoutExpired:
            broker.kill()

    return rc


if __name__ == "__main__":
    sys.exit(run())
