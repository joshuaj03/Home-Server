#!/usr/bin/env python3
"""
HomePi Smart Speaker Media Controls & Volume Synchronization Bridge
Bridges JBL Clip 3 physical buttons (Play/Pause, Next, Prev, Volume) with Home Assistant:

1. AVRCP Absolute Volume Synchronization:
   - When +/- buttons are pressed on the speaker, BlueZ updates MediaTransport1.Volume.
     This daemon immediately updates Home Assistant's media_player.vlc_telnet volume slider.
   - When the volume slider is adjusted in Home Assistant, updates the PipeWire Bluetooth sink
     volume, adjusting the speaker's physical hardware amplifier via AVRCP.

2. Hardware Media Key Listener:
   - Reads Linux input events from /dev/input/event* matching 'JBL Clip 3 (AVRCP)'.
   - Translates KEY_PLAYPAUSE, KEY_NEXTSONG, KEY_PREVIOUSSONG into Home Assistant
     media_player service calls.
"""

import json
import logging
import os
import re
import select
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bluetooth-media-bridge")

HA_BASE_URL = "http://127.0.0.1:8123"
ENTITY_ID = "media_player.vlc_telnet"
TOKEN_FILE = "/home/jake/HomeServer/.ha_token"
SINK_NAME = "bluez_output.30_C0_1B_4D_39_49.1"

running = True
last_speaker_sync_time = 0.0
last_ha_sync_time = 0.0
last_button_time = 0.0
current_volume = 0.3


def handle_signal(sig, frame):
    global running
    logger.info("Shutdown signal received, exiting...")
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def get_ha_token():
    try:
        with open(TOKEN_FILE, "r") as f:
            return f.read().strip()
    except Exception as e:
        logger.error(f"Failed to read HA token from {TOKEN_FILE}: {e}")
        return None


def call_ha_service(service, data):
    token = get_ha_token()
    if not token:
        return
    url = f"{HA_BASE_URL}/api/services/media_player/{service}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception as e:
        logger.debug(f"HA service {service} failed: {e}")


def find_avrcp_device():
    try:
        with open("/proc/bus/input/devices", "r") as f:
            text = f.read()
        for block in text.split("\n\n"):
            if "JBL Clip 3 (AVRCP)" in block:
                for line in block.split("\n"):
                    if line.startswith("H: Handlers="):
                        for part in line.split():
                            if part.startswith("event"):
                                path = f"/dev/input/{part}"
                                if os.path.exists(path):
                                    return path
    except Exception as e:
        logger.debug(f"Error inspecting input devices: {e}")
    return None


def input_event_listener():
    """Listens for hardware button events from /dev/input/event* (AVRCP)"""
    global last_button_time
    logger.info("Starting AVRCP hardware key listener thread...")

    while running:
        dev_path = find_avrcp_device()
        if not dev_path:
            time.sleep(2)
            continue

        try:
            fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
            logger.info(f"Attached to AVRCP input device: {dev_path}")
        except Exception as e:
            logger.debug(f"Failed to open {dev_path}: {e}")
            time.sleep(2)
            continue

        try:
            while running:
                r, _, _ = select.select([fd], [], [], 1.0)
                if not r:
                    continue

                data = os.read(fd, 24)
                if len(data) == 24:
                    sec, usec, ev_type, ev_code, ev_value = struct.unpack("qqHHi", data)
                    # ev_type == 1 is EV_KEY, ev_value == 1 is key press
                    if ev_type == 1 and ev_value == 1:
                        now = time.time()
                        if now - last_button_time < 0.35:
                            continue  # Debounce
                        last_button_time = now

                        if ev_code in (164, 207, 119):  # PLAYPAUSE, PLAY, PAUSE
                            logger.info("[BUTTON] Play/Pause pressed on JBL Clip 3")
                            call_ha_service("media_play_pause", {"entity_id": ENTITY_ID})
                        elif ev_code == 163:  # NEXTSONG
                            logger.info("[BUTTON] Next Track pressed on JBL Clip 3")
                            call_ha_service("media_next_track", {"entity_id": ENTITY_ID})
                        elif ev_code == 165:  # PREVIOUSSONG
                            logger.info("[BUTTON] Previous Track pressed on JBL Clip 3")
                            call_ha_service("media_previous_track", {"entity_id": ENTITY_ID})
        except Exception as e:
            logger.debug(f"Input listener stream interrupted: {e}")
        finally:
            try:
                os.close(fd)
            except Exception:
                pass
            time.sleep(1)


def get_vlc_sink_input_volume():
    """Extracts volume strictly from the VLC media player sink-input block"""
    try:
        res = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for block in res.stdout.split("Sink Input #"):
            if not block.strip():
                continue
            if "vlc" in block.lower():
                m = re.search(r"Volume:.*?(\d+)%", block)
                if m:
                    return int(m.group(1))
    except Exception as e:
        logger.debug(f"Error reading sink-inputs: {e}")
    return None


def pactl_sink_input_watcher():
    """Watches for volume slider adjustments in Home Assistant and syncs to speaker"""
    global last_speaker_sync_time, last_ha_sync_time, current_volume
    logger.info("Starting PulseAudio sink-input volume watcher...")

    while running:
        try:
            proc = subprocess.Popen(
                ["pactl", "subscribe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            while running and proc.poll() is None:
                line = proc.stdout.readline()
                if not line:
                    break
                # When HA adjusts volume, VLC sink-input changes
                if "sink-input" in line and "change" in line:
                    now = time.time()
                    if now - last_speaker_sync_time < 1.2:
                        # Caused by speaker-to-HA sync, ignore
                        continue

                    # Query new VLC sink-input volume strictly
                    vlc_vol_pct = get_vlc_sink_input_volume()
                    if vlc_vol_pct is not None:
                        ha_vol = vlc_vol_pct / 100.0
                        if abs(ha_vol - current_volume) >= 0.03:
                            current_volume = ha_vol
                            last_ha_sync_time = now
                            logger.info(f"[VOLUME] VLC stream volume changed to {vlc_vol_pct}%. Syncing to JBL hardware sink...")
                            subprocess.run(
                                ["pactl", "set-sink-volume", SINK_NAME, f"{vlc_vol_pct}%"],
                                capture_output=True,
                            )
        except Exception as e:
            logger.debug(f"Pactl watcher exception: {e}")
            time.sleep(2)


def ha_volume_poller():
    """Polls Home Assistant volume in case slider is moved while VLC is idle"""
    global last_speaker_sync_time, last_ha_sync_time, current_volume
    logger.info("Starting Home Assistant volume polling thread...")

    while running:
        time.sleep(1.5)
        now = time.time()
        if now - last_speaker_sync_time < 2.0:
            continue

        token = get_ha_token()
        if not token:
            continue

        try:
            url = f"{HA_BASE_URL}/api/states/{ENTITY_ID}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.load(resp)
                ha_vol = data.get("attributes", {}).get("volume_level")
                if ha_vol is not None and abs(ha_vol - current_volume) >= 0.03:
                    current_volume = ha_vol
                    last_ha_sync_time = now
                    pct = int(round(ha_vol * 100))
                    logger.info(f"[VOLUME] Home Assistant volume slider adjusted to {pct}%. Syncing to JBL hardware sink...")
                    subprocess.run(
                        ["pactl", "set-sink-volume", SINK_NAME, f"{pct}%"],
                        capture_output=True,
                    )
        except Exception as e:
            logger.debug(f"HA volume poll error: {e}")


def on_dbus_property_changed(interface, changed_props, invalidated_props, path):
    """Fires when physical +/- buttons on the speaker change MediaTransport1.Volume"""
    global last_speaker_sync_time, last_ha_sync_time, current_volume

    if interface != "org.bluez.MediaTransport1":
        return

    if "Volume" in changed_props:
        now = time.time()
        if now - last_ha_sync_time < 1.2:
            # Caused by HA-to-speaker sync, ignore
            return

        raw_vol = int(changed_props["Volume"])
        speaker_vol = round(raw_vol / 127.0, 2)
        if abs(speaker_vol - current_volume) >= 0.02:
            current_volume = speaker_vol
            last_speaker_sync_time = now
            logger.info(f"[VOLUME] Speaker physical buttons set volume to {speaker_vol:.0%}. Syncing to Home Assistant...")
            call_ha_service("volume_set", {"entity_id": ENTITY_ID, "volume_level": speaker_vol})


def main():
    logger.info("Initializing JBL Clip 3 Media Controls & Volume Synchronization Bridge...")

    # Start input listener thread
    t_input = threading.Thread(target=input_event_listener, daemon=True)
    t_input.start()

    # Start pactl sink-input watcher thread
    t_pactl = threading.Thread(target=pactl_sink_input_watcher, daemon=True)
    t_pactl.start()

    # Start Home Assistant volume poller thread
    t_ha = threading.Thread(target=ha_volume_poller, daemon=True)
    t_ha.start()

    # Setup D-Bus main loop for volume property monitoring
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    bus.add_signal_receiver(
        on_dbus_property_changed,
        signal_name="PropertiesChanged",
        dbus_interface="org.freedesktop.DBus.Properties",
        path_keyword="path",
    )

    loop = GLib.MainLoop()

    def check_running():
        if not running:
            loop.quit()
            return False
        return True

    GLib.timeout_add(500, check_running)

    try:
        loop.run()
    except (KeyboardInterrupt, SystemExit):
        pass

    logger.info("Media Controls & Volume Bridge stopped.")


if __name__ == "__main__":
    main()
