# HomePi Homeserver & Docker Stacks — Server Guide

A fully modular, production-ready Docker Compose environment and host automation suite running on a Raspberry Pi (`aarch64`, Debian/Linux).

This repository contains the Compose orchestration, stack configurations, host systemd helper services, and maintenance scripts for the **HomePi** server.

---

## 1. System Overview & Architecture

- **Host Hardware:** Raspberry Pi (`aarch64`)
- **Host IP:** `192.168.1.150`
- **Default User:** `jake` (`PUID=1000`, `PGID=1000`)
- **Timezone:** `Asia/Kolkata`
- **Root Project Directory:** `/home/jake/HomeServer`
- **Storage Strategy:**
  - **Fast System Storage (MicroSD / SSD):** OS, Docker binaries, Compose projects, application config bind-mounts (`data/`).
  - **High-Capacity Media & NAS Storage (External USB 3.0 HDD):** Formatted as `ext4`, permanently mounted at `/mnt/hdd` (Samba shares: `[HomePiStorage]`, `[JellyfinMedia]`).
- **Orchestration:** Docker Compose v2 utilizing the root [`compose.yml`](compose.yml) `include:` directive to consolidate 4 isolated stack definitions.

---

## 2. Directory Structure

```text
/home/jake/HomeServer/
├── compose.yml                              # Root Compose project (`homeserver`) including all stacks
├── .dockerignore                            # Build & Docker context exclusion rules
├── .env                                     # Environment variables & secrets (gitignored)
├── .env.example                             # Template environment file
├── .gitignore                               # Git exclusions (secrets, persistent data, logs)
├── memory.md                                # Persistent architectural reference & migration history
├── README.md                                # This server guide
├── scripts/                                 # Host automation scripts & systemd units
│   ├── update.sh                            # Validates, pulls, rebuilds, updates & prunes containers
│   ├── updater-server.py                    # Lightweight HTTP API (:9911) for Glance 1-click updates
│   ├── homeserver-updater.service           # systemd unit for updater-server.py
│   ├── media-sorter.py                      # Inotify-based Jellyfin media sorter & subtitle downloader
│   └── homeserver-media-sorter.service      # systemd unit for media-sorter.py
├── stacks/                                  # Modular Compose stack definitions
│   ├── core/
│   │   └── compose.yml                      # pihole, npm (Nginx Proxy Manager), portainer, watchtower
│   ├── media/
│   │   └── compose.yml                      # jellyfin (hardware accelerated, host networking)
│   ├── monitoring/
│   │   └── compose.yml                      # glance (dashboard), glances (system telemetry)
│   └── iot/
│       ├── compose.yml                      # mosquitto (MQTT), flask_app, homeassistant
│       └── flask_app/                       # Custom Flask IoT sensor dashboard + Dockerfile
├── data/                                    # Persistent bind-mounted configs & state (gitignored)
│   ├── pihole/                              # etc-pihole & etc-dnsmasq.d configs and FTL database
│   ├── npm/                                 # SQLite DB, Nginx vhost configs, Let's Encrypt certs
│   ├── portainer/                           # Portainer server DB and certs
│   ├── jellyfin/                            # config & cache (media is mounted from /mnt/hdd)
│   ├── glance/                              # glance.yml, multi-page layouts, user.css & icons
│   ├── mosquitto/                           # mosquitto.conf, passwd, and persistent broker DB
│   └── homeassistant/                       # Home Assistant core config, .storage, automations
├── logs/                                    # Host automation execution logs (gitignored)
│   ├── update.log                           # Container update script output
│   ├── update-status.json                   # Real-time state of running updates
│   └── media-sorter.log                     # Media sorter and subtitle fetcher logs
└── .venv/                                   # Python virtualenv for host automation daemons (gitignored)
```

---

## 3. Services & Port Map

| Service | Stack | Direct LAN Access | Local Domain (Pi-hole + NPM) | Purpose / Description |
| :--- | :--- | :--- | :--- | :--- |
| **Glance Dashboard** | `monitoring` | `http://192.168.1.150:8080` | `http://homepi.local` | Central homepage, service links, system telemetry, 1-click updater |
| **Glances Monitor** | `monitoring` | `http://192.168.1.150:61208` | `http://monitor.homepi.local` | Real-time Pi resource usage (CPU, RAM, Disks, SMART) |
| **Jellyfin** | `media` | `http://192.168.1.150:8096` | `http://jellyfin.homepi.local` | Media streaming server with `/dev/dri` hardware acceleration |
| **Home Assistant** | `iot` | `http://192.168.1.150:8123` | `http://ha.homepi.local` | Home automation platform with Bluetooth and host discovery |
| **IoT Sensor App** | `iot` | `http://192.168.1.150:5000` | `http://sensor.homepi.local` | Custom lightweight Flask dashboard for DHT11 sensor telemetry |
| **Mosquitto MQTT** | `iot` | `192.168.1.150:1883` | `mosquitto:1883` (internal) | MQTT broker for IoT telemetry and sensor topics |
| **Pi-hole Admin** | `core` | `http://192.168.1.150:8089/admin` | `http://pihole.homepi.local/admin` | Network-wide DNS sinkhole, adblocker, and local DNS router |
| **Nginx Proxy Mgr** | `core` | `http://192.168.1.150:81` | `http://nginx.homepi.local` | Reverse proxy manager with automatic SSL / local domain routing |
| **Portainer CE** | `core` | `http://192.168.1.150:9000` | `http://portainer.homepi.local` | Visual Docker container and volume management GUI |
| **Watchtower** | `core` | *Background daemon* | — | Automatic overnight image updater (runs daily at 04:00 AM) |
| **Updater API** | `systemd` | `http://192.168.1.150:9911` | Embedded iframe in Glance | Host HTTP daemon triggering `./scripts/update.sh` |
| **Media Sorter** | `systemd` | *Filesystem inotify* | Samba Drop Folder | Automated movie/show sorter & Subliminal subtitle downloader |

---

## 4. Initial Setup & Deployment Guide

### 4.1 Prerequisites

On the Raspberry Pi host, ensure Docker Engine and Compose v2 are installed:

```bash
# Verify Docker and Compose
docker --version
docker compose version

# Ensure the current user belongs to the docker group
sudo usermod -aG docker $USER
```

### 4.2 Clone & Configure Environment

```bash
cd /home/jake
git clone <REPO_URL> HomeServer
cd /home/jake/HomeServer

# Create your .env from the provided example template
cp .env.example .env
nano .env
```

Set the required variables in `.env`:
- `TZ`: Your local timezone (e.g. `Asia/Kolkata`).
- `PUID` / `PGID`: User and group IDs (`id -u` and `id -g`, typically `1000`).
- `GLANCE_SECRET_TOKEN`: Authentication token for Glance widgets.
- `JELLYFIN_MEDIA_DIR`: Absolute path to your media directory (`/mnt/hdd/jellyfin/media`).
- `WATCHTOWER_SCHEDULE`: Cron expression for Watchtower (`0 0 4 * * *` for 4 AM).
- `UPDATER_PORT`: Port for the host updater service (default `9911`).
- `UPDATER_TOKEN`: Secret token matching the Glance update button authentication.
- `JELLYFIN_URL` & `JELLYFIN_API_KEY`: API credentials for auto-refreshing Jellyfin libraries.

### 4.3 Start All Containers

```bash
# Start all 4 stacks in detached mode
docker compose up -d

# Verify all containers are running
docker compose ps
```

### 4.4 Set Up Host Helper Services (systemd)

Two lightweight background Python daemons run directly on the host using systemd:

1. **Create Python virtual environment and install dependencies:**
   ```bash
   cd /home/jake/HomeServer
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install guessit==4.4.0 subliminal==2.8.0 pysubs2 pyinotify==0.9.6 requests==2.34.2
   deactivate
   ```

2. **Enable and start the systemd services:**
   ```bash
   # Copy systemd unit files to system folder
   sudo cp scripts/homeserver-updater.service /etc/systemd/system/
   sudo cp scripts/homeserver-media-sorter.service /etc/systemd/system/

   # Reload systemd, enable on boot, and start
   sudo systemctl daemon-reload
   sudo systemctl enable --now homeserver-updater.service
   sudo systemctl enable --now homeserver-media-sorter.service

   # Check status
   sudo systemctl status homeserver-updater.service
   sudo systemctl status homeserver-media-sorter.service
   ```

---

## 5. Storage, External HDD & NAS Setup

### 5.1 External HDD Details & Mount Configuration

The server utilizes an external 2.5" USB 3.0 HDD dedicated to bulk storage and Jellyfin media.

- **Filesystem:** `ext4` on partition `/dev/sda1` (Label: `HomePi Storage`, UUID: `e7c08f2a-004a-44a6-a456-2605eb3ecdac`).
- **Mount Point:** `/mnt/hdd` (owned by `jake:jake`).
- **Permanent `/etc/fstab` Mount Entry:**
  ```text
  UUID=e7c08f2a-004a-44a6-a456-2605eb3ecdac  /mnt/hdd  ext4  defaults,noatime,nofail,x-systemd.device-timeout=10s  0  2
  ```
  - `noatime`: Eliminates access-time write overhead, significantly speeding up reads.
  - `nofail`: Ensures the Raspberry Pi boots uninterrupted even if the external drive is detached.
  - `x-systemd.device-timeout=10s`: Limits mount wait time during boot.

### 5.2 HDD Longevity & APM Tuning (Preventing Head-Parking Wear)

Laptop 2.5" HDDs default to an aggressive APM (Advanced Power Management) level of `128`, parking read/write heads every few seconds and drastically degrading drive lifespan.

We tune APM to `254` (maximum performance, heads remain active during usage) and persist this setting across reboots and USB replugs via `/etc/udev/rules.d/69-homepi-hdd-apm.rules`:

```udev
ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]", ENV{ID_SERIAL_SHORT}=="S3PTF6E0", RUN+="/usr/sbin/smartctl -s apm,254 /dev/%k"
```

Verify drive health and APM state anytime:
```bash
sudo smartctl -g apm /dev/sda
sudo smartctl -A /dev/sda | grep -E 'Load_Cycle_Count|UDMA_CRC_Error_Count|Temperature_Celsius'
```

### 5.3 Samba (SMB) Network Shares

Samba (`/etc/samba/smb.conf`) exposes three shares across the local network:

```ini
[HomePiStorage]
   path = /mnt/hdd
   browseable = yes
   read only = no
   guest ok = yes
   create mask = 0775
   directory mask = 0775

[JellyfinMedia]
   path = /mnt/hdd/jellyfin/media
   browseable = yes
   read only = no
   guest ok = yes
   create mask = 0775
   directory mask = 0775

[PiShare]
   path = /home/jake
   browseable = yes
   read only = no
   guest ok = yes
```

#### How to Connect:
- **Windows:** Open File Explorer and enter `\\192.168.1.150\JellyfinMedia` or `\\192.168.1.150\HomePiStorage`.
- **macOS:** In Finder, press `Cmd + K` and enter `smb://192.168.1.150`.
- **Linux:** Mount using `mount -t cifs //192.168.1.150/JellyfinMedia /mnt/share -o guest,uid=1000`.

---

## 6. Media Automation & Jellyfin Auto-Sorter

The host daemon `homeserver-media-sorter.service` continuously monitors the root of `/mnt/hdd/jellyfin/media` for incoming files.

### Workflow:
1. **Drop Folder:** Drag and drop video files, release folders, or standalone `.srt` files directly into `\\192.168.1.150\JellyfinMedia`.
2. **Transfer Detection:** The sorter monitors writes with `pyinotify`. It waits until file size and mtime are stable for 10 seconds, then verifies integrity via `ffprobe`.
3. **Identification & Renaming:**
   - Detects Movies vs. TV Series via `guessit`.
   - Queries keyless IMDb (movies) or TVMaze (shows) for canonical titles, episode names, and release years.
   - Moves organized files to:
     - **Movies:** `Movies/<Title> (<Year>)/<Title> (<Year>).<ext>`
     - **TV Shows:** `Shows/<Show> (<Year>)/Season <SS>/<Show> - S<SS>E<EE> - <Episode Title>.<ext>`
4. **Subtitles:**
   - Automatically pairs pasted subtitles (converts `.ass`, `.vtt`, `.ssa` to UTF-8 `.en.srt`).
   - If no local subtitle exists, fetches English subtitles automatically from OpenSubtitles and BSPlayer via `subliminal`.
5. **Jellyfin Library Refresh:** Automatically sends an authenticated `POST /Library/Refresh` API call to Jellyfin to immediately index new media.
6. **Protected Folders:** Existing library roots (`Movies`, `Shows`, `Music`, `MusicVideos`, `Books`) are strictly read-only to the sorter and never modified.

```bash
# Monitor sorter activity in real-time
tail -f /home/jake/HomeServer/logs/media-sorter.log
```

---

## 7. DNS, Routing & Reverse Proxy (Pi-hole + NPM)

Access services using clean, memorable local domain names without memorizing port numbers.

### Step 1: Pi-hole Local DNS
In the Pi-hole Admin console (`http://192.168.1.150:8089/admin`):
- Navigate to **Local DNS** -> **DNS Records**.
- Map `homepi.local` and `*.homepi.local` to `192.168.1.150`.

### Step 2: Nginx Proxy Manager Proxy Hosts
In Nginx Proxy Manager (`http://192.168.1.150:81`):
Create Proxy Hosts forwarding each domain to the appropriate container/host port:

| Domain Name | Forward Hostname / IP | Forward Port | Options to Enable |
| :--- | :--- | :--- | :--- |
| `homepi.local` | `192.168.1.150` | `8080` | Block Common Exploits, WebSockets |
| `monitor.homepi.local` | `192.168.1.150` | `61208` | Block Common Exploits |
| `jellyfin.homepi.local` | `192.168.1.150` | `8096` | Block Common Exploits, WebSockets |
| `ha.homepi.local` | `192.168.1.150` | `8123` | **WebSockets Support (Required)** |
| `sensor.homepi.local` | `192.168.1.150` | `5000` | Block Common Exploits |
| `pihole.homepi.local` | `192.168.1.150` | `8089` | Block Common Exploits |
| `nginx.homepi.local` | `192.168.1.150` | `81` | Block Common Exploits |
| `portainer.homepi.local`| `192.168.1.150` | `9000` | Block Common Exploits, WebSockets |

---

## 8. Home Assistant & IoT Configuration

### 8.1 Network & Capabilities
Home Assistant runs in `network_mode: host` to facilitate mDNS, SSDP, and local device discovery. It is granted `NET_ADMIN` and `NET_RAW` Linux capabilities and mounts `/run/dbus:/run/dbus:ro` for native Bluetooth discovery.

### 8.2 Reverse Proxy Configuration (Home Assistant 2026.11+)
In modern Home Assistant releases, HTTP reverse-proxy configuration is managed via storage (`data/homeassistant/.storage/http`). `use_x_forwarded_for: true` and `trusted_proxies` (`127.0.0.1/32`, `::1/128`, `192.168.1.0/24`, `172.16.0.0/12`) are pre-configured to avoid proxy 400 Bad Request errors.

### 8.3 Mosquitto MQTT & IoT Dashboard
- **MQTT Broker:** Available at `127.0.0.1:1883` on the host, or `mosquitto:1883` on the `iot_net` Docker bridge.
- **Sensor App:** Custom Flask dashboard reading DHT11 telemetry on MQTT topics `home/dht11/temperature` and `home/dht11/humidity`.
- **USB Zigbee/Z-Wave Dongles:** To pass through a USB dongle, add the serial path under `homeassistant` in [`stacks/iot/compose.yml`](stacks/iot/compose.yml):
  ```yaml
  devices:
    - /dev/serial/by-id/usb-Silicon_Labs_CP2102...:/dev/ttyUSB0
  ```
  Then recreate the container: `docker compose up -d homeassistant`.

---

## 9. Container Updates & Maintenance

### 9.1 Automatic Overnight Updates (Watchtower)
Watchtower (`nickfedor/watchtower:latest`) automatically checks upstream registries for all image updates according to `WATCHTOWER_SCHEDULE` (default: 04:00 AM daily). Old dangling images are cleaned automatically.
*(Note: `flask_app` is excluded from Watchtower because it is built from local source).*

### 9.2 Manual Command-Line Updates (`scripts/update.sh`)
Run manual updates anytime from `/home/jake/HomeServer`:

```bash
# Update all stacks, rebuild local images, and prune old cache
./scripts/update.sh

# Or update only a specific container (e.g. jellyfin or homeassistant)
./scripts/update.sh jellyfin
```

`update.sh` performs the following steps:
1. Validates Compose configuration syntax (`docker compose config --quiet`).
2. Pulls updated container images from registries.
3. Rebuilds locally built images (`flask_app`) with `--pull`.
4. Restarts and reconciles containers (`docker compose up -d --remove-orphans`).
5. Prunes dangling images (`docker image prune -f`) and Docker build cache older than 24h.
6. Displays service statuses, systemd unit health, and root filesystem disk headroom.

### 9.3 1-Click Update from Glance Dashboard
The Glance homepage features an **Update Stack** widget powered by `homeserver-updater.service` (`scripts/updater-server.py` on port `9911`):
- Clicking the refresh button dispatches an authenticated `POST /api/update` request.
- The server runs `scripts/update.sh` asynchronously in the background.
- Live progress and historical logs can be inspected anytime at `http://192.168.1.150:9911/logs`.

---

## 10. Operations & Troubleshooting Runbook

### Everyday Docker Commands

```bash
cd /home/jake/HomeServer

# Check status of all containers
docker compose ps

# View unified logs or logs for a specific service
docker compose logs -f
docker compose logs -f homeassistant
docker compose logs -f glance

# Recreate a specific container after configuration changes
docker compose up -d --force-recreate glance

# Restart a single container
docker compose restart jellyfin

# Stop all containers
docker compose down
```

### Checking Host Automations

```bash
# Check status of updater and media-sorter services
sudo systemctl status homeserver-updater homeserver-media-sorter

# Restart services after script changes
sudo systemctl restart homeserver-updater
sudo systemctl restart homeserver-media-sorter

# Tail service logs
tail -f logs/update.log
tail -f logs/media-sorter.log
```

### Checking Storage & Disk Health

```bash
# View disk usage across SD card and external HDD
df -h / /mnt/hdd

# Check HDD SMART health and temperatures
sudo smartctl -H /dev/sda
sudo smartctl -A /dev/sda

# Check Samba server status
sudo systemctl status smbd
```

### Common Gotchas & Fixes

1. **Permission Denied on Bind Mounts:**
   If a container cannot write to its `data/` directory, ensure ownership matches `1000:1000`:
   ```bash
   sudo chown -R 1000:1000 data/<service_name>
   ```
2. **Mosquitto Permission Issues:**
   Mosquitto runs as internal user `1883:1883`. Its directory must be owned accordingly:
   ```bash
   sudo chown -R 1883:1883 data/mosquitto
   ```
3. **Glances Exits Immediately:**
   Glances in Docker requires `-w` to serve the web interface: `command: ["glances", "-w", "-B", "0.0.0.0"]`.
4. **Jellyfin Hardware Acceleration:**
   Ensure the video device `/dev/dri` exists and is passed into Jellyfin in `stacks/media/compose.yml`.
