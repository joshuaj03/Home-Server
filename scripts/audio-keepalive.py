#!/usr/bin/env python3
"""
HomePi Smart Speaker Audio Keep-Alive Daemon
Prevents JBL Clip 3 (and other portable Bluetooth speakers) from triggering
their internal 15-minute auto-standby / auto-power-off firmware timer.

Streams an imperceptible 5 Hz infrasound signal (stereo, 44.1 kHz, -36 dBFS)
directly into the Bluetooth audio sink via PipeWire:
- Inaudible: 5 Hz is far below the human hearing threshold (20 Hz) and the
  physical acoustic limit of the speaker transducer (120 Hz). Zero hiss or hum.
- Non-zero digital energy: Keeps the speaker's internal DSP audio detection active
  so the 15-minute idle countdown never triggers.
- Seamless mixing: PipeWire mixes announcements and music on top with zero delay.
- Dynamic sink discovery: Detects active BlueZ Bluetooth sinks automatically.
"""

import logging
import math
import os
import signal
import struct
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("audio-keepalive")

SAMPLERATE = 44100
FREQ = 5.0
AMPLITUDE = 500  # Approx -36 dBFS
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")

running = True


def handle_signal(sig, frame):
    global running
    logger.info("Shutdown signal received, stopping keep-alive daemon...")
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def generate_pcm_buffer():
    """Generates 1 second of 5 Hz stereo 16-bit PCM infrasound audio."""
    frames = bytearray()
    for i in range(SAMPLERATE):
        val = int(AMPLITUDE * math.sin(2 * math.pi * FREQ * i / SAMPLERATE))
        frames.extend(struct.pack("<hh", val, val))
    return bytes(frames)


def find_bluetooth_sink():
    """Finds the active BlueZ Bluetooth output sink."""
    try:
        env = dict(os.environ, XDG_RUNTIME_DIR=RUNTIME_DIR)
        res = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        if res.returncode == 0:
            for line in res.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2 and "bluez_output" in parts[1]:
                    return parts[1]
    except Exception as e:
        logger.debug(f"Error querying sinks: {e}")
    return None


def main():
    logger.info("Starting Smart Speaker Inaudible Audio Keep-Alive Service...")
    buffer = generate_pcm_buffer()
    logger.info(f"Generated 5 Hz infrasound keep-alive buffer ({len(buffer)} bytes, 44.1 kHz, stereo).")

    while running:
        sink = find_bluetooth_sink()
        if not sink:
            logger.debug("No active Bluetooth audio sink found. Waiting...")
            time.sleep(3)
            continue

        logger.info(f"Connecting inaudible keep-alive stream to sink: {sink}")
        cmd = [
            "pw-play",
            "--target", sink,
            "--rate", str(SAMPLERATE),
            "--channels", "2",
            "--format", "s16",
            "--volume", "0.05",
            "--media-role", "keepalive",
            "-",
        ]

        proc = None
        try:
            env = dict(os.environ, XDG_RUNTIME_DIR=RUNTIME_DIR, PATH="/usr/bin:/bin")
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )

            # Write continuous audio stream
            while running and proc.poll() is None:
                proc.stdin.write(buffer)
                proc.stdin.flush()

        except (BrokenPipeError, OSError) as e:
            logger.info(f"Keep-alive stream disconnected ({e}). Retrying...")
        except Exception as e:
            logger.warning(f"Unexpected keep-alive stream error: {e}")
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

        if running:
            time.sleep(2)

    logger.info("Audio keep-alive daemon stopped.")


if __name__ == "__main__":
    main()
