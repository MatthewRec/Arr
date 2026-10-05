#!/usr/bin/env python3
"""Sonarr stall watchdog. Run from cron every 5 minutes.

Why: Sonarr runs at most 3 commands at once. If long searches hold all 3
slots, download tracking (RefreshMonitoredDownloads) waits in the queue, the
Activity page freezes and finished downloads don't import. The throttled
searcher prevents most of this; the watchdog catches the rest.

Sonarr counts as stalled if any of these is true:
  * the API does not answer within API_TIMEOUT seconds
  * RefreshMonitoredDownloads has been queued longer than TRACKING_MINUTES
  * 3 or more searches have been running longer than SEARCH_MINUTES each
  * an import command has been queued longer than IMPORT_MINUTES

What it does:
  1. First check that finds a stall: cancels queued search commands (only
     queued ones; Sonarr can't cancel a running command). A cancelled search
     never ran, so it does not set lastSearchTime and the throttled searcher
     sends it again later.
  2. Still stalled at the next check: docker restart sonarr, at most
     MAX_RESTARTS times in RESTART_WINDOW_HOURS. Then alert only. No checks for
     GRACE_MINUTES after a restart while Sonarr starts up. The restart waits
     if an import is running.
  3. Posts to Slack (#sonarr-alerts) only when the state changes: stalled,
     restarted, restart limit reached, recovered.

Slack: webhook URL in SLACK_ENV as SLACK_WEBHOOK_SONARR_ALERTS=<url>. While
that is empty, messages go to the log only. Never print the URL.

  --dry-run      show what would be done; change nothing, post nothing
  --test-slack   send one test message and exit
  --status       post the current state to Slack now (on demand); changes nothing
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

SONARR = "http://<VM_IP>:8989/api/v3"
CONFIG = "/docker/servarr/sonarr/config.xml"
SLACK_ENV = "/docker/servarr/slack.env"
STATE = os.path.expanduser("~/sonarr-watchdog-state.json")
API_TIMEOUT = 10          # seconds
TRACKING_MINUTES = 15
SEARCH_MINUTES = 30
IMPORT_MINUTES = 30
MAX_RESTARTS = 2
RESTART_WINDOW_HOURS = 6
GRACE_MINUTES = 10
IMPORTS = ("ProcessMonitoredDownloads", "DownloadedEpisodesScan", "ManualImport")

NOW = datetime.datetime.now(datetime.timezone.utc)
DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    print(f"{NOW:%Y-%m-%d %H:%M} {'DRY RUN ' if DRY_RUN else ''}{msg}", flush=True)


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def minutes_since(s):
    return (NOW - ts(s)).total_seconds() / 60


def api(path, method="GET"):
    key = re.search(r"<ApiKey>([^<]+)", open(CONFIG).read()).group(1)
    req = urllib.request.Request(SONARR + path, headers={"X-Api-Key": key}, method=method)
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
        body = r.read()
    return json.loads(body) if body else None


def webhook():
    try:
        m = re.search(r"^SLACK_WEBHOOK_SONARR_ALERTS=(\S+)", open(SLACK_ENV).read(), re.M)
    except OSError:
        return None
    url = m.group(1) if m else None
    return url if url and url.startswith("https://hooks.slack.com/") else None


def notify(text):
    url = webhook()
    if DRY_RUN or not url:
        log(f"slack {'(dry run)' if DRY_RUN else '(not set up)'}: {text}")
        return
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        log(f"slack sent: {text}")
    except Exception as e:   # message only; the URL is never logged
        log(f"slack failed ({type(e).__name__}): {text}")


def load_state():
    try:
        return json.load(open(STATE))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log("WARNING state file unreadable, starting fresh")
        return {}


def save_state(state):
    if DRY_RUN:
        return
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def check():
    """Return (reasons, commands). commands is None if the API did not answer."""
    try:
        commands = api("/command")
    except Exception as e:
        return [f"API did not answer within {API_TIMEOUT} s ({type(e).__name__})"], None
    queued = [c for c in commands if c["status"] == "queued"]
    started = [c for c in commands if c["status"] == "started"]
    reasons = []
    for c in queued:
        waited = minutes_since(c["queued"])
        if c["name"] == "RefreshMonitoredDownloads" and waited > TRACKING_MINUTES:
            reasons.append(f"download tracking queued {waited:.0f} min")
        elif c["name"] in IMPORTS and waited > IMPORT_MINUTES:
            reasons.append(f"{c['name']} queued {waited:.0f} min")
    long_searches = [c for c in started if "Search" in c["name"] and minutes_since(c["started"]) > SEARCH_MINUTES]
    if len(long_searches) >= 3:
        names = ", ".join(f"{c['name']} {minutes_since(c['started']):.0f} min" for c in long_searches)
        reasons.append(f"{len(long_searches)} long searches hold the command slots ({names})")
    return reasons, commands


def cancel_queued_searches(commands):
    searches = [c for c in commands if c["status"] == "queued" and "Search" in c["name"]]
    cancelled = 0
    for c in searches:
        if DRY_RUN:
            cancelled += 1
            continue
        try:
            api(f"/command/{c['id']}", method="DELETE")
            cancelled += 1
        except Exception as e:
            log(f"cancel {c['name']} #{c['id']} failed ({type(e).__name__})")
    return cancelled


def restart_sonarr():
    if DRY_RUN:
        return True
    docker = shutil.which("docker") or "/usr/bin/docker"
    r = subprocess.run([docker, "restart", "sonarr"], capture_output=True, text=True, timeout=180)
    if r.returncode:
        log(f"docker restart sonarr failed: {r.stderr.strip()[:200]}")
    return r.returncode == 0


def main():
    if "--test-slack" in sys.argv:
        if not webhook():
            log(f"no SLACK_WEBHOOK_SONARR_ALERTS in {SLACK_ENV}; nothing sent")
            return
        notify("Test message from the Sonarr watchdog. Alerts will arrive here.")
        return

    if "--status" in sys.argv:
        reasons, commands = check()
        restarts = [r for r in load_state().get("restarts", [])
                    if ts(r) > NOW - datetime.timedelta(hours=RESTART_WINDOW_HOURS)]
        if commands is None:
            head = f":warning: Sonarr status: {'; '.join(reasons)}"
        else:
            running = [c["name"] for c in commands if c["status"] == "started"]
            queued = sum(1 for c in commands if c["status"] == "queued")
            head = (f"{':warning: Sonarr status: stalled (' + '; '.join(reasons) + ')' if reasons else ':white_check_mark: Sonarr status: OK'}"
                    f" · {len(running)} running ({', '.join(running) or 'none'}) · {queued} queued")
        notify(f"{head} · {len(restarts)} watchdog restart(s) in the last {RESTART_WINDOW_HOURS} h")
        return

    state = load_state()
    window = NOW - datetime.timedelta(hours=RESTART_WINDOW_HOURS)
    state["restarts"] = [r for r in state.get("restarts", []) if ts(r) > window]

    last = state["restarts"][-1] if state["restarts"] else None
    if last and minutes_since(last) < GRACE_MINUTES:
        log(f"restarted {minutes_since(last):.0f} min ago, waiting for Sonarr to start")
        save_state(state)
        return

    reasons, commands = check()

    if not reasons:
        if state.get("stall_since"):
            took = minutes_since(state["stall_since"])
            notify(f":white_check_mark: Sonarr recovered after {took:.0f} min.")
            log(f"recovered after {took:.0f} min")
        else:
            log("ok")
        state.pop("stall_since", None)
        state.pop("limit_alerted", None)
        save_state(state)
        return

    why = "; ".join(reasons)

    # Step 1: first check that sees the stall -> cancel queued searches.
    if not state.get("stall_since"):
        state["stall_since"] = NOW.isoformat()
        if commands is None:
            action = "API not answering, so nothing could be cancelled."
        else:
            n = cancel_queued_searches(commands)
            action = f"Cancelled {n} queued search(es)."
        notify(f":warning: Sonarr has stalled: {why}. {action} "
               f"Will restart Sonarr at the next check if still stalled.")
        log(f"stalled: {why}. {action}")
        save_state(state)
        return

    # Step 2: still stalled -> restart, within the limit.
    if commands and any(c["status"] == "started" and c["name"] in IMPORTS for c in commands):
        log(f"still stalled ({why}), but an import is running; restart postponed")
        save_state(state)
        return
    if len(state["restarts"]) >= MAX_RESTARTS:
        if not state.get("limit_alerted"):
            notify(f":rotating_light: Sonarr is still stalled ({why}). It was restarted {MAX_RESTARTS} times "
                   f"in the last {RESTART_WINDOW_HOURS} h, so no more automatic restarts. Please check it.")
            state["limit_alerted"] = True
        log(f"still stalled ({why}); restart limit reached, alert only")
        save_state(state)
        return
    ok = restart_sonarr()
    state["restarts"].append(NOW.isoformat())   # failed tries count too, so they can't repeat every 5 min
    if ok:
        notify(f":arrows_counterclockwise: Sonarr still stalled ({why}). Ran docker restart sonarr "
               f"(restart {len(state['restarts'])} of {MAX_RESTARTS} in {RESTART_WINDOW_HOURS} h).")
        log(f"restarted sonarr: {why}")
    else:
        notify(f":rotating_light: Sonarr is stalled ({why}) and docker restart sonarr failed. Please check it.")
    save_state(state)


if __name__ == "__main__":
    main()
