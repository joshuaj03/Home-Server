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
  - Drive: Seagate 2.5" 500 GB 5400 RPM laptop HDD (`ST500LT012-1DG142`, serial `S3PTF6E0`).
  - Interface: USB 3.0 (`5000M`, `uas` / ASMedia `174c:55aa` SATA bridge).
  - Health (`smartctl`): Overall health `PASSED`, `0` reallocated / pending / offline sectors, temperature ~37–38°C, ~7,971 power-on hours.
- **Partitioning & Filesystem:**
  - Wiped 4 legacy Windows partitions (`sda1`–`sda4`, ~256 GB backed up to PC).
  - Created single 4K-aligned GPT partition (`/dev/sda1`, ~465.8 GB).
  - Formatted as `ext4` with label `HomePi Storage` (`UUID=e7c08f2a-004a-44a6-a456-2605eb3ecdac`).
  - Retained default `5%` reserved block headroom (~23 GB safety/anti-fragmentation buffer; ~435 GB usable).
- **Persistent Mount Configuration:**
  - Mountpoint: `/mnt/hdd` (owned by `jake:jake` / `1000:1000`).
  - `/etc/fstab` entry:
    ```text
    UUID=e7c08f2a-004a-44a6-a456-2605eb3ecdac  /mnt/hdd  ext4  defaults,noatime,nofail,x-systemd.device-timeout=10s  0  2
    ```
  - Options explanation: `noatime` disables access-time write overhead for faster streaming; `nofail` ensures boot succeeds if HDD is detached; `x-systemd.device-timeout=10s` bounds startup delay.
- **Longevity & APM Tuning:**
  - Default laptop APM level (`128`) aggressively parked heads every few seconds (accumulating `141,495` historical load cycles).
  - Tuned APM to `254` (maximum performance / minimal head parking).
  - Persisted across boots/replugs via udev rule `/etc/udev/rules.d/69-homepi-hdd-apm.rules`:
    ```text
    ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]", ENV{ID_SERIAL_SHORT}=="S3PTF6E0", RUN+="/usr/sbin/smartctl -s apm,254 /dev/%k"
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
sudo systemctl status homeserver-updater homeserver-media-sorter

# Check HDD mount, storage, and APM
df -h /mnt/hdd
sudo smartctl -g apm /dev/sda
sudo smartctl -A /dev/sda | grep -E 'Load_Cycle_Count|UDMA_CRC_Error_Count|Temperature_Celsius'

# Run manual update (full stack or single service)
./scripts/update.sh
./scripts/update.sh homeassistant

# Tail logs
tail -f logs/update.log
tail -f logs/media-sorter.log
docker compose logs -f homeassistant
```
