# Media Subtitle Watcher

Automatically strips subtitle streams that are muxed into video files, the
moment new media lands in your library no cron interval, no re-scanning
your whole collection, no quality loss (it's a lossless remux, not a
re-encode).

This repo/folder contains two pieces:

| File | What it is |
|---|---|
| `watch-and-strip.sh` | The watcher. Uses `inotify` to detect new/changed video files and processes them in real time. |
| `media-subtitle-watcher.service` | A `systemd` unit that runs `watch-and-strip.sh` as a persistent background service. |

**Dependency:** `watch-and-strip.sh` doesn't strip anything itself it
detects files and hands each one off to a companion script,
[`strip-embedded-subtitles.sh`](./strip-embedded-subtitles.sh), which does
the actual `ffprobe`/`ffmpeg` work. That script must live alongside
`watch-and-strip.sh` (or be pointed to with `--strip-script`). See the
[Companion script](#companion-script-strip-embedded-subtitlessh) section
below for its own flags.

## How it works

```
New file written/moved into your media directory
        │
        ▼
inotifywait sees a close_write or moved_to event
        │
        ▼
watch-and-strip.sh waits for the file size to stop changing
(the "settle" check/avoids grabbing a file mid-download)
        │
        ▼
Calls strip-embedded-subtitles.sh --file <path>
        │
        ├─ ffprobe checks for subtitle streams
        │
        ├─ none found  → left untouched
        │
        └─ found       → ffmpeg remuxes the file without them
                          (-c copy: video/audio copied as-is, no re-encode)
```

Every file is handled independently and serially (one at a time, even if a
season pack drops ten episodes at once) that keeps things simple and
avoids spinning up a pile of concurrent `ffmpeg` processes. Since it's a
stream copy rather than a transcode, each file typically takes a few
seconds regardless.

## Requirements

- Linux with `bash`, `systemd`
- [`inotify-tools`](https://github.com/inotify-tools/inotify-tools) (provides `inotifywait`)
- `ffmpeg` / `ffprobe`
- `strip-embedded-subtitles.sh` from this repo, installed alongside `watch-and-strip.sh`

```bash
sudo apt update
sudo apt install -y inotify-tools ffmpeg
```

## Installation

```bash
# 1. Put both scripts together in one place
sudo mkdir -p /opt/subtitle-strip
sudo cp watch-and-strip.sh strip-embedded-subtitles.sh /opt/subtitle-strip/
sudo chmod +x /opt/subtitle-strip/*.sh

# 2. Create a place for the manifest (incremental-scan state file) to live
sudo mkdir -p /var/lib/subtitle-strip
sudo chown <user>:<group> /var/lib/subtitle-strip   # same user the service will run as
```

Edit `media-subtitle-watcher.service` before installing it:

- `ExecStart=` set `--dir` to your real media root, and adjust
  `--settle-seconds` / `--manifest` / `--remove-sidecars` / `--ext` to taste
  (see [Configuration reference](#configuration-reference) below)
- `User=` / `Group=` set to whichever user already owns your media files
  (commonly the same user Sonarr/Radarr run as)

```bash
# 3. Install and start the service
sudo cp media-subtitle-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now media-subtitle-watcher.service

# 4. Confirm it's running
sudo systemctl status media-subtitle-watcher.service
journalctl -u media-subtitle-watcher.service -f
```

## Configuration reference

### `watch-and-strip.sh` flags

| Flag | Default | Description |
|---|---|---|
| `--dir PATH` | *(required)* | Root media directory to watch, recursively |
| `--ext "mkv mp4 m4v avi mov ts"` | `mkv mp4 m4v avi mov ts` | Space-separated file extensions to react to |
| `--settle-seconds N` | `20` | Seconds a file's size must stay unchanged before it's considered fully written and safe to process |
| `--strip-script PATH` | same directory as this script | Path to `strip-embedded-subtitles.sh` |
| `--manifest PATH` | *(none)* | Record each processed file (path+size+mtime) here, so a periodic `--manifest` safety-net scan (see below) knows it's already been handled |
| `--remove-sidecars` | off | Also delete matching sidecar subtitle files (`.srt`/`.ass`/`.ssa`/`.sub`/`.idx`/`.vtt`) next to a stripped video |
| `-h`, `--help` | | Print built-in usage |

### `media-subtitle-watcher.service` directives worth knowing

| Directive | Purpose |
|---|---|
| `User=` / `Group=` | Runs as this user **must** have read/write access to your media directory |
| `WorkingDirectory=` | Where the scripts live |
| `ExecStart=` | The actual command + flags this is where you configure `--dir`, `--settle-seconds`, etc. |
| `Restart=on-failure` / `RestartSec=10` | Auto-restarts the watcher if it crashes, after a 10s delay |
| `Nice=10` | Lower CPU scheduling priority this shouldn't compete with anything latency-sensitive |
| `IOSchedulingClass=best-effort` / `IOSchedulingPriority=7` | Lower disk I/O priority so remuxing doesn't stall other reads/writes on the box |

Any change to the `.service` file requires `sudo systemctl daemon-reload`
followed by `sudo systemctl restart media-subtitle-watcher.service` to take
effect.

## Common commands

**Service control**

```bash
sudo systemctl status media-subtitle-watcher.service     # is it running?
sudo systemctl start media-subtitle-watcher.service       # start it
sudo systemctl stop media-subtitle-watcher.service        # stop it
sudo systemctl restart media-subtitle-watcher.service      # restart (needed after config changes)
sudo systemctl enable media-subtitle-watcher.service       # start automatically on boot
sudo systemctl disable media-subtitle-watcher.service      # stop starting automatically on boot
```

**Logs**

```bash
journalctl -u media-subtitle-watcher.service -f            # follow live
journalctl -u media-subtitle-watcher.service --since today  # today's log only
journalctl -u media-subtitle-watcher.service -n 200         # last 200 lines
```

**Run it manually in a terminal (without systemd) useful for testing**

```bash
/opt/subtitle-strip/watch-and-strip.sh --dir /path/to/media --settle-seconds 5
# Ctrl+C to stop
```

**Change a setting**

```bash
sudo systemctl edit --full media-subtitle-watcher.service   # opens the unit in $EDITOR
sudo systemctl daemon-reload
sudo systemctl restart media-subtitle-watcher.service
```

**Uninstall**

```bash
sudo systemctl disable --now media-subtitle-watcher.service
sudo rm /etc/systemd/system/media-subtitle-watcher.service
sudo systemctl daemon-reload
```

## Companion script: `strip-embedded-subtitles.sh`

`watch-and-strip.sh` calls this script under the hood with `--file <path>`
for each file it detects. It can also be run on its own either as a
one-off bulk scan of an existing library, or from cron as an incremental
safety net alongside the watcher (recommended: same `--manifest` path as
the watcher, so work isn't duplicated).

| Flag | Description |
|---|---|
| `--dir PATH` | Bulk-scan this directory recursively |
| `--file PATH` | Process exactly one file (what the watcher uses) |
| `--dry-run` | Report what would change, modify nothing |
| `--remove-sidecars` | Also delete matching sidecar subtitle files |
| `--keep-backup` | Keep the original as `<file>.bak` instead of overwriting it |
| `--ext "..."` | Extensions to scan in `--dir` mode |
| `--manifest PATH` | Incremental mode skip files already checked (by path+size+mtime), record newly-checked ones |
| `--prune-manifest` | Drop manifest entries for files that no longer exist on disk |

```bash
# One-off bulk pass over an existing library, see what would change first
./strip-embedded-subtitles.sh --dir /mnt/media --dry-run

# Then actually run it
./strip-embedded-subtitles.sh --dir /mnt/media --remove-sidecars

# Nightly cron safety net (same manifest path as the watcher's --manifest)
0 2 * * * /opt/subtitle-strip/strip-embedded-subtitles.sh --dir /mnt/media --manifest /var/lib/subtitle-strip/manifest.tsv --remove-sidecars >> /var/log/subtitle-strip-cron.log 2>&1
```

## Troubleshooting

**Service won't start / exits immediately**
Check `journalctl -u media-subtitle-watcher.service -n 50` the most
common causes are: `--dir` doesn't exist or isn't readable by `User=`,
`inotifywait` isn't installed, or `strip-embedded-subtitles.sh` isn't
executable / isn't at the path `--strip-script` expects.

**Files aren't being picked up**
Confirm the extension is in `--ext` (default covers `mkv mp4 m4v avi mov
ts` add yours if it's something else, e.g. `webm`). Also check the file
actually triggers a `close_write` or `moved_to` event some tools use
other write patterns; watch the logs while manually copying a test file in.

**A file was skipped with "didn't settle"**
The file's size was still changing after the `--settle-seconds` window
either it's still downloading, or `--settle-seconds` is too short for your
download speed. Increase it (e.g. `--settle-seconds 60`) for slower
connections; the file will simply be picked up again on its next write
event.

**High disk I/O during a big batch import**
Expected every file with subtitles gets remuxed (read + write the whole
file), just without re-encoding. `Nice=`/`IOSchedulingClass=` in the
`.service` file already de-prioritize this relative to other processes;
lower `IOSchedulingPriority=` further (up to `7`) if it's still noticeable.

**Manifest file grew large / references deleted files**
Run `strip-embedded-subtitles.sh --dir ... --manifest ... --prune-manifest`
periodically to drop entries for files that no longer exist.
