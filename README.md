# arr_stack

Self-hosted media stack running on one Docker host: Usenet downloads behind a WireGuard VPN, automatic movie/show management with Sonarr and Radarr, requests through Seerr, playback through Jellyfin with Intel Quick Sync, automatic HEVC compression with Tdarr, and public access through a Cloudflare Tunnel.

This repository holds the Docker Compose files, the environment template and the custom scripts. It is a backup and a rebuild guide. Every value in `servarr/.env` written as `<LIKE_THIS>` **must be filled in before use.** All references to **Getter** is a place holder. Only import media you personally own and have permission to ingest. 

---

## Contents

```
arr_stack/
├── README.md
├── servarr/
│   ├── compose.yaml                       # main stack (VPN, downloaders, *arr apps, Seerr, Tdarr, tunnel)
│   ├── .env                               # environment template (sanitized)
│   ├── slack.env                          # Slack webhook template (not read by Compose)
│   └── socket-proxy/
│       └── haproxy.cfg.template           # strict Docker API allow-list for deunhealth
├── jellyfin/
│   └── compose.yaml                       # Jellyfin (separate stack)
├── config/                                # app settings exported from the running stack (no secrets)
│   ├── sonarr/                            # custom formats, quality profile, quality sizes, other settings
│   ├── radarr/                            # same for Radarr
│   ├── tdarr/
│   │   ├── flows/                         # importable flows: cpu_hevc_shows.json, movies_english_subtitles.json
│   │   └── libraries.json                 # library settings (paths, filters, flow, watching)
│   └── jellyfin/
│       ├── encoding.xml                   # Quick Sync transcoding settings
│       └── network.xml                    # proxy / local subnet settings (<LAN_SUBNET> placeholder)
├── TV_SideLoad/
│   ├── README.md                          # Uses Tizen to sideload Jellyfin app onto Samsung TV 
│   └── Jellyfin-10.10.z.wgt               # signed application **will need to sign your own**
└── scripts/
    ├── crontab.txt                        # cron lines for every scheduled script
    ├── <getter>/
    │   ├── FixExtension.py                # <getter> post-processing: fixes obfuscated files with no extension
    │   └── EpisodeOrder.py                # <getter> queue script: downloads a season's episodes in order
    ├── sonarr/
    │   ├── sonarr_throttled_search.py     # cron: searches missing episodes in small batches, max 2 searches at a time
    │   └── sonarr_watchdog.py             # cron: detects a stalled Sonarr, cancels queued searches, then restarts it
    ├── slack/
    │   └── slack_reports.py               # cron: Slack health alerts, daily digest, usage stats, weekly downloads
    └── tdarr/
        ├── hevc_loadtest.py               # measures remote-viewer capacity (library mix, real playback, GPU test)
        └── tdarr_worker_window.py         # cron: Tdarr worker limits by time of day (backlog / normal 01:00-06:30)
```

---

## Architecture

```
                        Internet
                           │
              ┌────────────┴─────────────┐
              │ Cloudflare Tunnel        │  media.<domain> → Jellyfin
              │ (cloudflared)            │  requests.<domain> → Seerr
              └────────────┬─────────────┘
                           │
 Seerr ──► Radarr (movies) / Sonarr (shows) ──► Prowlarr (indexers) ─┐
                                                                     │  all inside the
                                                   <getter> ◄────────┘  gluetun VPN namespace
                                                      │
                                          /scratch (working files, local NVMe)
                                                      │
                   import (move) ◄──── /data/downloads/<getter>/completed
                           │
             /data/movies , /data/shows  (network share)
                           │
              ┌────────────┼──────────────┐
           Jellyfin      Tdarr          Bazarr
          (playback)  (HEVC transcode)  (subtitles)
```

| Container | Image | Port (bound to host IP) | Network | Purpose |
| --- | --- | --- | --- | --- |
| `gluetun` | `qmcgaw/gluetun` | 6789 (<getter>), 9696 (Prowlarr) | `servarrnetwork` | WireGuard VPN client; <getter> and Prowlarr share its network |
| `<getter>` | `lscr.io/linuxserver/<getter>` | via gluetun | `service:gluetun` |  |
| `prowlarr` | `lscr.io/linuxserver/prowlarr` | via gluetun | `service:gluetun` | Indexer manager, syncs indexers to Sonarr/Radarr |
| `sonarr` | `lscr.io/linuxserver/sonarr` | 8989 | `servarrnetwork` | TV shows and anime |
| `radarr` | `lscr.io/linuxserver/radarr` | 7878 | `servarrnetwork` | Movies |
| `bazarr` | `lscr.io/linuxserver/bazarr` | 6767 | `servarrnetwork` | Subtitles |
| `seerr` | `ghcr.io/seerr-team/seerr` | 5055 | `servarrnetwork` | Request front end |
| `tdarr` | `ghcr.io/haveagitgat/tdarr` | 8265 | `servarrnetwork` | Library transcoding to HEVC |
| `cloudflared` | `cloudflare/cloudflared` | none | `servarrnetwork` | Public access without opening ports |
| `docker-socket-proxy` | `tecnativa/docker-socket-proxy:v0.5.0` | none | `socketproxy` (internal) | Only container that touches `docker.sock` (read-only) |
| `deunhealth` | `qmcgaw/deunhealth` | none | `socketproxy` (internal) | Restarts unhealthy <getter>/Prowlarr |
| `jellyfin` | `lscr.io/linuxserver/jellyfin` | 8096, 7359/udp | `jellyfin_default` | Media server with Intel Quick Sync |

---

## Step-by-step deployment

The files use placeholders written as `<LIKE_THIS>`. List every file that still has one, then replace them:

```bash
grep -rlE '<[A-Z_]+>' --exclude=README.md .
grep -rl '<VM_IP>' . | xargs sed -i 's/<VM_IP>/192.168.1.50/g'     # your Docker host's LAN IP
```

| Placeholder | Where | Value |
| --- | --- | --- |
| `<VM_IP>` | both compose files, all scripts | LAN IP of the Docker host |
| `<JELLYFIN_PUBLIC_HOST>`, `<SEERR_PUBLIC_HOST>` | `scripts/slack/slack_reports.py` | public hostnames of the tunnel (optional) |
| `<LAN_SUBNET>` | `config/jellyfin/network.xml` | your LAN, e.g. `192.168.1.0/24` |
| `<Getter_USERNAME>`, `<Getter_PASSWORD>` | `config/sonarr/settings.json`, `config/radarr/settings.json` | <getter> control login |
| everything else in `servarr/.env` and `servarr/slack.env` | see step 5 and the Slack section | secrets |

### 1. Host

Tested on an Ubuntu 26.04 LTS KVM virtual machine on Proxmox. Any Linux host with Docker works.

Recommended resources:

| Resource | Value | Why |
| --- | --- | --- |
| CPU | 12 cores, CPU type `host` | Exposes AVX2 to Tdarr/ffmpeg |
| RAM | 9 GB, no ballooning | Required when a GPU is passed through |
| Disk 1 | 64 GB | OS, Docker, app configs |
| Disk 2 | 200 GB, fast (NVMe) | `/scratch` for <getter> working files |
| GPU | Intel iGPU passed through | Jellyfin Quick Sync (`/dev/dri`) |
| NIC | `virtio` with multiqueue (`queues=6`) | Download throughput |

### 2. Install Docker and base settings

```bash
# Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER        # log out and back in afterwards

# Docker needs IP forwarding; pin it so other packages cannot turn it off
echo 'net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-docker-ip-forward.conf
sudo sysctl --system

# Firewall: SSH and Jellyfin only. Docker bypasses UFW, which is why every
# published port in the compose files is bound to the host IP instead.
sudo ufw allow 22/tcp
sudo ufw allow 8096/tcp
sudo ufw allow 7359/udp
sudo ufw enable
```

### 3. Storage

Two paths must exist on the host before starting the stack.

**`/scratch`: <getter> working files on a local fast disk**

```bash
sudo mkfs.ext4 /dev/sdb1                 # second disk; check the device name first with lsblk
sudo mkdir -p /scratch
echo '/dev/sdb1 /scratch ext4 defaults,noatime 0 2' | sudo tee -a /etc/fstab
sudo mount /scratch
sudo mkdir -p /scratch/<getter>
sudo chown -R 1000:1000 /scratch
```

**`/data`: the media library (network share)**

Every container sees the library at the same path, `/data`. That keeps file paths identical between <getter>, Sonarr, Radarr, Tdarr and Jellyfin, so imports are instant moves and no remote path mappings are needed.

```bash
sudo apt install cifs-utils
sudo mkdir -p /data

# Credentials file, readable by root only
sudo tee /etc/samba/credentials >/dev/null <<'EOF'
username=<SHARE_USER>
password=<SHARE_PASSWORD>
EOF
sudo chmod 600 /etc/samba/credentials

# Mount on first access (systemd automount), owned by UID/GID 1000
echo '//<NAS_IP>/data /data cifs credentials=/etc/samba/credentials,vers=3.1.1,uid=1000,gid=1000,file_mode=0775,dir_mode=0775,_netdev,x-systemd.automount 0 0' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload
ls /data
```

Folder layout on the share:

```
/data
├── downloads/getter/completed     # finished downloads, before import
├── downloads/getter/scripts       # <getter> extension scripts (FixExtension.py)
├── movies                         # Radarr root folder
└── shows                          # Sonarr root folder
```

```bash
mkdir -p /data/downloads/getter/{completed,scripts} /data/movies /data/shows
```

Tdarr also needs a transcode cache directory on the big scratch disk (not the system disk; a remux needs a full copy of the file): `sudo mkdir -p /scratch/tdarr && sudo chown 1000:1000 /scratch/tdarr`.

### 4. Get the files in place

```bash
sudo mkdir -p /docker
sudo chown $USER:$USER /docker
git clone <this-repo> /tmp/arr_stack
cp -r /tmp/arr_stack/servarr  /docker/servarr
cp -r /tmp/arr_stack/jellyfin /docker/jellyfin
chmod 600 /docker/servarr/.env
```

### 5. Fill in `servarr/.env`

| Variable | Value |
| --- | --- |
| `TZ` | `America/New_York` (Eastern, follows daylight saving). Keep it the same as the hard-coded `TZ` of `tdarr` and Jellyfin and `LOCAL_TZ` in `slack_reports.py` |
| `PUID` / `PGID` | `1000` / `1000` (the user that owns `/data` and `/scratch`) |
| `VPN_SERVICE_PROVIDER` / `VPN_TYPE` | `airvpn` / `wireguard` (any [gluetun provider](https://github.com/qdm12/gluetun-wiki) works) |
| `FIREWALL_VPN_INPUT_PORTS` | The port forwarded to you by the VPN provider |
| `WIREGUARD_PUBLIC_KEY`, `WIREGUARD_PRIVATE_KEY`, `WIREGUARD_PRESHARED_KEY`, `WIREGUARD_ADDRESSES` | From the WireGuard config generated in your VPN provider's client area |
| `SERVER_NAMES` | Comma-separated VPN servers. Pick ones close to your Usenet provider; ping time matters more than country |
| `SET_IP_*` | Static IPs on `servarrnetwork` (`172.39.0.0/24`); defaults are fine |
| `CF_TUNNEL_TOKEN` | Token from Cloudflare Zero Trust → Networks → Tunnels → your tunnel |
| `TDARR_API_KEY` | Any long random string, e.g. `openssl rand -hex 18` |
| `JELLYSTAT_*` | Only if Jellystat is enabled |

> **Note:** every static IP in `compose.yaml` comes from a `SET_IP_*` variable in `.env`. A misspelled variable name does not stop the container from starting: it silently gets a random IP instead. Check with `docker network inspect servarrnetwork` after the first start. (Seerr had this problem until 2026-10-01, when `${SET_IP_SEEN}` was corrected to `${SET_IP_SEERR}`.)

Also change the host IP in the `ports:` lines of both compose files. Every container runs on Eastern time (`America/New_York`): most take it from `TZ` in `.env`, while the `tdarr` service and `jellyfin/compose.yaml` set it directly. For another zone, change all three plus `LOCAL_TZ` in `scripts/slack/slack_reports.py`. The host itself stays on UTC (the cron lines in `scripts/crontab.txt` are written in UTC).

Changing `TZ` later means recreating the containers that use it. gluetun loads `.env`, so recreate it and the containers in its network: `docker compose up -d --no-deps gluetun`, then `docker compose up -d --no-deps --force-recreate <getter> prowlarr`, then `docker compose up -d --no-deps deunhealth sonarr radarr bazarr seerr`, and `docker compose up -d` in `/docker/jellyfin`.

### 6. Start the stacks

```bash
cd /docker/servarr
docker compose up -d
docker compose ps              # gluetun must become "healthy" before getter/prowlarr start
docker logs gluetun | grep -i "public ip"

cd /docker/jellyfin
docker compose up -d
```

After a reboot or any change to the socket proxy:

```bash
docker restart deunhealth
docker logs deunhealth         # should show: Monitoring containers getter and prowlarr
```

**When changing the VPN:** containers that share gluetun's network must be recreated with it, or they exit with `joining network namespace ... No such container`:

```bash
cd /docker/servarr
docker compose up -d gluetun
docker compose up -d --force-recreate getter prowlarr
```

---

## Container configuration

Web UIs are at `http://<HOST_IP>:<port>`. Configure them in this order. Each app's API key is under Settings → General.

### gluetun (VPN)

- All settings come from `.env`.
- The health check pings out every 20s. Gettter and Prowlarr wait for `service_healthy` and restart with gluetun.
- Getter's and Prowlarr's ports are published on **gluetun**, because those containers have no network of their own.
- Test speed after setup. 

### Getter (`:6789`)

Settings → set these, then **Save all changes** and **Reload**:

| Section | Setting | Value |
| --- | --- | --- |
| Paths | `MainDir` | `/data/downloads/getter` |
| Paths | `DestDir` | `/data/downloads/getter/completed` |
| Paths | `InterDir`, `TempDir`, `QueueDir`, `<getter>Dir` | under `/scratch/...` (local NVMe) |
| Paths | `ScriptDir` | `${MainDir}/scripts` |
| News-servers | Primary | Primary provider, Level 0, many connections (e.g. 50), TLS port 563, strict certificate check |
| News-servers | Backup | Backup provider on a **different backbone**, Level 1, 10 connections, TLS port 563, strict certificate check |
| Queue | `DiskSpace` | `250` (MB; pauses when free space drops below this) |
| Queue | `HealthCheck` | `park` |
| Queue | `KeepHistory` | `30` (days) |
| Security | `ControlUsername` / `ControlPassword` | Set your own |
| Queue | `ArticleCache` | `1500` (MB) |
| Queue | `DirectRename` | `no` |
| Unpack | `DirectUnpack` | `no` |
| Check and repair | `ParCheck` / `ParRepair` / `ParRename` / `RarRename` | `auto` / `yes` / `yes` / `yes` |
| Logging | `DetailTarget`, `DebugTarget` | `none` (stops per-article logging) |
| Extension scripts | `Extensions`, `ScriptOrder` | `FixExtension, EpisodeOrder` (script names **without** `.py`; with `.py` getter loads a script but never runs it) |

Install the post-processing script:

```bash
cp scripts/getter/FixExtension.py scripts/getter/EpisodeOrder.py /data/downloads/getter/scripts/
chmod 755 /data/downloads/getter/scripts/FixExtension.py /data/downloads/getter/scripts/EpisodeOrder.py
```

Categories: `Movies` and `TV` (Sonarr and Radarr send  with these; the names are case-sensitive and must match the  client setting in each app). Leave each category's `DestDir` empty and keep `AppendCategoryDir=no`, so every finished download lands directly in `/data/downloads/getter/completed`.

### Prowlarr (`:9696`)

1. Indexers → Add your indexers.
2. Settings → Apps → Add **Sonarr** (`http://172.39.0.13:8989`) and **Radarr** (`http://172.39.0.14:7878`) with their API keys. Prowlarr server URL: `http://172.39.0.12:9696`.
3. Indexer priority: the main indexer `1`, the second one `25` (lower number is preferred). Prowlarr syncs the priority to Sonarr/Radarr.
4. Sync. Indexers then appear in Sonarr/Radarr automatically. **Do not also add the same indexers by hand** in Sonarr/Radarr, because that doubles indexer API usage.

### Sonarr (`:8989`) and Radarr (`:7878`)

Same steps for both:

1. Settings → Media Management:
   - Root folder: `/data/shows` (Sonarr) or `/data/movies` (Radarr).
   - Rename: **off** in Sonarr (release names are kept), **on** in Radarr (`{Movie Title} ({Release Year}) {Quality Full}`, folder `{Movie Title} ({Release Year})`). Sonarr's season folders are `Season {season}`.
   - Propers and repacks: prefer and upgrade. Minimum free space when importing: 100 MB. Extra files: not imported.
   - "Use hardlinks instead of copy" can stay on. It only affects torrents, and usenet imports here are moves.
2. Settings → Download Clients → Add **getter**:
   - Host `gluetun` (getter lives in gluetun's network namespace), port `6789`, getter username/password.
   - Category `TV` (Sonarr) or `Movies` (Radarr).
   - Remove completed and remove failed downloads: on. Completed download handling and "redownload failed": on.
   - No remote path mappings: every container sees `/data` at the same path.
3. Settings → Custom Formats (see `config/<app>/custom_formats.json`):
   - Sonarr: **english**, two required conditions: language English **and** resolution 1080p.
   - Radarr: **standard**, language English (not required, score 0; kept for reference).
4. Settings → Profiles: quality profile **HD - 720p/1080p** (used as the default by Seerr), upgrades off, no 4K.
   - Sonarr: custom format `english` scored `10`, minimum custom format score `10`. In practice Sonarr only grabs **English 1080p** releases, even though 720p qualities are ticked.
   - Radarr: profile language **English**; 720p and 1080p (including Remux-1080p) allowed.
5. Settings → Quality: the size limits per quality differ from the defaults. Copy them from `config/<app>/quality_definitions.json` (MB per minute: min / preferred / max).
6. Settings → Indexers: RSS sync every 15 min (Sonarr) and 30 min (Radarr). Indexers themselves come from Prowlarr.
7. Sonarr only: anime is added with series type **Anime** (Seerr does this automatically for anime titles).

Everything above is also in `config/sonarr/settings.json` and `config/radarr/settings.json` (naming, media management, download handling, indexer options, delay profiles, download client without credentials). Use them as a checklist, or apply them with the API (`PUT /api/v3/config/naming`, `/config/mediamanagement`, `/config/downloadclient`, `/config/indexer`; add the `id` the new instance returns from a `GET` first). Custom formats can be pasted one by one in Settings → Custom Formats → **+** → **Import**. The quality profile JSON lists custom formats by name; a new instance needs their new ids before a `POST /api/v3/qualityprofile`.

### Bazarr (`:6767`)

1. Settings → Sonarr: host `sonarr` (container name on `servarrnetwork`), port `8989`, base URL `/`, API key.
2. Settings → Radarr: host `radarr`, port `7878`, base URL `/`, API key.
3. Settings → Languages: enable English, create a profile **English** (one item: `en`, not hearing-impaired, not forced) and make it the default for series and movies.
4. Settings → Subtitles: keep "Use embedded subtitles" on and tick **Ignore embedded PGS subtitles**. Files whose only English track is PGS (image-based, the type most likely to force a burn-in transcode) then get an external `.srt`. Embedded ASS is still accepted, so anime keeps its styled subtitles.
5. Settings → Providers, all free and without an account: Podnapisi, Gestdown, YIFY Subtitles, TVsubtitles, AnimeTosho. OpenSubtitles.com has the biggest catalogue but needs an account.
6. System → Tasks: run **Sync with Sonarr** and **Sync with Radarr** once, then the two **Search for Missing … Subtitles** tasks. After that Bazarr searches on its own schedule.

The same settings can be applied with the API (`POST /api/system/settings`, form fields such as `settings-general-use_sonarr=true`, `settings-general-enabled_providers=<name>` repeated per provider, `languages-enabled=en`, and `languages-profiles=<JSON list>`). The API key is under `auth.apikey` in `bazarr/config/config.yaml`. Bazarr's port is bound to the host IP, so call `http://<VM_IP>:6767`, not `127.0.0.1`.

### Seerr (`:5055`)

1. Sign in with Jellyfin: `http://<VM_IP>:8096`.
2. Settings → Services → add Radarr (`172.39.0.14:7878`) and Sonarr (`172.39.0.13:8989`), both reached over `servarrnetwork`:
   - Mark both as default servers.
   - Quality profile `HD - 720p/1080p`.
   - Root folders `/data/movies` and `/data/shows`.
   - For Sonarr, set the anime root folder (`/data/shows`) and anime quality profile (`HD - 720p/1080p`) too, anime series type `anime`, season folders on.
   - **Sonarr: untick "Enable Automatic Search"** (`preventSearch: true`). Otherwise every show request starts a full-series search, and long anime series can block Sonarr for hours (see `sonarr_throttled_search.py`). Radarr keeps automatic search on, because a movie search is a single quick query.
3. Settings → General → enable auto-approve for the admin user.
4. "Enable Special Episodes" is off, so whole-series requests skip specials. "Allow partial series requests" is on.
5. Settings → Notifications → Slack: a webhook for a `#media-request` channel (request made, approved, available). This one is separate from the `slack.env` webhooks used by the scripts.
6. Settings → Jellyfin: sync libraries. Titles turn **Available** once Jellyfin has them.
7. Settings → General → copy the API key if another tool needs it (stored as `main.apiKey` in `/docker/servarr/seerr/settings.json`).

### Jellyfin (`:8096`)

1. Libraries:
   - Movies → `/data/movies`.
   - Shows → `/data/shows`.
   - Turn on real-time monitoring so new imports appear within a minute.
2. Dashboard → Playback → Transcoding (stored in `/docker/jellyfin/config/encoding.xml`; the working file is in `config/jellyfin/encoding.xml` and can be copied in while the container is stopped):
   - Hardware acceleration **Intel QuickSync (QSV)**, device `/dev/dri/renderD128`.
   - Hardware decoding for every codec your iGPU lists in `vainfo`. On a 12th-gen (Alder Lake) iGPU: H.264, HEVC, MPEG-2, VC-1, VP8, VP9 and AV1, plus HEVC 10-bit, VP9 10-bit and HEVC RExt 10/12-bit.
   - Keep hardware encoding on. HEVC/AV1 encoding, low-power encoders and tone mapping are left off. This library is 1080p/720p SDR only, and in testing the low-power encoder was slower, not faster.
   - Check the GPU first:
     ```bash
     docker exec jellyfin /usr/lib/jellyfin-ffmpeg/vainfo --display drm --device /dev/dri/renderD128
     ```
   - If you edit `encoding.xml` by hand, stop the container first; Jellyfin rewrites the file while running.
   - Measured on an i7-12650H iGPU (1080p source, 4 Mbps H.264 output): about 5 simultaneous 1080p transcodes or 7 at 720p, using about 1/25 of the CPU time of software x264.
   - Dashboard → Playback → Streaming: **Internet streaming bitrate limit 6 Mbps** (`RemoteClientBitrateLimit` 6000000 in `system.xml`). Also check each user's own limit (Users → user → "Internet streaming bitrate limit"), which overrides the server value when set. 6 Mbps matches the cap on Tdarr's HEVC output, so converted files play remotely without a transcode. Usable upload ÷ 6 Mbps is your maximum remote viewers (about 26 on a 200 Mbps upload).
3. Networking (`/docker/jellyfin/config/network.xml`; template in `config/jellyfin/network.xml`, replace `<LAN_SUBNET>`), so tunnel visitors log with their real IP and LAN devices count as local:
   - `KnownProxies`: `10.99.0.1` (the gateway of `jellyfin_default`, pinned in `jellyfin/compose.yaml`).
   - `LocalNetworkSubnets`: your LAN, e.g. `<LAN_SUBNET>` such as `192.168.1.0/24`, plus `10.99.0.0/24`.
   - Keep the subnet pinned. Docker otherwise picks a free `172.x` range each time the network is recreated. When it moves, every viewer, including LAN devices, shows up as the gateway IP, counts as remote and gets the remote bitrate limit. If `10.99.0.0/24` clashes with your LAN, change it in both files.
4. Port `7359/udp` stays on `0.0.0.0` on purpose: clients find the server with a LAN broadcast. Port `1900/udp` (DLNA) is not published.
5. If something is imported but missing: Dashboard → Scheduled Tasks → **Scan Media Library**.

### Tdarr (`:8265`)

`auth=true` is set, so create a login account on first visit. The API uses `TDARR_API_KEY`.

1. Libraries → add two libraries:

   | Library | Source | Transcode cache |
   | --- | --- | --- |
   | Movies | `/data/movies` | `/temp` |
   | Shows | `/data/shows` | `/temp` |

   For both libraries:
   - Container filter: `mkv,mp4,mov,m4v,mpg,mpeg,avi,flv,webm,wmv,vob,evo,iso,m2ts,ts`.
   - Folder watching on, scan on start on.
   - Output folder: same as the source (replace the original).
   The exact settings are in `config/tdarr/libraries.json`.
2. Flows → create two flows. The quickest way is Flows → **Import** with `config/tdarr/flows/cpu_hevc_shows.json` and `config/tdarr/flows/movies_english_subtitles.json`, then assign each flow to its library (Library → Transcode options → Flows). The flows run three classic community plugins (`Tdarr_Plugin_MC93_Migz4CleanSubs`, `Tdarr_Plugin_bsh1_Boosh_FFMPEG_QSV_HEVC`, `Tdarr_Plugin_00td_action_re_order_all_streams_v2`) plus built-in flow plugins; Tdarr downloads the community plugins on first start. What they do:
   - **Movies: English subtitles only**, assigned to Movies: `Input File → Keep English subtitles → Replace Original File` (subtitle step as below). Movies are not re-encoded: most are high-bitrate releases that would lose visible detail at a 6 Mbps cap.
   - **CPU HEVC**, assigned to Shows (Transcode options → Flows):

   ```
   Input File
     └► Keep English subtitles (classic plugin Migz Clean Subtitle Streams,
        Tdarr_Plugin_MC93_Migz4CleanSubs: language "eng,und", commentary false)
     └► Check Overall Bitrate (> 4 Mbps) ── no ──► Replace Original File
          └► Codec is HEVC? ── yes ──────────────────────────┐
               └ no ► Codec is VP9? ── yes ─────────────────┤
                        └ no ► Codec is AV1? ── yes ────────┤
                                 └ no ► Transcode to H265    │
                                        (Boosh FFMPEG QSV    │
                                         HEVC, preset medium,│
                                         max_average_bitrate │
                                         6000)               │
                                          └─────────────────►┤
                                                             ▼
                                Order MKV Streams (video, audio, subtitle;
                                languages eng, jpn, fre)
                                          └► Replace Original File
   ```

   Output container is MKV.

   The subtitle step removes every embedded subtitle track whose language tag is not `eng` or `und`. Tracks with no language tag are kept. It is a stream copy (`-map 0 -map -0:s:N -c copy`), so video and audio are not re-encoded. Files with nothing to remove skip it.

   The bitrate check decides what gets converted. Anything above 4 Mbps overall (the Jellyfin remote streaming limit) that is not already HEVC, VP9 or AV1 is converted to HEVC with Quick Sync. The Boosh plugin targets half the source video bitrate, capped at 6000 kbps by `max_average_bitrate`. Files at or under 4 Mbps only get the subtitle step. A size check (e.g. "> 3 GB") is a poor fit: most H.264 TV episodes are under 1.5 GB but still well above 4 Mbps.

   To clean files Tdarr already marked "Not required", set them back to Queued with the API: `POST /api/v2/bulk-update-files` with `{"data":{"fileIds":[...],"updatedObj":{"TranscodeDecisionMaker":"Queued"}}}`.

   The QSV HEVC step needs the GPU inside the Tdarr container: `devices: /dev/dri:/dev/dri` plus `group_add` with the host's `render` and `video` group IDs (`getent group render video`; 991 and 44 here). Without it every conversion fails with `Failed to set value 'qsv:hw_any,child_device_type=vaapi' for option 'init_hw_device'`. Check with `docker exec tdarr ls -l /dev/dri`.

   Put the transcode cache (`/temp`) on a disk with more free space than your largest file, multiplied by the number of workers that can run at once. A remux needs a full copy of the file.
3. Schedule: both libraries only start new files **01:00–06:30** every day. Tdarr schedules work in whole hours, so open 01:00–07:00 (Library → Schedule, or the API: `/api/v2/cruddb`, collection `LibrarySettingsJSONDB`, field `schedule`, slots like `Mon:01-02`), and let `scripts/tdarr/tdarr_worker_window.py` set 0 workers from 06:30 to 07:00. Scans and folder watching still run all day.

### Cloudflare Tunnel

In Cloudflare Zero Trust → Networks → Tunnels → your tunnel → Public Hostnames:

| Hostname | Service |
| --- | --- |
| `media.<your-domain>` | `http://<VM_IP>:8096` (Jellyfin) |
| `requests.<your-domain>` | `http://seerr:5055` or `http://<VM_IP>:5055` (Seerr) |

No inbound ports need to be opened on the router.

### Docker socket proxy and deunhealth

- `deunhealth` restarts containers labelled `deunhealth.restart.on.unhealthy=true` (getter, Prowlarr) when their health check fails.
- It does not get the raw Docker socket. It talks to `docker-socket-proxy` on an internal network with no internet access.
- `socket-proxy/haproxy.cfg.template` allows only:
  - read: `_ping`, `version`, `events`, container list and container inspect;
  - write: `POST /containers/<id>/restart`.
- Everything else returns 403.
- The stock `ALLOW_RESTARTS` flag in v0.5.0 does not work unless `POST=1`, and `POST=1` would also allow creating containers. That is why the custom template is used instead.

---

## Scripts

Scheduled scripts and their cron lines are listed in `scripts/crontab.txt`. Config files the scripts read:

| Script | Reads | Writes |
| --- | --- | --- |
| `sonarr_throttled_search.py` | `/docker/servarr/sonarr/config.xml` (API key) | `~/sonarr-throttled-search.log` |
| `sonarr_watchdog.py` | `/docker/servarr/sonarr/config.xml` (API key), `/docker/servarr/slack.env` (webhook) | `~/sonarr-watchdog.log`, `~/sonarr-watchdog-state.json` |
| `hevc_loadtest.py` | `/docker/servarr/.env` (`TDARR_API_KEY`), Jellyfin DB copy | `~/hevc-loadtest/` |
| `tdarr_worker_window.py` | `/docker/servarr/.env` (`TDARR_API_KEY`) | `~/tdarr-worker-window.log`, `~/tdarr-worker-window-state.json`, Tdarr worker limits and schedules |
| `slack_reports.py` | `slack.env` (webhooks), Sonarr/Radarr `config.xml`, `getter.conf`, `seerr/settings.json`, `.env` (`TDARR_API_KEY`), Jellyfin DB copy + ffmpeg logs | `~/slack-reports.log`, `~/slack-reports-state.json`, `~/slack-reports-digest.json` |

### `scripts/getter/FixExtension.py`

getter post-processing script.

Some releases unpack to a random file name with no extension (e.g. `KKC1Lye6iMcBJWMq`). Sonarr and Radarr skip such files, and the download stays stuck at *"No files found are eligible for import"* or *"Manual Import required"*.

The script runs after every successful download and reads the first bytes of each file with no extension. It then adds the matching extension:

| Magic bytes | Extension |
| --- | --- |
| `1A 45 DF A3` (Matroska/EBML) | `.mkv` |
| `ftyp` at offset 4 | `.mp4` |
| `RIFF` | `.avi` |

Unknown files are left alone. The extension always matches the real container, so Tdarr's container filter and ffprobe handle the file normally.

Test it without getter:

```bash
mkdir /tmp/fx && printf '\x1a\x45\xdf\xa3x' > /tmp/fx/abc
getter_DIRECTORY=/tmp/fx thingPP_TOTALSTATUS=SUCCESS python3 FixExtension.py   # exit 93, abc -> abc.mkv
```

### `scripts/getter/EpisodeOrder.py`

Getter queue script (`QUEUE EVENTS: thing_ADDED`) that downloads the episodes of a season in order, E01 before E02 and so on.

**Problem:** Sonarr sends the episodes from a season search in whatever order it ranked the releases, and getter downloads in queue order.

**What it does:** each time an thing is added (including a re-grab after a failed download), the script:
- finds the waiting items that belong to the same show and season;
- swaps them into episode order, using only the queue positions those items already hold. Other shows, and each show's place in the queue, stay the same;
- leaves alone any item that is already downloading or in post-processing, so nothing is interrupted part-way;
- moves only the part of the queue that changes.

It recognises `Show.Name.S01E05...`, `Show Name - S01E05 ...` and anime-style `[Group] Show Name - 05 [...]` names (season 1, absolute numbering).

Preview the result without changing the queue:

```bash
docker exec getter python3 /data/downloads/getter/scripts/EpisodeOrder.py --dry-run
```

### `scripts/sonarr/sonarr_throttled_search.py`

Replaces full-series searches.

**Problem:** Sonarr runs at most 3 commands at once. A full-series search on an anime goes episode by episode, about a minute per episode, so a 70-episode show holds a slot for over an hour. Three of them block everything else:
- download tracking (`RefreshMonitoredDownloads`), so the Activity page looks frozen and finished downloads never import;
- series refreshes, RSS sync and other scheduled jobs.

**How the script avoids it:** each run (every 10 min from cron):
- It counts searches already queued or running. If there are `MAX_SEARCHES` (2) or more, it does nothing. Otherwise it fills the free search slots.
- It picks missing, monitored, aired episodes that are:
  - not already downloading;
  - not covered by a search that is still running;
  - not searched in the last 24 h. Titles with no release are retried once a day rather than in a loop.
- Priority order:
  1. series listed in `PRIORITY`;
  2. series added in the last 48 h, newest request first, so a fresh Seerr request jumps the queue automatically;
  3. never-searched episodes;
  4. everything else.
- **Standard (non-anime) series** with a whole aired season missing get one `SeasonSearch`. That finds season packs in 1 query instead of one per episode.
- Everything else (anime, partial seasons) goes in `EpisodeSearch` batches of up to 10 episodes.
- It logs a `WARNING` if download tracking has been waiting more than 15 min.

At most 2 searches use Sonarr's 3 command slots, leaving one free for imports and refreshes.

Settings at the top of the script: `MAX_SEARCHES`, `BATCH`, `RETRY_HOURS`, `NEW_SERIES_HOURS`, `PRIORITY`. Check what it would send with `python3 sonarr_throttled_search.py --dry-run`.

Install (replace `<VM_IP>` in the script first):

```bash
mkdir -p /docker/servarr/scripts
cp scripts/sonarr/sonarr_throttled_search.py /docker/servarr/scripts/
chmod 700 /docker/servarr/scripts/sonarr_throttled_search.py
( crontab -l 2>/dev/null; echo '*/10 * * * * /usr/bin/python3 /docker/servarr/scripts/sonarr_throttled_search.py >> $HOME/sonarr-throttled-search.log 2>&1' ) | crontab -
```

Avoid the **Search Monitored** / **Search All** buttons on a series page for long shows. They queue the same full-series search this script exists to avoid. Searching one season or a few episodes is fine.

### `scripts/sonarr/sonarr_watchdog.py`

Catches the stalls the throttled searcher doesn't prevent. Runs every 5 min from cron.

Sonarr counts as stalled if any of these is true:
- the API does not answer within 10 s;
- download tracking (`RefreshMonitoredDownloads`) has been queued for more than 15 min;
- 3 or more searches have each been running for more than 30 min;
- an import command has been queued for more than 30 min.

What it does:
1. First check that finds a stall: cancels the **queued** search commands. Sonarr can't cancel a running command. A cancelled search never ran, so the throttled searcher sends it again later.
2. Still stalled at the next check: `docker restart sonarr`, at most 2 times in 6 h, then alert only. It skips checks for 10 min after a restart, and waits if an import is running.
3. Posts to Slack only when the state changes: stalled, restarted, restart limit reached, recovered.

State (restart count, stall start) is kept in `~/sonarr-watchdog-state.json`. Deleting it is safe; the watchdog starts fresh.

**Slack:** the webhook goes in `servarr/slack.env` as `SLACK_WEBHOOK_SONARR_ALERTS`, not in `.env`. gluetun loads `.env` with `env_file`, so a webhook there would end up in gluetun's environment, and editing `.env` makes the next `docker compose up -d` recreate gluetun. While the value is empty or a placeholder, messages go to the log only. After adding the URL, run `python3 sonarr_watchdog.py --test-slack`.

Check what it would do with `--dry-run` (changes nothing, posts nothing).

On-demand status: `python3 sonarr_watchdog.py --status` posts one message to `#sonarr-alerts` now, whether or not anything is wrong. It shows OK or the stall reason, the running commands, the number queued and the watchdog restarts in the last 6 h. It only reads Sonarr and does not change the watchdog state. Example: `:white_check_mark: Sonarr status: OK · 0 running (none) · 0 queued · 0 watchdog restart(s) in the last 6 h`.

Install (replace `<VM_IP>` in the script first):

```bash
cp scripts/sonarr/sonarr_watchdog.py /docker/servarr/scripts/
chmod 700 /docker/servarr/scripts/sonarr_watchdog.py
cp servarr/slack.env /docker/servarr/slack.env && chmod 600 /docker/servarr/slack.env   # then fill in the URL
( crontab -l 2>/dev/null; echo '*/5 * * * * /usr/bin/python3 /docker/servarr/scripts/sonarr_watchdog.py >> $HOME/sonarr-watchdog.log 2>&1' ) | crontab -
```

### `scripts/slack/slack_reports.py`

Posts server updates to Slack. One script, four modes, one webhook per channel. `#media-request` is left alone: Seerr already posts there.

| Mode | Channel | When | Content |
| --- | --- | --- | --- |
| `health` | `#server-health` | every 5 min, only on change | Container stopped or unhealthy, VPN without internet (tested from inside gluetun), Jellyfin or Seerr not answering on the LAN or not reachable from the internet (through the Cloudflare tunnel), `/data` share missing or not answering, disk over 85% on `/`, `/scratch` or `/data`, getter paused or at 0 KB/s for 30 min with items queued, Sonarr/Radarr health warnings, getter/Sonarr/Radarr API not answering, VPN exit city changed. Posted as a card with a red (open problem) or green (resolved) bar and a status board: one green/red tile each for Containers, VPN, Jellyfin, Seerr, Sonarr, Radarr, getter, Storage |
| `digest` | `#server-health` | 08:00 Eastern | Green or red card with the same status board, Tdarr HEVC progress (shows left, change since the last digest, queued, errors) and the list of open problems |
| `usage` | `#usage-stats` | Friday 07:00 Eastern, covering the previous Friday to Thursday | Date range, plays, number of viewers, peak simultaneous streams, approximate direct-play share, failed logins and lockouts, a plays-by-day bar graph (busiest day marked), top 5 shows/movies as a bulleted list |
| `weekly` | `#weekly-downloads` | Sunday 18:00 Eastern | Summary (movies and episodes added, GB added, space left on `/data`, complete/partial/not downloaded counts, failed downloads, episodes still missing, Seerr requests) and a table with one row per movie or show: title, `Movie` or the seasons downloaded, status (Imported / Partial with missing count / Not downloaded / upgrade failed) |

Layout: messages use Slack Block Kit. The weekly table is a Slack `table` block (max 100 rows per message, so a longer week is split over several messages). If Slack rejects the table, the same table is sent as monospace text instead and the log says `sent (fallback layout)`. The bar graph is drawn with block characters in a code block, so no image service is involved.

Public reachability: set the two public URLs in `ACCESS` at the top of the script (`<JELLYFIN_PUBLIC_HOST>`, `<SEERR_PUBLIC_HOST>`). While they are placeholders, only the LAN checks run. Hostnames are never posted; an alert just says "not reachable from the internet (HTTP 502)".

Preview a health alert without a real problem: `python3 slack_reports.py health --simulate jellyfin:public` posts a card labelled TEST ALERT and changes no state. Any problem key works, e.g. `vpn`, `seerr:lan`, `container:sonarr`.

How the health alerts stay quiet:
- A problem must be seen on 2 checks in a row (10 min) before it is posted, which filters out short blips such as an indexer timeout.
- An open problem is repeated at most once an hour, and a "resolved" line is posted when it clears.
- All changes from one check go out as a single message.
- If a post fails, the alert is retried at the next check.
- State is kept in `~/slack-reports-state.json`. Deleting it is safe: open problems are then reported again.

Privacy: messages carry counts and titles only, never usernames, IP addresses, API keys or the tunnel hostname. The VPN exit is reported by city; the IP in gluetun's log is dropped. Usage stats come from a read-only copy of Jellyfin's database and Jellyfin's ffmpeg transcode logs, so no Jellyfin API key is needed. The copy is deleted after each run. Plays come from `ActivityLogs`. Each play is matched to `BaseItems` for its show or movie name, so episodes of one show count together; the ids need their dashes removed to match. A play counts once per viewer and episode, so restarts and seeks don't inflate a title. Direct-play share is an estimate: plays minus titles with a transcode log. Change the period with `USAGE_DAYS`.

Sonarr stalls are not checked here. `sonarr_watchdog.py` handles them and posts to `#sonarr-alerts`.

**Time zone:** the VM runs on UTC and Debian's cron has no `CRON_TZ`. Each daily or weekly job is started at both UTC hours its Eastern time can fall on, and `--local-hour` exits unless it is that hour in `America/New_York`. For example, `0 12,13 * * *` with `--local-hour 8` is the 08:00 digest, and `0 11,12 * * 5` with `--local-hour 7` is the Friday 07:00 usage report. Daylight saving changes need no cron edits. Change `LOCAL_TZ` in the script for another zone.

**Setup:**
1. In Slack, create the channels `#server-health`, `#usage-stats`, `#weekly-downloads` and `#sonarr-alerts` (private). Slack names can't contain spaces.
2. Use one Slack app with **Incoming Webhooks** turned on and no other scopes (api.slack.com/apps → Create New App → From scratch). This setup uses the existing app "Homelab", which also holds the Seerr webhook for `#media-request`.
3. Add one webhook per channel: app → Incoming Webhooks → **Add New Webhook** → pick the channel → Allow. Repeat for each channel.
4. **Paste the URLs by hand.** This step is always manual: copy each URL from the app's Incoming Webhooks page (**Copy** button) and paste it after the matching `=` in `/docker/servarr/slack.env` (`nano /docker/servarr/slack.env`). Use `slack.env`, not `.env`; the scripts only read `slack.env`. Keep the file mode `600`. Automation agents are not allowed to read webhook URLs (they are credentials), so they can't do this step.

   | Channel | Variable in `slack.env` |
   | --- | --- |
   | `#server-health` | `SLACK_WEBHOOK_SERVER_HEALTH` |
   | `#usage-stats` | `SLACK_WEBHOOK_USAGE_STATS` |
   | `#weekly-downloads` | `SLACK_WEBHOOK_WEEKLY_DOWNLOADS` |
   | `#sonarr-alerts` | `SLACK_WEBHOOK_SONARR_ALERTS` |

   Check it without showing the URLs: `grep -c '=https://hooks.slack.com/' /docker/servarr/slack.env` should print `4`.
5. Run `python3 /docker/servarr/scripts/slack_reports.py test` and `python3 /docker/servarr/scripts/sonarr_watchdog.py --test-slack`. Each channel gets one test message, and the log line for each says `sent`. A channel listed as `no … in slack.env` has an empty or mistyped URL.

Webhook URLs are passwords: anyone with one can post to that channel. A webhook belongs to the Slack user who added it, so if that account leaves the workspace, posting stops. To rotate a webhook, remove it under the app's Incoming Webhooks page, add a new one, and update `slack.env`.

Check any mode without posting: `python3 slack_reports.py <mode> --dry-run`. That prints the notification line and the Slack blocks as JSON and changes no state.

Install (replace `<VM_IP>`, `<JELLYFIN_PUBLIC_HOST>` and `<SEERR_PUBLIC_HOST>` in the script first):

```bash
cp scripts/slack/slack_reports.py /docker/servarr/scripts/
chmod 700 /docker/servarr/scripts/slack_reports.py
cp servarr/slack.env /docker/servarr/slack.env && chmod 600 /docker/servarr/slack.env   # then fill in the URLs
crontab -e    # add the slack_reports.py lines from scripts/crontab.txt
```

Limitation: every check runs on this VM. If the VM, its internet connection or the power is down, nothing is posted, and silence looks the same as "all fine". The 08:00 digest is the daily sign of life: if it doesn't arrive, look at the server. Catching a full outage would need an outside heartbeat service.

### `scripts/redownload/retry_status.py`

Prints one status line per title in a request batch:
- whether the title is in Jellyfin (read from a copy of Jellyfin's database);
- its Radarr/Sonarr file counts;
- its queue entries;
- Getter's current speed.

The last line is `ACTIVE=<n>`; `0` means nothing is still downloading. Edit the `MOVIES` and `SHOWS` lists for your own batch.

### `scripts/tdarr/hevc_loadtest.py`

Estimates how many remote viewers Jellyfin can carry, so a change (Quick Sync, HEVC conversion, a different remote bitrate cap) can be measured before and after. It combines:
- **Library mix** from Tdarr's file database: the share of files that need a transcode under a 4 Mbps and a 6 Mbps remote cap.
- **Real playback** over the last 3 days: titles played (Jellyfin activity log, read from a copy of the database) against titles transcoded (Jellyfin's ffmpeg logs).
- **GPU test**: 8 parallel 60-second Quick Sync transcodes with Jellyfin's own ffmpeg, on median-size 1080p files of the library's most common codec, at 1080p and 720p output.
- **Viewers** ≈ min(GPU slots ÷ share needing a transcode, usable upload ÷ remote cap). The upload is set in `UPLOAD_MBPS`.

```bash
cp scripts/tdarr/hevc_loadtest.py /docker/servarr/scripts/
export HOST_IP=<VM_IP>
python3 /docker/servarr/scripts/hevc_loadtest.py --label baseline   # before a change
python3 /docker/servarr/scripts/hevc_loadtest.py --label after      # after it
```

`--when-done` is for cron. It does nothing until Tdarr has no queued files and no non-HEVC file above 4 Mbps is left. Then it writes `~/hevc-loadtest/report-<date>.md`, comparing the result with the latest baseline, and removes its own crontab line. Run it outside the Tdarr window, because a busy Tdarr skews the GPU and disk numbers.

### `scripts/tdarr/tdarr_worker_window.py`

Sets Tdarr's worker limits by time of day. Cron runs it every 5 minutes. All Tdarr workers use Quick Sync, so 4 busy workers slow Jellyfin's playback transcodes to about 0.6× real time, and viewers buffer. It has two modes, stored in `~/tdarr-worker-window-state.json`:

**Backlog mode** (default) is for a large queue, with the library schedule open all hours. While files are queued:
- 06:30–22:00 Eastern: 1 GPU worker, 0 CPU workers;
- other hours: 2 CPU + 2 GPU workers.

When nothing is queued and no worker is busy, it switches to normal mode once:
- it sets the 01:00–07:00 schedule on every library;
- it sets 2 CPU + 3 GPU workers.

**Normal mode** ends the daily window at 06:30. Tdarr schedules work in whole hours, so the script sets 0 workers from 06:30 to 07:00. At other times it keeps 2 CPU + 3 GPU workers.

Lowering a limit does not stop running jobs. They finish their current file first.

Set `T` (Tdarr API URL), the hours and the limits at the top of the script. `--dry-run` prints what it would change. To start a new backlog run, open the schedule to all hours, delete the state file and wait for the next cron run.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `getter`/`prowlarr` exit with `joining network namespace ... No such container` | gluetun was recreated alone. Run `docker compose up -d --force-recreate getter prowlarr`. |
| All container networking breaks after a package uninstall | `net.ipv4.ip_forward` was turned off. Pin it in `/etc/sysctl.d/99-docker-ip-forward.conf` (step 2). |
| `/data` shows `Host is down`, downloads crawl at 1–5 MB/s | The NAS's Samba service ran out of memory. Give it more RAM and set `smbd` to restart automatically. |
| Download stuck at "No files found are eligible for import" | Obfuscated file name with no extension. `FixExtension.py` prevents this. For older downloads, rename the file to `.mkv` and Sonarr/Radarr import it on their own. |
| "Found matching movie via grab history, but release was matched to movie by ID. Manual Import required." | Same cause. Use Radarr → Activity → Manual Import and choose the movie. |
| Title never downloads | No complete release on your Usenet servers. Use Wanted → Search All in Sonarr/Radarr, or add a provider on another backbone. |
| Imported but not in Jellyfin | Dashboard → Scheduled Tasks → Scan Media Library. |
| Sonarr Activity page frozen, finished downloads not importing | All 3 command slots are held by long searches (System → Tasks → Queue shows `RefreshMonitoredDownloads` waiting). `sonarr_watchdog.py` fixes this on its own within about 10 min (see `~/sonarr-watchdog.log`). By hand: `docker restart sonarr`, then let `sonarr_throttled_search.py` do the searching. |
| No Slack messages arrive | Look in `~/slack-reports.log`. `(not set up)` means the channel's URL in `/docker/servarr/slack.env` is empty or not a `hooks.slack.com` URL. `failed (HTTPError)` usually means the webhook was removed or its owner left the workspace: create a new one. Test with `slack_reports.py test`. |

---

## Security notes

- Never commit a filled-in `.env`. It contains the VPN private key, the Cloudflare tunnel token and the Tdarr API key.
- Every published port is bound to the host's LAN IP (except Jellyfin discovery, `7359/udp`). Docker bypasses UFW, so a `0.0.0.0` binding would be reachable from every interface.
- Only `docker-socket-proxy` mounts `docker.sock`, read-only, behind a strict allow-list.
- Use SSH keys only on the host (`PasswordAuthentication no`).
- Images use `:latest` tags. To pin versions, record digests with `docker image ls --digests`.
