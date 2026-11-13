# HomePi Homeserver Memory

Persistent reference of everything configured, migrated, automated, and verified on this Raspberry Pi (`aarch64`, `192.168.1.150`, user `jake`).

---

## 1. System & Architecture Overview

- **Host:** Raspberry Pi (`aarch64`), IP `192.168.1.150`, user `jake` (`PUID=1000`, `PGID=1000`), timezone `Asia/Kolkata`.
- **Workspace:** `/home/jake/HomeServer` (root Compose project `homeserver` in [compose.yml](compose.yml) using `include:`).
- **Environment & Secrets:** Stored in `/home/jake/HomeServer/.env` (gitignored; template in [.env.example](.env.example)).
  - Variables: `TZ`, `PUID`, `PGID`, `GLANCE_SECRET_TOKEN`, `JELLYFIN_MEDIA_DIR`, `WATCHTOWER_SCHEDULE`, `UPDATER_PORT`, `UPDATER_TOKEN`, `JELLYFIN_URL`, `JELLYFIN_API_KEY`.
- **Media Library & NAS Storage Path:** Stored on external 500 GB USB HDD mounted at `/mnt/hdd` (`/mnt/hdd/jellyfin/media` for Jellyfin). Samba shares `[HomePiStorage]` (`/mnt/hdd`), `[JellyfinMedia]` (`/mnt/hdd/jellyfin/media`), and `[PiShare]` (`/home/jake`) in `/etc/samba/smb.conf`.
- **Pre-Migration Backups Preserved Untouched:**
  - Host folders: `/home/jake/docker`, `/home/jake/npm`, `/home/jake/glance`, `/home/jake/jellyfin/{config,cache}`, `/home/jake/projects/iot-dashboard` (~290 MB total).
  - Docker named volume: `portainer_data` (`263.9 kB`).

---

## 2. Docker Compose Stack Reorganization

All containers were consolidated from ad-hoc CLI/legacy runs into 4 modular Compose stacks under `stacks/` with persistent bind mounts under `/home/jake/HomeServer/data/`:

| Stack File | Service | Image | Network / Ports | Persistent Data / Notes |
| :--- | :--- | :--- | :--- | :--- |
| [stacks/core/compose.yml](stacks/core/compose.yml) | `pihole` | `pihole/pihole:latest` | `host` (`:53` DNS, `:8089` web) | `data/pihole/{etc-pihole,etc-dnsmasq.d}`; Pi-hole v6 `pihole.toml` keeps `*.homepi.local` DNS & editable UI password |
| [stacks/core/compose.yml](stacks/core/compose.yml) | `npm` | `jc21/nginx-proxy-manager:latest` | `homeserver_default` (`80`, `81`, `443`) | `data/npm/{data,letsencrypt}`; proxies `homepi.local` subdomains |
| [stacks/core/compose.yml](stacks/core/compose.yml) | `portainer` | `portainer/portainer-ce:latest` | `homeserver_default` (`9000`, `9443`) | `data/portainer` (migrated from `portainer_data` volume) + `docker.sock` |
| [stacks/core/compose.yml](stacks/core/compose.yml) | `watchtower` | `nickfedor/watchtower:latest` | `homeserver_default` | Chosen over unmaintained `containrrr/watchtower` for Docker Engine `28.5.1` API compatibility; daily `04:00 AM` (`WATCHTOWER_SCHEDULE`), cleanup enabled (`WATCHTOWER_ROLLING_RESTART=false` because Watchtower rejects rolling restarts when `homeassistant` uses `depends_on: mosquitto`) |
| [stacks/media/compose.yml](stacks/media/compose.yml) | `jellyfin` | `jellyfin/jellyfin:latest` | `host` (`:8096`) | `data/jellyfin/{config,cache}`, `/mnt/hdd/jellyfin/media:/media` (`${JELLYFIN_MEDIA_DIR}`), `/dev/dri` |
| [stacks/monitoring/compose.yml](stacks/monitoring/compose.yml) | `glance` | `glanceapp/glance:latest` | `host` (`:8080`) | `data/glance/{config,assets}`, `docker.sock:ro` |
| [stacks/monitoring/compose.yml](stacks/monitoring/compose.yml) | `glances` | `nicolargo/glances:latest-full` | `61208`, `61209` | Uses `command: ["glances", "-w", "-B", "0.0.0.0"]` (without `-w`, image crashes trying to open curses TUI) |
| [stacks/iot/compose.yml](stacks/iot/compose.yml) | `mosquitto` | `eclipse-mosquitto:latest` | `iot_net` (`1883`) | `data/mosquitto/{config,data,log}` (`1883:1883` ownership, replacing old anonymous volume) |
| [stacks/iot/compose.yml](stacks/iot/compose.yml) | `flask_app` | `iot-dashboard-flask_app:latest` | `iot_net` (`5000`) | Built from `stacks/iot/flask_app`; excluded from Watchtower (`com.centurylinklabs.watchtower.enable: "false"`); reads `home/dht11/{temperature,humidity}` via MQTT |
| [stacks/iot/compose.yml](stacks/iot/compose.yml) | `homeassistant` | `ghcr.io/home-assistant/home-assistant:stable` | `host` (`:8123`) | `data/homeassistant:/config`, `/run/dbus:/run/dbus:ro`, `cap_add: [NET_ADMIN, NET_RAW]` |

---

## 3. Container Update Automation & Glance One-Click Button

- **Update Script ([scripts/update.sh](scripts/update.sh)):**
  - Validates Compose syntax (`docker compose config --quiet`).
  - Accepts optional service arguments (`./scripts/update.sh homeassistant`) or updates all stacks with no arguments.
  - Runs `docker compose pull --ignore-buildable`, `docker compose build --pull` (rebuilds `flask_app`), `docker compose up -d --remove-orphans`, `docker image prune -f`, and `docker builder prune -f --filter "until=24h"`.
  - Prints container status, `homeserver-updater` / `homeserver-media-sorter` active state, and `df -h /`.
- **Host Updater Backend ([scripts/updater-server.py](scripts/updater-server.py) & [scripts/homeserver-updater.service](scripts/homeserver-updater.service)):**
  - Runs on the host via `systemd` (`User=jake`, `Group=docker`, port `9911`) so recreating the `glance` container during an update does not kill the update script mid-run.
  - Glance `v0.8.4` (`page.js`) loads pages via `innerHTML` and does not execute `<script>` tags inside HTML/extension widgets, so [data/glance/config/basic_start.yml](data/glance/config/basic_start.yml) embeds it cleanly as `type: iframe` (`Container Updates` -> `http://192.168.1.150:9911`).
  - `POST /api/update` requires `X-Update-Token` matching `UPDATER_TOKEN` in `.env`.
  - Live log viewer available at `http://192.168.1.150:9911/logs` (persisted in `logs/update.log` & `logs/update-status.json`).

---

## 4. Jellyfin Media Auto-Sorter

- **Service:** `homeserver-media-sorter.service` ([scripts/homeserver-media-sorter.service](scripts/homeserver-media-sorter.service)) running [scripts/media-sorter.py](scripts/media-sorter.py) inside `/home/jake/HomeServer/.venv` (`guessit==4.4.0`, `subliminal==2.8.0`, `pysubs2`, `pyinotify==0.9.6`, `requests==2.34.2`). Includes `RequiresMountsFor=/mnt/hdd` so the service waits for the HDD on boot.
- **Drop Folder:** Paste loose videos, video + `.srt` pairs, whole release folders, or standalone `.srt` files directly into the root of `/mnt/hdd/jellyfin/media` (`\\192.168.1.150\JellyfinMedia`).
- **Protected Folders (Never Modified):** `Movies`, `Shows`, `Music`, `MusicVideos`, `Books`.
- **Pipeline:**
  1. Ignores partial transfer extensions (`.part`, `.tmp`, `.crdownload`, `.!qb`, etc.) and waits until size/mtime remain unchanged for 10 seconds, then validates with `ffprobe`.
  2. Parses filename/folder metadata via `guessit` + keyless IMDb (movies) or TVMaze (episodes) and moves to `Movies/<Title> (<Year>)/<Title> (<Year>).<ext>` or `Shows/<Show> (<Year>)/Season <SS>/<Show> - S<SS>E<EE> - <Episode Title>.<ext>`.
  3. Pairs/converts pasted English subtitles to `.en.srt`, or downloads `.en.srt` via Subliminal (`opensubtitles`, `bsplayer`; `tvsubtitles`/`podnapisi` excluded due to 403/DNS failures). Embedded `ffmpeg` subtitle extraction was intentionally removed because Jellyfin plays embedded subtitles natively without the extra Pi disk I/O and CPU.
  4. Removes processed release folders containing only samples/junk/extra subs, and triggers `POST /Library/Refresh` on Jellyfin (`JELLYFIN_URL` + `JELLYFIN_API_KEY` in `.env`).
- **Logs:** `/home/jake/HomeServer/logs/media-sorter.log`.

---

## 5. Home Assistant Setup Notes

- **Version & Mode:** `homeassistant` `2026.11.1` (`ghcr.io/home-assistant/home-assistant:stable`), running in `network_mode: host` on port `8123` with `NET_ADMIN` + `NET_RAW` capabilities and `/run/dbus:/run/dbus:ro` for Bluetooth (`habluetooth`).
- **Reverse Proxy Gotcha (HA 2026.11):** In Home Assistant `2026.11`, HTTP settings moved to storage (`data/homeassistant/.storage/http`) and ignore `configuration.yaml` `http:` blocks once `yaml_migration_done: true`. `use_x_forwarded_for: true` and `trusted_proxies` (`127.0.0.1/32`, `::1/128`, `192.168.1.0/24`, `172.16.0.0/12`) are configured directly in `data/homeassistant/.storage/http`, and `configuration.yaml` is kept clean (no `yaml_still_present_after_migration` repair warnings).
- **Glance:** Added `Home Assistant` link (`http://192.168.1.150:8123`, `sh:home-assistant`) to [data/glance/config/basic_start.yml](data/glance/config/basic_start.yml).
- **Mosquitto MQTT:** Available to HA at `127.0.0.1:1883` (broker credentials in [stacks/iot/flask_app/app.py](stacks/iot/flask_app/app.py); sensor topics `home/dht11/temperature` and `home/dht11/humidity`).
- **MQTT Integration & Discovery:** Native MQTT integration configured in Home Assistant (`127.0.0.1:1883`, user `homepi`, MQTT 5/3.1.1, discovery prefix `homeassistant`). Discovers ESP32 climate node with 7 registered entities (`sensor.room_temperature`, `sensor.room_humidity`, `binary_sensor.esp32_dht11_online`, `sensor.esp32_dht11_wifi_signal`, `sensor.esp32_dht11_uptime`, `sensor.esp32_dht11_error_count`, `binary_sensor.dht11_sensor_health`).

---

## 6. Disk Cleanup & `.dockerignore`

- **Cleanups Performed:**
  - `scripts/update.sh` pruned `2.359 GB` of replaced images during the full stack update + `29.91 MB` during `flask_app` rebuild.
  - Pruned `75.11 MB` Docker build cache, 2 empty (`0 B`) anonymous Docker volumes, `5.6 GB` APT `.deb` archives (`sudo apt-get clean`), `1.7 GB` archived systemd journals (`sudo journalctl --vacuum-size=150M`), and `730 MB` pip cache (`/home/jake/.cache/pip`).
  - Preserved browser caches (`~/.cache/{chromium,BraveSoftware,mozilla}`, ~1.7 GB) and pre-migration backups.
- **Docker Ignore Files:**
  - Root [.dockerignore](.dockerignore) (excludes `.env`, `data/`, `logs/`, `.venv/`, `scripts/`, `*.md`, `*.bk`, caches).
  - Build context [stacks/iot/flask_app/.dockerignore](stacks/iot/flask_app/.dockerignore) (excludes `html.bk`, `__pycache__/`, `.env`, docs, logs from `flask_app` builds).

---

## 7. Storage, External HDD Integration & NAS Setup

- **Hardware & Device Details:**
  - Drive: Seagate 2.5" 500 GB 5400 RPM laptop HDD (`ST500LT012-1DG142`, serial `<DRIVE_SERIAL>`).
  - Interface: USB 3.0 (`5000M`, `uas` / ASMedia SATA bridge).
  - Health (`smartctl`): Overall health `PASSED`, `0` reallocated / pending / offline sectors, temperature ~37–38°C, ~7,971 power-on hours.
- **Partitioning & Filesystem:**
  - Wiped 4 legacy Windows partitions (`sda1`–`sda4`, ~256 GB backed up to PC).
  - Created single 4K-aligned GPT partition (`/dev/sda1`, ~465.8 GB).
  - Formatted as `ext4` with label `HomePi Storage` (`UUID=<STORAGE_UUID>`).
  - Retained default `5%` reserved block headroom (~23 GB safety/anti-fragmentation buffer; ~435 GB usable).
- **Persistent Mount Configuration:**
  - Mountpoint: `/mnt/hdd` (owned by `jake:jake` / `1000:1000`).
  - `/etc/fstab` entry:
    ```text
    UUID=<STORAGE_UUID>  /mnt/hdd  ext4  defaults,noatime,nofail,x-systemd.device-timeout=10s  0  2
    ```
  - Options explanation: `noatime` disables access-time write overhead for faster streaming; `nofail` ensures boot succeeds if HDD is detached; `x-systemd.device-timeout=10s` bounds startup delay.
- **Longevity & APM Tuning:**
  - Default laptop APM level (`128`) aggressively parked heads every few seconds (accumulating `141,495` historical load cycles).
  - Tuned APM to `254` (maximum performance / minimal head parking).
  - Persisted across boots/replugs via udev rule `/etc/udev/rules.d/69-homepi-hdd-apm.rules`:
    ```text
    ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]", ENV{ID_SERIAL_SHORT}=="<DRIVE_SERIAL>", RUN+="/usr/sbin/smartctl -s apm,254 /dev/%k"
    ```
- **Performance Benchmark:**
  - Sustained flushed write (`fdatasync`): **65.6 MB/s**.
  - Direct read (`iflag=direct`): **58.7 MB/s**.
  - `rsync` migration transfer: **85.31 MB/s**.
- **Services & Share Integration:**
  - **Samba Shares (`/etc/samba/smb.conf`):**
    - `[HomePiStorage]`: Points to `/mnt/hdd` (`browseable = yes`, `read only = no`, `guest ok = yes`) for whole-drive LAN NAS storage.
    - `[JellyfinMedia]`: Points to `/mnt/hdd/jellyfin/media` (`browseable = yes`, `read only = no`, `guest ok = yes`).
    - `[PiShare]`: Points to `/home/jake` (existing share).
  - **Jellyfin:** Points `/media` to `/mnt/hdd/jellyfin/media` via `JELLYFIN_MEDIA_DIR=/mnt/hdd/jellyfin/media` in `.env`.
  - **Auto-Sorter:** Watches `/mnt/hdd/jellyfin/media` with systemd unit dependency `RequiresMountsFor=/mnt/hdd`.
  - **Root Disk Space Freed:** Old `/home/jake/jellyfin` media directory deleted from SD card; rootfs (`/`) usage dropped from 83% (9.9 GB free) to 67% (~19 GB free).

---

## 8. Quick Troubleshooting & Operations

```bash
cd /home/jake/HomeServer

# Check all containers & systemd helpers
docker compose ps
sudo systemctl status homeserver-updater homeserver-media-sorter homeserver-bluetooth-reconnect homeserver-vlc homeserver-mpris-proxy homeserver-bluetooth-media-bridge

# Check JBL Clip 3 Bluetooth & audio status
bluetoothctl info <SPEAKER_BT_MAC>
wpctl status
pactl get-default-sink

# Trigger test announcements via Home Assistant
curl -s -X POST -H "Authorization: Bearer ${HA_TOKEN}" \
  http://127.0.0.1:8123/api/services/script/smart_speaker_test_announcements

# Check HDD mount, storage, and APM
df -h /mnt/hdd
sudo smartctl -g apm /dev/sda
sudo smartctl -A /dev/sda | grep -E 'Load_Cycle_Count|UDMA_CRC_Error_Count|Temperature_Celsius'

# Run manual update (full stack or single service)
./scripts/update.sh
./scripts/update.sh homeassistant

# Check IoT Stack & Flask App
curl http://127.0.0.1:5000/sensor
curl http://127.0.0.1:5000/health
docker compose logs -f flask_app mosquitto

# Tail logs
tail -f logs/update.log
tail -f logs/media-sorter.log
docker compose logs -f homeassistant
```

---

## 9. IoT Telemetry, Flask Dashboard & ESP32 Firmware

- **Flask App ([stacks/iot/flask_app/](stacks/iot/flask_app/)):**
  - Refactored with thread-safe `SensorState`, `.env` integration via `python-dotenv`, resilient InfluxDB client handling, and PEP 257 docstrings.
  - Endpoints: `GET /sensor` (JSON telemetry for Glance/HA), `GET /` (dark-themed card), `GET /health`.
  - Subscribes to `home/dht11/temperature` and `home/dht11/humidity`.
- **ESP32 Firmware ([stacks/iot/firmware/](stacks/iot/firmware/)):**
  - **Backup:** [DHT11_Sensor_v1_backup.ino](stacks/iot/firmware/DHT11_Sensor_v1_backup.ino).
  - **Production Firmware:** [DHT11_Sensor/DHT11_Sensor.ino](stacks/iot/firmware/DHT11_Sensor/DHT11_Sensor.ino) and [DHT11_Sensor/config.h](stacks/iot/firmware/DHT11_Sensor/config.h).
  - Non-blocking Wi-Fi and MQTT reconnection with exponential backoff (no watchdog reboots).
  - MQTT Last Will and Testament on `home/dht11/status` (`offline`, QoS 1, retained).
  - Automatic Home Assistant MQTT Discovery on `homeassistant/#` (7 entities grouped under `ESP32 DHT11 Sensor Node`).
  - Strict preservation of raw numeric payloads on `home/dht11/temperature` and `home/dht11/humidity` (un-retained).
  - Additional diagnostics: `home/dht11/rssi`, `home/dht11/uptime`, `home/dht11/ip`, `home/dht11/sensor_status`, `home/dht11/error_count`.

---

## 10. Smart Speaker Architecture (JBL Clip 3, Piper TTS & Home Assistant)

### 10.1 Audio Subsystem & Bluetooth Integration
- **Host Audio Server:** PipeWire `1.2.7` + WirePlumber `0.4.13` + PipeWire-Pulse running under systemd user session `user@1000` (`jake`).
- **User Linger:** `loginctl enable-linger jake` ensures user audio daemons run automatically on headless boot without GUI login.
- **SBC Profile & Latency Tuning (Resolving Stuttering & Distortion):**
  - **SBC vs SBC-XQ Analysis:** On Raspberry Pi 5, the onboard Bluetooth controller shares an internal UART and antenna with 2.4 GHz Wi-Fi. SBC-XQ's ~512 kbps bitrate exceeds the link bandwidth and causes over-the-air packet drops, audible as grainy crackling, stuttering, and distortion. Standard SBC (`a2dp-sink`, ~328 kbps bitpool 53) provides clean, robust audio transmission with `ERR: 0` in `pw-top`.
  - **Bluetooth LE Continuous Scanning Conflict Resolved:** Home Assistant's default `bluetooth` integration was continuously issuing D-Bus `StartDiscovery` (`Discovering: yes` in `bluetoothctl show`). Because the Pi 5 combo radio has only one RF synthesizer, scanning across BLE advertisement channels constantly interrupted A2DP audio transmission, starving the speaker buffer. Disabling the unused `bluetooth` config entry in HA stopped background discovery completely (`Discovering: no`), eliminating radio hopping stalls.
  - **VLC Telnet Digital Hard-Clipping Fix (`custom_components/vlc_telnet`):** Upstream Home Assistant `vlc_telnet` hardcodes `MAX_VOLUME = 500`. In VLC telnet, volume 256 is 100% (0 dBFS bit-perfect pass-through); values from 257 to 500 apply aggressive software pre-amp gain. At volume 0.8, HA was feeding `volume 400` (+11.63 dB) into VLC, brutalizing audio peaks into square waves and producing extreme grainy distortion. Implemented custom component override in `data/homeassistant/custom_components/vlc_telnet/` setting `MAX_VOLUME = 256`, guaranteeing clean 0..1 linear mapping with zero digital clipping.
  - **Wi-Fi Power Management Disabled:** Disabled power management on `wlan0` (`iwconfig wlan0 power off` & NM powersave 2) to eliminate 2.4 GHz co-channel sleep/wake packet stalls on the shared Broadcom radio.
  - **WirePlumber Configuration (`/home/jake/.config/wireplumber/bluetooth.lua.d/51-bluez-custom.lua`):** Forces `a2dp-sink`, `bluez5.enable-sbc-xq = false`, `bluez5.default.rate = 44100`, `session.suspend-timeout-seconds = 0` (preventing connection sleep dropouts), and `resample.quality = 4`.
  - **PipeWire Rate & Quantum Tuning (`~/.config/pipewire/pipewire.conf.d/10-rates.conf`):** Configures `default.clock.rate = 44100`, `allowed-rates = [ 44100 48000 ]`, `default.clock.quantum = 2048`, and `min-quantum = 1024`. This guarantees generous 46 ms buffer headroom and exact integer-ratio upsampling for Piper TTS (22050 Hz * 2 = 44100 Hz).
- **Bluetooth Auto-Reconnect Daemon:**
  - Script: [scripts/bluetooth-reconnect.py](scripts/bluetooth-reconnect.py).
  - Systemd unit: `homeserver-bluetooth-reconnect.service` ([scripts/homeserver-bluetooth-reconnect.service](scripts/homeserver-bluetooth-reconnect.service)).
  - Checks BlueZ adapter status, auto-reconnects JBL Clip 3 (`<SPEAKER_BT_MAC>`) on boot and whenever disconnected, locks profile to standard `a2dp-sink`, sets default sink via `pactl set-default-sink bluez_output.<BT_MAC_UNDERSCORES>.1`, and refreshes the VLC audio daemon upon reconnection.
- **VLC Telnet Audio Daemon:**
  - Systemd unit: `homeserver-vlc.service` ([scripts/homeserver-vlc.service](scripts/homeserver-vlc.service)).
  - Runs `/usr/bin/cvlc -I telnet --telnet-port 4212 --telnet-password ${VLC_TELNET_PASSWORD} --aout pulse --network-caching=2000 --file-caching=2000 --live-caching=2000 --disc-caching=2000 --sout-mux-caching=2000 --audio-resampler=soxr --no-drop-late-frames --no-skip-frames` under user `jake` with `PULSE_SERVER=unix:/run/user/1000/pulse/native`.
  - 2000 ms pre-buffering and SoX resampler completely eliminate HTTP streaming underruns, popping, and clock-drift jitter.

### 10.2 Microphone Input Investigation & Technical Assessment
- **Bluetooth Profiles Tested:**
  - `a2dp-sink` (Active default): High-fidelity 48 kHz stereo playback (SBC/SBC-XQ codec). No microphone source exposed by BlueZ/PipeWire (`sinks: 1, sources: 0`).
  - `headset-head-unit` (HSP/HFP): Tested switching profile via `pactl set-card-profile bluez_card.<BT_MAC_UNDERSCORES> headset-head-unit`. Successfully exposed microphone source `bluez_input.<BT_MAC_UNDERSCORES>.0` and recorded audio with `pw-record` (verified RMS energy ~68.5).
- **Core Limitation:** Under Bluetooth Classic specifications, bidirectional audio forces the profile to 8 kHz mono CVSD (or 16 kHz mSBC) telephone quality. In HSP/HFP mode, audio output is severely distorted, tinny, and low-bandwidth. Furthermore, dynamically negotiating between A2DP and HSP/HFP introduces 1–3 second audio dropouts.
- **Recommendation for Voice Assistant Phase:** Maintain JBL Clip 3 dedicated to `a2dp-sink` for crystal-clear TTS announcements, music, and alarms. For the upcoming voice assistant phase (speech recognition / wake-word detection), connect a dedicated lightweight USB microphone (e.g. Anker PowerConf, Jabra Speak, or a mini USB boundary microphone) directly to the Raspberry Pi 5 USB ports.

### 10.3 Piper Local TTS Integration
- **Container:** `wyoming-piper` ([stacks/iot/compose.yml](stacks/iot/compose.yml)) using image `rhasspy/wyoming-piper:latest`.
- **Voice Models:** Studio-grade `en_US-lessac-high` (109 MB local ONNX model) and `en_GB-alan-medium` (61 MB local ONNX model) stored in `/home/jake/HomeServer/data/wyoming-piper/`. Low-bitrate 16 kHz `-low` catalog models were purged to ensure crystal-clear synthesis.
- **Home Assistant Loopback Routing:** Configured `internal_url: "http://127.0.0.1:8123"` in [data/homeassistant/configuration.yaml](data/homeassistant/configuration.yaml) so that VLC streams TTS audio directly over local loopback with zero network jitter or Wi-Fi latency.

### 10.4 Home Assistant Scripts & Alarms
- **Reusable Announcement Script ([data/homeassistant/scripts.yaml](data/homeassistant/scripts.yaml)):**
  - `script.smart_speaker_announce`: Dynamic message announcement with volume override and Piper voice parameters.
  - `script.smart_speaker_wake_up_briefing`: Personalized morning briefing announcing time, room temperature (reading DHT11 MQTT sensor `sensor.room_temperature`), and custom alarm message.
  - `script.smart_speaker_reminder`: Dynamic or stored reminder announcement.
  - `script.smart_speaker_test_announcements`: Automated test sequence for the 4 verified phrases.
- **Helpers ([data/homeassistant/configuration.yaml](data/homeassistant/configuration.yaml)):**
  - `input_boolean.smart_speaker_weekday_alarm_enabled` (toggle)
  - `input_boolean.smart_speaker_weekend_alarm_enabled` (toggle)
  - `input_boolean.smart_speaker_onetime_alarm_enabled` (toggle, auto-disables upon firing)
  - `input_datetime.smart_speaker_weekday_alarm_time` (default `07:00:00`)
  - `input_datetime.smart_speaker_weekend_alarm_time` (default `08:30:00`)
  - `input_datetime.smart_speaker_onetime_alarm_time` (date & time)
  - `input_text.smart_speaker_alarm_message` (custom text)
  - `input_text.smart_speaker_reminder_message` (custom reminder text)
  - `input_number.smart_speaker_volume` (volume slider 0.1–1.0, default 0.8)
- **Automations ([data/homeassistant/automations.yaml](data/homeassistant/automations.yaml)):**
  - `smart_speaker_weekday_alarm`: Monday–Friday trigger at weekday alarm time.
  - `smart_speaker_weekend_alarm`: Saturday–Sunday trigger at weekend alarm time.
  - `smart_speaker_onetime_alarm`: Exact date/time trigger, announces alert, then toggles boolean off.

### 10.5 Hardware Media Controls & Bidirectional Volume Synchronization
- **BlueZ MPRIS Proxy (`homeserver-mpris-proxy.service`):**
  - Unit: [scripts/homeserver-mpris-proxy.service](scripts/homeserver-mpris-proxy.service) running `/usr/bin/mpris-proxy -e` attached to user session bus `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`.
  - Bridges AVRCP Pass-Through commands (Play, Pause, Next track, Previous track) from the JBL Clip 3 directly into VLC's MPRIS interface (`org.mpris.MediaPlayer2.vlc`).
- **Media Controls & Volume Bridge (`homeserver-bluetooth-media-bridge.service`):**
  - Daemon: [scripts/bluetooth-media-bridge.py](scripts/bluetooth-media-bridge.py) & unit [scripts/homeserver-bluetooth-media-bridge.service](scripts/homeserver-bluetooth-media-bridge.service).
  - **Speaker Buttons -> Home Assistant Volume:** Monitors BlueZ D-Bus `PropertiesChanged` on `org.bluez.MediaTransport1` for property `Volume` (0..127). When the user presses `+` or `-` on the JBL Clip 3, it converts the level (`vol / 127.0`) and calls `media_player.volume_set` in Home Assistant, so the Home Assistant UI slider moves in real time.
  - **Home Assistant Slider -> Physical Speaker Volume:** Monitors PulseAudio sink-input volume changes from Home Assistant and syncs them to PipeWire sink `bluez_output.<BT_MAC_UNDERSCORES>.1`, sending AVRCP Absolute Volume commands to the speaker to adjust the physical amplifier.
  - **Hardware Key Listener:** Monitors `/dev/input/event*` for `JBL Clip 3 (AVRCP)` events (`KEY_PLAYPAUSE`, `KEY_NEXTSONG`, `KEY_PREVIOUSSONG`) and dispatches Home Assistant media player service calls.
