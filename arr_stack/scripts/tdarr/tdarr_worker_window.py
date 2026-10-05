#!/usr/bin/env python3
"""Tdarr worker window. Run from cron every 5 minutes.

Backlog mode (a large conversion queue is draining, schedule open all hours):
  - 06:30-22:00 Eastern (viewing hours): 1 GPU worker, 0 CPU workers, so
    Jellyfin keeps most of the Quick Sync GPU for playback transcodes;
  - other hours: 2 CPU + 2 GPU workers (all 4 use Quick Sync).
  When nothing is queued and no worker is busy, switch to normal mode once:
  set the 01:00-07:00 schedule on every library and 2 CPU + 3 GPU workers.

Normal mode: the library schedule is in whole hours (01:00-07:00), so this
script ends the window at 06:30 by setting 0 workers from 06:30 to 07:00.
Running jobs are never killed; they finish their current file.

Mode is kept in ~/tdarr-worker-window-state.json. Log: ~/tdarr-worker-window.log.
--dry-run prints what it would do.
"""
import json, os, sys, urllib.request
from datetime import datetime, time
from zoneinfo import ZoneInfo

T = "http://<VM_IP>:8265/api/v2"
ENV = "/docker/servarr/.env"  # holds TDARR_API_KEY
LOG = os.path.expanduser("~/tdarr-worker-window.log")
STATE = os.path.expanduser("~/tdarr-worker-window-state.json")
TZ = ZoneInfo("America/New_York")
# Backlog mode
DAY_START, DAY_END = time(6, 30), time(22, 0)
DAY = {"transcodecpu": 0, "transcodegpu": 1}
NIGHT = {"transcodecpu": 2, "transcodegpu": 2}
# Normal mode: window 01:00-06:30 (schedule hours 01-07, workers off 06:30-07:00)
SCHEDULE_HOURS = range(1, 7)
WINDOW_END = time(6, 30)
NORMAL = {"transcodecpu": 2, "transcodegpu": 3}
OFF = {"transcodecpu": 0, "transcodegpu": 0}
DRY = "--dry-run" in sys.argv
K = open(ENV).read().split("TDARR_API_KEY=")[1].split()[0]


def log(msg):
    line = f"{datetime.now(TZ):%F %T %Z} {msg}"
    if DRY:
        print(line)
    else:
        with open(LOG, "a") as f:
            f.write(line + "\n")


def call(path, data=None):
    body = json.dumps({"data": data}).encode() if data is not None else None
    req = urllib.request.Request(f"{T}/{path}", data=body,
                                 headers={"x-api-key": K, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        b = r.read()
        return json.loads(b) if b[:1] in (b"[", b"{") else b


def set_limits(node_id, current, target):
    """Tdarr only steps limits by one, so step until each type matches."""
    for wtype, want in target.items():
        have = current.get(wtype, 0)
        if have == want:
            continue
        log(f"{wtype}: {have} -> {want}")
        if DRY:
            continue
        process = "increase" if want > have else "decrease"
        for _ in range(abs(want - have)):
            call("alter-worker-limit", {"nodeID": node_id, "process": process, "workerType": wtype})


def set_normal_schedule():
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    schedule = [{"_id": f"{d}:{h:02d}-{h + 1:02d}", "checked": h in SCHEDULE_HOURS}
                for d in days for h in range(24)]
    for lib in call("cruddb", {"collection": "LibrarySettingsJSONDB", "mode": "getAll"}):
        log(f"schedule 01:00-07:00: {lib['name']}")
        if not DRY:
            call("cruddb", {"collection": "LibrarySettingsJSONDB", "mode": "update",
                            "docID": lib["_id"], "obj": {"schedule": schedule}})


def main():
    mode = json.load(open(STATE))["mode"] if os.path.exists(STATE) else "backlog"
    nodes = call("get-nodes")
    if not nodes:
        log("no Tdarr node online; nothing done")
        return
    node_id, node = next(iter(nodes.items()))
    limits = node.get("workerLimits", {})
    now = datetime.now(TZ).time()

    if mode == "normal":
        target = OFF if WINDOW_END <= now < time(7, 0) else NORMAL
        set_limits(node_id, limits, target)
        return

    busy = sum(len(n.get("workers", {})) for n in nodes.values())
    files = call("cruddb", {"collection": "FileJSONDB", "mode": "getAll"})
    queued = sum(f.get("TranscodeDecisionMaker") == "Queued" for f in files)
    if queued == 0 and busy == 0:
        log("backlog done: switching to normal mode")
        set_normal_schedule()
        set_limits(node_id, limits, NORMAL)
        if not DRY:
            json.dump({"mode": "normal", "since": f"{datetime.now(TZ):%F %T}"}, open(STATE, "w"))
        return
    if queued == 0:
        return  # last files still converting; switch on a later run

    day = DAY_START <= now < DAY_END
    target = DAY if day else NIGHT
    if any(limits.get(k, 0) != v for k, v in target.items()):
        log(f"queued={queued} busy={busy} window={'day' if day else 'night'}")
    set_limits(node_id, limits, target)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"error: {e!r}")
        sys.exit(1)
