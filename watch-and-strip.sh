#!/usr/bin/env bash
#
# watch-and-strip.sh
#
# Real-time companion to strip-embedded-subtitles.sh. Watches a media
# directory tree with inotify and, the moment a new (or modified) video file
# shows up, waits for it to finish being written to disk, then strips any
# embedded subtitle streams from it immediately -- no cron, no periodic
# re-scanning of your whole library, no wasted work.
#
# This is the recommended approach over a cron-based full/incremental scan:
# it reacts to files the instant they land (regardless of whether they came
# in via Sonarr/Radarr, a manual copy, rsync, whatever), and it only ever
# touches the one file that changed. Run strip-embedded-subtitles.sh's
# --manifest mode from cron alongside this as a low-frequency safety net
# (see the nightly example in that script's help) in case the watcher was
# down for some reason (reboot before the service re-enabled, etc).
#
# ---------------------------------------------------------------------------
# REQUIREMENTS
#   inotify-tools   apt install inotify-tools
#   strip-embedded-subtitles.sh in the same directory as this script
#     (or point at it with --strip-script)
#
# USAGE
#   ./watch-and-strip.sh --dir /path/to/media [options]
#
# OPTIONS
#   --dir PATH             Root media directory to watch (required)
#   --ext "mkv mp4 m4v avi mov ts"   Extensions to react to
#                          (default: "mkv mp4 m4v avi mov ts")
#   --settle-seconds N     After a file event fires, wait until the file's
#                          size has been stable for this many seconds before
#                          processing it -- avoids grabbing a file mid-write
#                          from your download client. Default: 20
#   --strip-script PATH    Path to strip-embedded-subtitles.sh
#                          (default: same directory as this script)
#   --manifest PATH        Also record each processed file in this manifest,
#                          so a --manifest cron safety-net scan (see
#                          strip-embedded-subtitles.sh) knows it's already
#                          been handled and won't recheck it.
#   --remove-sidecars      Passed through: also delete matching sidecar
#                          subtitle files (.srt/.ass/.ssa/.sub/.idx/.vtt)
#
# Designed to run forever as a systemd service -- see
# media-subtitle-watcher.service in this delivery for a ready-to-use unit.
# ---------------------------------------------------------------------------

set -uo pipefail  # note: no -e here -- this loop must keep running even if one event errors

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEDIA_DIR=""
EXTENSIONS="mkv mp4 m4v avi mov ts"
SETTLE_SECONDS=20
STRIP_SCRIPT="${SCRIPT_DIR}/strip-embedded-subtitles.sh"
MANIFEST=""
REMOVE_SIDECARS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) MEDIA_DIR="$2"; shift 2 ;;
    --ext) EXTENSIONS="$2"; shift 2 ;;
    --settle-seconds) SETTLE_SECONDS="$2"; shift 2 ;;
    --strip-script) STRIP_SCRIPT="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --remove-sidecars) REMOVE_SIDECARS=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

log()  { printf '\033[1;34m[*]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }

[[ -z "$MEDIA_DIR" ]] && { err "--dir is required. See --help."; exit 1; }
[[ -d "$MEDIA_DIR" ]] || { err "Directory not found: $MEDIA_DIR"; exit 1; }
[[ -x "$STRIP_SCRIPT" ]] || { err "strip-embedded-subtitles.sh not found or not executable at: $STRIP_SCRIPT (use --strip-script)"; exit 1; }
command -v inotifywait >/dev/null 2>&1 || { err "inotifywait not found. Install it: apt install inotify-tools"; exit 1; }

# Build a regex like \.(mkv|mp4|m4v|avi|mov|ts)$ for filtering inotify events
ext_regex="\.($(echo "$EXTENSIONS" | tr ' ' '|'))$"

is_settled() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  local size1 size2
  size1="$(stat -c '%s' "$f" 2>/dev/null)" || return 1
  sleep "$SETTLE_SECONDS"
  [[ -f "$f" ]] || return 1
  size2="$(stat -c '%s' "$f" 2>/dev/null)" || return 1
  [[ "$size1" == "$size2" ]]
}

handle_file() {
  local f="$1"
  log "Detected: $f -- waiting for it to settle (${SETTLE_SECONDS}s size-stability check) ..."
  if ! is_settled "$f"; then
    warn "File didn't settle (still being written, or disappeared) -- skipping for now: $f"
    return
  fi
  log "Settled. Processing: $f"
  local args=( --file "$f" )
  [[ -n "$MANIFEST" ]] && args+=( --manifest "$MANIFEST" )
  [[ $REMOVE_SIDECARS -eq 1 ]] && args+=( --remove-sidecars )
  "$STRIP_SCRIPT" "${args[@]}" || warn "strip-embedded-subtitles.sh reported an error for: $f (see output above)"
}

log "Watching ${MEDIA_DIR} for new/changed video files (${EXTENSIONS}) ..."
log "Settle window: ${SETTLE_SECONDS}s | strip script: ${STRIP_SCRIPT}"

# close_write: a file was open for writing and got closed (covers most download clients)
# moved_to:    a file was moved/renamed into the watched tree (covers atomic-rename-on-complete clients)
# Processed one at a time, deliberately: strip-embedded-subtitles.sh's
# --manifest append isn't lock-protected, and a season-pack import landing
# 10 episodes at once shouldn't spin up 10 concurrent ffmpeg remuxes. Each
# file is -c copy (no re-encode), so serial processing is still fast --
# typically a few seconds per file.
inotifywait -m -r -e close_write -e moved_to --exclude '\.nosubs\.tmp\.' --format '%w%f' "$MEDIA_DIR" 2>/dev/null |
while IFS= read -r changed_file; do
  if [[ "$changed_file" =~ $ext_regex ]]; then
    handle_file "$changed_file"
  fi
done
