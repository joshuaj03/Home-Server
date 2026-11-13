#!/usr/bin/env python3
"""
JBL Clip 3 Bluetooth Auto-Reconnect & Keepalive Daemon for HomePi
Continuously monitors Bluetooth connection to JBL Clip 3 (30:C0:1B:4D:39:49).
- Automatically reconnects if the speaker disconnects or is powered on.
- Sets PipeWire default sink to the JBL speaker.
- Sends an inaudible keepalive pulse every 5 minutes to prevent the speaker
  from triggering auto-standby power down.
"""

import logging
import os
import signal
import subprocess
import sys
import time
import dbus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bluetooth-reconnect")

MAC_RAW = "30:C0:1B:4D:39:49"
MAC_DBUS = "30_C0_1B_4D_39_49"
DEV_PATH = f"/org/bluez/hci0/dev_{MAC_DBUS}"
ADAPTER_PATH = "/org/bluez/hci0"
SINK_NAME = f"bluez_output.{MAC_DBUS}.1"
KEEPALIVE_FILE = "/home/jake/HomeServer/scripts/keepalive.wav"
KEEPALIVE_INTERVAL = 300  # 5 minutes

running = True


def handle_signal(sig, frame):
    global running
    logger.info("Shutdown signal received, exiting gracefully...")
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def ensure_adapter_powered(bus):
    try:
        adapter_obj = bus.get_object("org.bluez", ADAPTER_PATH)
        props = dbus.Interface(adapter_obj, "org.freedesktop.DBus.Properties")
        is_powered = bool(props.Get("org.bluez.Adapter1", "Powered"))
        if not is_powered:
            logger.info("Bluetooth adapter hci0 is powered off. Powering on...")
            props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
            time.sleep(1)
    except Exception as e:
        logger.debug(f"Failed to check/power adapter: {e}")


def set_default_audio_sink():
    try:
        time.sleep(1.5)
        res = subprocess.run(
            ["pactl", "set-default-sink", SINK_NAME],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            logger.info(f"PipeWire default sink set to: {SINK_NAME}")
        else:
            logger.debug(f"pactl set-default-sink output: {res.stderr.strip()}")
    except Exception as e:
        logger.warning(f"Failed to set default sink: {e}")


def send_keepalive():
    if os.path.exists(KEEPALIVE_FILE):
        try:
            subprocess.run(
                ["paplay", "--volume=15000", KEEPALIVE_FILE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            logger.debug("Keepalive pulse sent to speaker.")
        except Exception as e:
            logger.debug(f"Keepalive error: {e}")


def main():
    logger.info(f"Starting JBL Clip 3 ({MAC_RAW}) auto-reconnect service with keepalive...")
    was_connected = None
    last_keepalive = time.time()

    while running:
        try:
            bus = dbus.SystemBus()
            ensure_adapter_powered(bus)

            dev_obj = bus.get_object("org.bluez", DEV_PATH)
            props = dbus.Interface(dev_obj, "org.freedesktop.DBus.Properties")
            dev = dbus.Interface(dev_obj, "org.bluez.Device1")

            connected = bool(props.Get("org.bluez.Device1", "Connected"))

            if connected:
                if was_connected is not True:
                    logger.info("JBL Clip 3 is CONNECTED.")
                    set_default_audio_sink()
                    was_connected = True
                    last_keepalive = time.time()

                # Check if keepalive interval reached
                now = time.time()
                if now - last_keepalive >= KEEPALIVE_INTERVAL:
                    send_keepalive()
                    last_keepalive = now

                # Sleep 5 seconds
                for _ in range(5):
                    if not running:
                        break
                    time.sleep(1)
            else:
                if was_connected is not False:
                    logger.warning("JBL Clip 3 is DISCONNECTED. Attempting reconnection...")
                    was_connected = False

                try:
                    dev.Connect()
                    logger.info("Connection command succeeded!")
                    was_connected = True
                    set_default_audio_sink()
                    last_keepalive = time.time()
                    time.sleep(3)
                except dbus.DBusException as de:
                    logger.debug(f"Connect failed (speaker likely off/sleeping): {de}")
                    for _ in range(5):
                        if not running:
                            break
                        time.sleep(1)

        except dbus.DBusException as e:
            logger.debug(f"D-Bus error: {e}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in monitor loop: {e}")
            time.sleep(5)

    logger.info("JBL Clip 3 auto-reconnect service stopped.")


if __name__ == "__main__":
    main()
