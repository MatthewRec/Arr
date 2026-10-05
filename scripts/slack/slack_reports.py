#!/usr/bin/env python3
"""Server updates in Slack: health alerts, daily digest, usage stats, weekly downloads.

Modes (first argument):
  health    every 5 min. Posts to #server-health only when a check changes:
            a new problem (seen on CONFIRM_CHECKS checks in a row), a reminder
            for a problem still open after REMIND_MINUTES, and "resolved".
            Each alert is a card (red = open problem, green = all resolved)
            with a status board: containers, VPN, Jellyfin, Seerr, Sonarr,
            Radarr, NZBGet, storage.
  digest    daily: the same status board plus Tdarr progress (#server-health).
  usage     weekly (Friday): Jellyfin plays, viewers, peak streams, direct play
            share, failed logins, a plays-by-day bar graph and the most watched
            titles over the last 7 days (#usage-stats).
  weekly    a table of every movie and show downloaded this week (title,
            season, status), GB added, space left, Seerr requests
            (#weekly-downloads).
  test      sends one test message to every channel that has a webhook.

Options:
  --dry-run        print the message (and its Slack blocks) instead of posting
                   it; change no state
  --local-hour H   run only if the hour in LOCAL_TZ is H. The VM runs on UTC and
                   Debian cron has no CRON_TZ, so cron starts the job at both
                   possible UTC hours and this keeps the one that matches.
  --simulate KEY   health only: post a test alert for problem KEY (e.g.
                   jellyfin:public, vpn, container:sonarr) to preview the card.
                   Changes no state.

Checks are read-only. Webhook URLs come from SLACK_ENV and are never printed.
While a channel's URL is empty, its messages go to the log only.
Usernames, IP addresses and public hostnames are never posted.
"""
import base64
import collections
import datetime
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import zoneinfo

HOST = "<VM_IP>"
SLACK_ENV = "/docker/servarr/slack.env"
STACK_ENV = "/docker/servarr/.env"
SONARR_CONFIG = "/docker/servarr/sonarr/config.xml"
RADARR_CONFIG = "/docker/servarr/radarr/config.xml"
NZBGET_CONF = "/docker/servarr/nzbget/nzbget.conf"
SEERR_SETTINGS = "/docker/servarr/seerr/settings.json"
JF_DB = "/docker/jellyfin/config/data/data/jellyfin.db"
JF_LOGS = "/docker/jellyfin/config/log"
STATE = os.path.expanduser("~/slack-reports-state.json")
DIGEST_STATE = os.path.expanduser("~/slack-reports-digest.json")
LOCAL_TZ = zoneinfo.ZoneInfo("America/New_York")

# Reachability checks: (LAN URL, public URL through the Cloudflare tunnel).
# A public URL left as a <placeholder> is skipped. Hostnames are never posted.
ACCESS = {
    "jellyfin": (f"http://{HOST}:8096/health", "https://<JELLYFIN_PUBLIC_HOST>/health"),
    "seerr": (f"http://{HOST}:5055/api/v1/status", "https://<SEERR_PUBLIC_HOST>/api/v1/status"),
}
VPN_PROBE = "https://1.1.1.1/cdn-cgi/trace"   # fetched from inside gluetun, so it goes through the VPN

DISKS = ("/", "/scratch", "/data")
DISK_LIMIT = 85              # percent used
NZB_IDLE_MINUTES = 30        # 0 KB/s with items queued
CONFIRM_CHECKS = 2           # a problem must be seen this many checks in a row
REMIND_MINUTES = 60          # repeat an open problem at most this often
SHOWS = "/data/shows/"       # Tdarr library converted to HEVC
EFFICIENT = ("hevc", "av1", "vp9")
TOP_N = 5
USAGE_DAYS = 7               # usage report covers this many full days before the run
BAR_WIDTH = 20               # characters in the longest bar of the plays-by-day graph
TABLE_ROWS = 99              # Slack tables hold 100 rows; one is the header

CHANNELS = {
    "health": "SLACK_WEBHOOK_SERVER_HEALTH",
    "digest": "SLACK_WEBHOOK_SERVER_HEALTH",
    "usage": "SLACK_WEBHOOK_USAGE_STATS",
    "weekly": "SLACK_WEBHOOK_WEEKLY_DOWNLOADS",
}

# Status board on the health cards: problem key prefix -> board tile.
BOARD = ("Containers", "VPN", "Jellyfin", "Seerr", "Sonarr", "Radarr", "NZBGet", "Storage")
TILE_OF = {"container": "Containers", "docker": "Containers", "vpn": "VPN", "jellyfin": "Jellyfin",
           "seerr": "Seerr", "sonarr": "Sonarr", "radarr": "Radarr", "nzbget": "NZBGet",
           "data": "Storage", "disk": "Storage"}
RED, GREEN = "#E01E5A", "#2EB67D"
UNICODE = {"white_check_mark": "✅", "warning": "⚠️", "x": "❌"}

NOW = datetime.datetime.now(datetime.timezone.utc)
DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    print(f"{NOW:%Y-%m-%d %H:%M} {'DRY RUN ' if DRY_RUN else ''}{msg}", flush=True)


# ---------- Slack ----------

def webhook(var):
    try:
        m = re.search(rf"^{var}=(\S+)", open(SLACK_ENV).read(), re.M)
    except OSError:
        return None
    url = m.group(1) if m else None
    return url if url and url.startswith("https://hooks.slack.com/") else None


def send(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def post(var, text, blocks=None, attachments=None, fallback=None):
    """Post to the channel behind webhook variable var. True if sent.
    text is the notification line; blocks/attachments are the Block Kit layout.
    If Slack rejects the blocks, fallback (other blocks) is sent instead."""
    payload = {"text": text[:3900]}
    if blocks:
        payload["blocks"] = blocks
    if attachments:
        payload["attachments"] = attachments
    url = webhook(var)
    if DRY_RUN or not url:
        log(f"slack {var} {'(dry run)' if DRY_RUN else '(not set up)'}:\n{text}")
        if DRY_RUN and (blocks or attachments):
            print(json.dumps({k: v for k, v in payload.items() if k != "text"}, ensure_ascii=False, indent=1))
        return DRY_RUN
    first = text.splitlines()[0][:120]
    try:
        send(url, payload)
        log(f"slack {var} sent: {first}")
        return True
    except urllib.error.HTTPError as e:   # Slack's answer names the problem (e.g. invalid_blocks), never the URL
        log(f"slack {var} rejected ({e.code} {e.read().decode(errors='replace')[:200]}): {first}")
        if fallback:
            try:
                send(url, {"text": text[:3900], "blocks": fallback})
                log(f"slack {var} sent (fallback layout): {first}")
                return True
            except Exception as e2:
                log(f"slack {var} fallback failed ({type(e2).__name__})")
        return False
    except Exception as e:   # the URL is never logged
        log(f"slack {var} failed ({type(e).__name__}): {first}")
        return False


def md(text):
    return {"type": "mrkdwn", "text": text}


def section(text):
    return {"type": "section", "text": md(text[:3000])}


def header(text):
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def context(text):
    return {"type": "context", "elements": [md(text)]}


def local_now():
    return datetime.datetime.now(LOCAL_TZ)


# ---------- helpers ----------

def get_json(url, headers=None, data=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def http_error(url, timeout=15):
    """None if url answers 2xx/3xx, else a short reason (no hostname)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "slack_reports health check"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return None if r.status < 400 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:
        return type(e).__name__


def arr(app, path):
    config, port = {"sonarr": (SONARR_CONFIG, 8989), "radarr": (RADARR_CONFIG, 7878)}[app]
    key = re.search(r"<ApiKey>([^<]+)", open(config).read()).group(1)
    return get_json(f"http://{HOST}:{port}/api/v3{path}", {"X-Api-Key": key}, timeout=60)


def nzbget(method, params=()):
    conf = open(NZBGET_CONF).read()
    user = re.search(r"^ControlUsername=(.*)$", conf, re.M).group(1).strip()
    pw = re.search(r"^ControlPassword=(.*)$", conf, re.M).group(1).strip()
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    body = json.dumps({"method": method, "params": list(params)}).encode()
    return get_json(f"http://{HOST}:6789/jsonrpc", {"Authorization": "Basic " + auth}, body, 15)["result"]


def tdarr_files():
    key = re.search(r"^TDARR_API_KEY=(\S+)", open(STACK_ENV).read(), re.M).group(1)
    body = json.dumps({"data": {"collection": "FileJSONDB", "mode": "getAll"}}).encode()
    return get_json(f"http://{HOST}:8265/api/v2/cruddb",
                    {"x-api-key": key, "content-type": "application/json"}, body, 120)


def seerr(path):
    key = json.load(open(SEERR_SETTINGS))["main"]["apiKey"]
    return get_json(f"http://{HOST}:5055/api/v1{path}", {"X-Api-Key": key})


def run(cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def load(path):
    try:
        return json.load(open(path))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log(f"WARNING {path} unreadable, starting fresh")
        return {}


def save(path, data):
    if DRY_RUN:
        return
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def minutes_since(s):
    return (NOW - ts(s)).total_seconds() / 60


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------- health checks ----------

def containers():
    r = run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.State}}|{{.Status}}"])
    out = []
    for line in r.stdout.splitlines():
        name, state, status = line.split("|", 2)
        health = "unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status else ""
        out.append((name, state, health))
    return out


def vpn_city():
    """City of the VPN exit, from gluetun's last 'Public IP address is' log line. The IP is dropped."""
    r = run(["docker", "logs", "--tail", "5000", "gluetun"], timeout=30)
    hits = re.findall(r"Public IP address is \S+ \(([^)]*?)\s*-\s*source", r.stdout + r.stderr)
    return hits[-1].split(",")[-1].strip() if hits else None


def disk_usage():
    """{mount: percent used}, or None for a mount that did not answer."""
    use = {}
    for m in DISKS:
        try:
            r = run(["timeout", "15", "df", "-P", m])
            use[m] = int(r.stdout.splitlines()[-1].split()[4].rstrip("%")) if r.returncode == 0 else None
        except Exception:
            use[m] = None
    return use


def check_health(state):
    """Return ({key: message}, facts) for the current problems."""
    problems, facts = {}, {}

    try:
        cs = containers()
        facts["containers"] = len(cs)
        facts["running"] = sum(1 for _, st, _ in cs if st == "running")
        for name, st, health in cs:
            if st != "running":
                problems[f"container:{name}"] = f"Container `{name}` is {st}"
            elif health == "unhealthy":
                problems[f"container:{name}"] = f"Container `{name}` is unhealthy"
    except Exception as e:
        problems["docker"] = f"Could not read container states ({type(e).__name__})"

    city = vpn_city()
    facts["vpn"] = city
    if city and state.get("vpn_city") and city != state["vpn_city"]:
        facts["vpn_changed"] = f"VPN exit moved from {state['vpn_city']} to {city}"
    if city:
        state["vpn_city"] = city
    try:
        r = run(["docker", "exec", "gluetun", "wget", "-q", "-T", "10", "-O", "/dev/null", VPN_PROBE], timeout=30)
        if r.returncode != 0:
            problems["vpn"] = "VPN has no internet connection (gluetun)"
    except Exception as e:
        problems["vpn"] = f"VPN check failed ({type(e).__name__})"

    for app, (lan, public) in ACCESS.items():
        name = app.capitalize()
        err = http_error(lan)
        if err:
            problems[f"{app}:lan"] = f"{name} is not answering on the LAN ({err})"
        if "<" not in public:
            err = http_error(public)
            if err:
                problems[f"{app}:public"] = f"{name} is not reachable from the internet ({err})"

    mounted = run(["findmnt", "-n", "-t", "cifs", "/data"]).returncode == 0
    reachable = run(["timeout", "15", "ls", "/data"]).returncode == 0
    if not (mounted and reachable):
        problems["data"] = "`/data` share is not mounted or not answering (Samba \"Host is down\"?)"

    use = disk_usage()
    facts["disks"] = use
    for m, pct in use.items():
        if pct is not None and pct > DISK_LIMIT:
            problems[f"disk:{m}"] = f"Disk `{m}` is {pct}% full"

    try:
        s = nzbget("status")
        queued = s["RemainingSizeMB"] > 0
        facts["nzb_items"] = len(nzbget("listgroups", [0]))
        if s["DownloadPaused"]:
            problems["nzbget:paused"] = "NZBGet downloads are paused"
        if queued and not s["DownloadPaused"] and s["DownloadRate"] == 0:
            state.setdefault("nzb_idle_since", NOW.isoformat())
            idle = minutes_since(state["nzb_idle_since"])
            if idle >= NZB_IDLE_MINUTES:
                problems["nzbget:idle"] = f"NZBGet at 0 KB/s for {idle:.0f} min with {s['RemainingSizeMB']} MB queued"
        else:
            state.pop("nzb_idle_since", None)
    except Exception as e:
        problems["nzbget:api"] = f"NZBGet API not answering ({type(e).__name__})"

    for app in ("sonarr", "radarr"):
        try:
            for h in arr(app, "/health"):
                if h.get("type") in ("warning", "error"):
                    problems[f"{app}:{h.get('source')}"] = f"{app.capitalize()}: {h.get('message', h.get('source'))}"
        except Exception as e:
            problems[f"{app}:api"] = f"{app.capitalize()} API not answering ({type(e).__name__})"

    return problems, facts


def disks_text(facts):
    return " · ".join(f"`{m}` {p}%" if p is not None else f"`{m}` ?" for m, p in facts.get("disks", {}).items())


def board(current, facts):
    """Status board: one tile per service, green or red, as section fields (2 columns)."""
    bad = collections.defaultdict(list)
    for key, msg in current.items():
        bad[TILE_OF.get(key.split(":")[0], "Containers")].append(msg)
    public = all("<" not in pub for _, pub in ACCESS.values())
    ok = {"Containers": f"{facts.get('running', '?')}/{facts.get('containers', '?')} running",
          "VPN": f"Connected · exit {facts.get('vpn') or 'unknown'}",
          "Jellyfin": "LAN + internet OK" if public else "LAN OK",
          "Seerr": "LAN + internet OK" if public else "LAN OK",
          "Sonarr": "No warnings",
          "Radarr": "No warnings",
          "NZBGet": f"Queue {facts.get('nzb_items', '?')}",
          "Storage": disks_text(facts) or "?"}
    fields = []
    for tile in BOARD:
        if bad[tile]:
            more = f"\n+{len(bad[tile]) - 3} more" if len(bad[tile]) > 3 else ""
            fields.append(md(f":red_circle: *{tile}*\n" + "\n".join(bad[tile][:3]) + more))
        else:
            fields.append(md(f":large_green_circle: *{tile}*\n{ok[tile]}"))
    return {"type": "section", "fields": fields}


def checked_line():
    return context(f"Checked {local_now():%a %b %-d, %-I:%M %p %Z} · health checks run every 5 minutes")


def health():
    simulate = sys.argv[sys.argv.index("--simulate") + 1] if "--simulate" in sys.argv else None
    state = load(STATE)
    known = state.setdefault("problems", {})
    current, facts = check_health(state)
    new, still, resolved = [], [], []

    if simulate:   # preview only: one fake new problem, nothing saved
        current[simulate] = f"Simulated problem `{simulate}` (test, nothing is wrong)"
        known = {simulate: {"since": NOW.isoformat(), "msg": current[simulate]}}
        new = [simulate]
    else:
        for key, msg in current.items():
            p = known.setdefault(key, {"since": NOW.isoformat(), "seen": 0})
            p["msg"], p["seen"] = msg, p["seen"] + 1
            if p["seen"] < CONFIRM_CHECKS:
                continue
            if not p.get("alerted"):
                new.append(key)
            elif minutes_since(p["alerted"]) >= REMIND_MINUTES:
                still.append(key)
        for key in [k for k in known if k not in current]:
            if known[key].get("alerted"):
                resolved.append(key)
            else:
                known.pop(key)

    lines = [f":rotating_light: *New:* {known[k]['msg']}" for k in new]
    lines += [f":hourglass: *Still open* ({minutes_since(known[k]['since']):.0f} min): {known[k]['msg']}" for k in still]
    lines += [f":white_check_mark: *Resolved* after {minutes_since(known[k]['since']):.0f} min: {known[k]['msg']}"
              for k in resolved]
    if facts.get("vpn_changed"):
        lines.append(f":globe_with_meridians: {facts['vpn_changed']}")

    if lines:
        open_now = new + still + [k for k in known if k in current and known[k].get("alerted")]
        if new:
            title = f":rotating_light: {known[new[0]]['msg']}" + (f" (+{len(new) - 1} more)" if len(new) > 1 else "")
        elif still:
            title = f":hourglass: Still open: {known[still[0]]['msg']}"
        elif resolved:
            title = ":white_check_mark: Resolved: " + "; ".join(known[k]["msg"] for k in resolved)
        else:
            title = f":globe_with_meridians: {facts['vpn_changed']}"
        if simulate:
            title = ":test_tube: TEST ALERT · " + title
        blocks = [section("\n".join(lines)), {"type": "divider"}, board(current, facts), checked_line()]
        sent = post(CHANNELS["health"], title, attachments=[{"color": RED if open_now else GREEN, "blocks": blocks}])
        if sent and not simulate:
            for k in new + still:
                known[k]["alerted"] = NOW.isoformat()
            for k in resolved:
                known.pop(k)
    else:
        log(f"ok ({len(current)} unconfirmed)" if current else "ok")
    if not simulate:
        save(STATE, state)


# ---------- daily digest ----------

def tdarr_progress(files):
    shows = [f for f in files if f.get("file", "").startswith(SHOWS) and f.get("fileMedium") == "video"]
    left = sum(1 for f in shows if (f.get("bit_rate") or 0) > 4e6 and f.get("video_codec_name") not in EFFICIENT)
    queued = sum(1 for f in files if f.get("TranscodeDecisionMaker") == "Queued")
    errors = sum(1 for f in files if f.get("TranscodeDecisionMaker") == "Transcode error")
    return left, queued, errors


def digest():
    state = load(STATE)
    current, facts = check_health(dict(state))   # a copy: the 5-min health run owns the state file
    title = (":white_check_mark: Daily check: all clear" if not current
             else f":warning: Daily check: {plural(len(current), 'open problem')}")
    try:
        left, queued, errors = tdarr_progress(tdarr_files())
        prev = load(DIGEST_STATE).get("tdarr_left")
        change = f" ({left - prev:+d} since yesterday)" if prev is not None and prev != left else ""
        tdarr = f":film_frames: *Tdarr:* {left} shows left to convert to HEVC{change} · {queued} queued · {plural(errors, 'error')}"
        save(DIGEST_STATE, {"tdarr_left": left, "when": NOW.isoformat()})
    except Exception as e:
        tdarr = f":film_frames: *Tdarr:* no answer ({type(e).__name__})"
    blocks = [board(current, facts), {"type": "divider"}, section(tdarr)]
    if current:
        blocks.append(section("\n".join(f"• {m}" for m in current.values())))
    blocks.append(checked_line())
    post(CHANNELS["digest"], title, attachments=[{"color": RED if current else GREEN, "blocks": blocks}])


# ---------- usage ----------

def jellyfin_rows(since):
    """ActivityLogs rows since `since` (UTC), read from a copy of the database."""
    tmp = "/tmp/slack_reports_jf.db"
    shutil.copy(JF_DB, tmp)
    if os.path.exists(JF_DB + "-wal"):
        shutil.copy(JF_DB + "-wal", tmp + "-wal")
    try:
        db = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        rows = db.execute("select Type, Name, UserId, ItemId, DateCreated from ActivityLogs where DateCreated >= ?",
                          (since.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
        # Show name for episodes, title for movies. ActivityLogs ids have no dashes, BaseItems ids may.
        names = {}
        for item in {r[3] for r in rows if r[3]}:
            hit = db.execute("select coalesce(SeriesName, Name) from BaseItems "
                             "where lower(replace(Id, '-', '')) = lower(replace(?, '-', ''))", (item,)).fetchone()
            if hit and hit[0]:
                names[item] = hit[0]
        db.close()
    finally:
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            if os.path.exists(p):
                os.remove(p)
    out = []
    for typ, name, user, item, created in rows:
        when = datetime.datetime.fromisoformat(str(created)[:19]).replace(tzinfo=datetime.timezone.utc)
        out.append((typ, names.get(item) or title_of(name or ""), user, item, when))
    return out


def title_of(name):
    """Fallback when the item is gone from the library (e.g. replaced by Tdarr):
    '<user> is playing <show> - <episode> on <device>' -> <show>. The username is dropped."""
    return name.split(" is playing ", 1)[-1].rsplit(" on ", 1)[0].split(" - ", 1)[0].strip()


def usage_stats(rows, start, end):
    plays = [r for r in rows if r[0] == "VideoPlayback" and start <= r[4] < end]
    stats = {"plays": len({(r[2], r[3]) for r in plays}),
             "viewers": len({r[2] for r in plays}),
             "failed": sum(1 for r in rows if r[0] == "AuthenticationFailed" and start <= r[4] < end),
             "lockouts": sum(1 for r in rows if r[0] == "UserLockedOut" and start <= r[4] < end),
             # one count per viewer and episode/movie, so restarts and seeks don't inflate a title
             "top": collections.Counter(t for t, _, _ in {(r[1], r[2], r[3]) for r in plays}).most_common(TOP_N),
             # plays per local day, same counting rule within each day
             "by_day": collections.Counter(d for d, _, _ in
                                           {(r[4].astimezone(LOCAL_TZ).date(), r[2], r[3]) for r in plays})}

    # Peak: replay starts and stops in time order; a start without a stop counts for at most 3 h.
    events = sorted((r[4], r[0], (r[2], r[3])) for r in rows if r[0] in ("VideoPlayback", "VideoPlaybackStopped"))
    active, peak = {}, 0
    for when, typ, key in events:
        active = {k: t for k, t in active.items() if when - t < datetime.timedelta(hours=3)}
        if typ == "VideoPlayback":
            active[key] = when
        else:
            active.pop(key, None)
        if start <= when < end:
            peak = max(peak, len(active))
    stats["peak"] = peak

    titles = set()   # one title can create several transcode logs (seeks, restarts)
    for p in glob.glob(f"{JF_LOGS}/FFmpeg.Transcode*"):
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc)
        if start <= mtime < end + datetime.timedelta(hours=3):
            head = open(p, errors="replace").read(4000)
            titles.add(head.split('"Path":"', 1)[-1].split('"', 1)[0])
    stats["direct"] = round(100 * max(0, stats["plays"] - len(titles)) / stats["plays"]) if stats["plays"] else None
    return stats


def usage_line(s):
    direct = f"~{s['direct']}% direct play" if s["direct"] is not None else "no plays"
    sec = f"{plural(s['failed'], 'failed login')}" + (f", {plural(s['lockouts'], 'lockout')}" if s["lockouts"] else "")
    return (f"{plural(s['plays'], 'play')} · {plural(s['viewers'], 'viewer')} · "
            f"peak {plural(s['peak'], 'stream')} · {direct} · {sec}")


def bar(n, top):
    """Horizontal bar in eighths of a character, scaled so `top` fills BAR_WIDTH."""
    eighths = round(n / top * BAR_WIDTH * 8) if top else 0
    full, part = divmod(eighths, 8)
    return ("█" * full + ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")[part]) or ("▏" if n else "")


def day_graph(by_day, days):
    top = max((by_day[d] for d in days), default=0)
    lines = []
    for d in days:
        n = by_day[d]
        mark = "  ◀ busiest" if n and n == top else ""
        lines.append(f"{d:%a %b %d}  {bar(n, top):<{BAR_WIDTH}}  {n:>3}{mark}")
    return "```\n" + "\n".join(lines) + "\n```"


def usage():
    """The 7 full days before today (local time). Run on Friday: Friday to Thursday."""
    today = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - datetime.timedelta(days=USAGE_DAYS)
    start, end = first.astimezone(datetime.timezone.utc), today.astimezone(datetime.timezone.utc)
    rows = jellyfin_rows(start - datetime.timedelta(days=1))   # one day earlier, for streams already running
    s = usage_stats(rows, start, end)
    span = f"{first:%a %b %-d} – {today - datetime.timedelta(days=1):%a %b %-d}"
    days = [(first + datetime.timedelta(days=i)).date() for i in range(USAGE_DAYS)]

    blocks = [header(f":tv: Usage · {span}"), section(usage_line(s)),
              section("*Plays by day*\n" + day_graph(s["by_day"], days))]
    if s["top"]:
        blocks.append(section("*Most watched*\n" + "\n".join(f"• {t} — {plural(n, 'play')}" for t, n in s["top"])))
    blocks.append(context("A play is one viewer starting one episode or movie. Direct play share is estimated from transcode logs."))
    post(CHANNELS["usage"], f":tv: Usage {span}: {usage_line(s)}", blocks=blocks)


# ---------- weekly downloads ----------

def seasons_label(nums):
    """[0, 1, 2, 3, 5] -> 'Specials, Seasons 1–3, 5'."""
    nums = sorted(set(nums))
    parts = ["Specials"] if 0 in nums else []
    runs = []
    for n in (n for n in nums if n):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    if runs:
        count = sum(b - a + 1 for a, b in runs)
        word = "Season" if count == 1 else "Seasons"
        parts.append(word + " " + ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs))
    return ", ".join(parts) or "?"


def cell(text, emoji=None, bold=False):
    elements = [{"type": "emoji", "name": emoji}, {"type": "text", "text": " "}] if emoji else []
    elements.append({"type": "text", "text": text, **({"style": {"bold": True}} if bold else {})})
    return {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": elements}]}


def table_block(rows):
    """rows: [(title, season, (emoji, status))]. A Slack table with a bold header row."""
    head = [cell("Title", bold=True), cell("Season", bold=True), cell("Status", bold=True)]
    body = [[{"type": "raw_text", "text": t}, {"type": "raw_text", "text": s}, cell(st, emoji=e)]
            for t, s, (e, st) in rows]
    return {"type": "table", "column_settings": [{"is_wrapped": True}] * 3, "rows": [head] + body}


def text_table(rows):
    """Fallback if Slack rejects the table block: monospace table in code blocks of < 3000 chars."""
    lines = [f"{'Title':<34} {'Season':<16} Status", "-" * 64]
    lines += [f"{t[:34]:<34} {s[:16]:<16} {UNICODE.get(e, '')} {st}" for t, s, (e, st) in rows]
    chunks, cur = [], []
    for line in lines:
        if sum(len(x) + 1 for x in cur) + len(line) > 2900:
            chunks.append(cur)
            cur = []
        cur.append(line)
    chunks.append(cur)
    return [section("```\n" + "\n".join(c) + "\n```") for c in chunks]


def weekly():
    since = NOW - datetime.timedelta(days=7)
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    movies = {m["id"]: m for m in arr("radarr", "/movie")}
    series = {s["id"]: s["title"] for s in arr("sonarr", "/series")}
    m_imp = arr("radarr", f"/history/since?date={stamp}&eventType=downloadFolderImported")
    m_fail = arr("radarr", f"/history/since?date={stamp}&eventType=downloadFailed")
    e_imp = arr("sonarr", f"/history/since?date={stamp}&eventType=downloadFolderImported&includeEpisode=true")
    e_fail = arr("sonarr", f"/history/since?date={stamp}&eventType=downloadFailed&includeEpisode=true")
    missing = arr("sonarr", "/wanted/missing?pageSize=5000&monitored=true&includeSeries=true")
    gb = sum(int(h["data"].get("size") or 0) for h in m_imp + e_imp) / 1e9
    free = shutil.disk_usage("/data").free / 1e12

    def movie_name(mid):
        m = movies.get(mid)
        return f"{m['title']} ({m.get('year')})" if m else "?"

    # Movies: imported this week, or failed this week without a later import.
    imported_ids = {h["movieId"] for h in m_imp}
    movie_rows = [(movie_name(i), "Movie", ("white_check_mark", "Imported")) for i in imported_ids]
    for i in {h["movieId"] for h in m_fail} - imported_ids:
        has_file = (movies.get(i) or {}).get("hasFile")
        movie_rows.append((movie_name(i), "Movie", ("warning", "Partial · upgrade failed, old file kept") if has_file
                           else ("x", "Download failed")))

    # Shows: one row per show. Seasons = seasons downloaded (or still missing if nothing came in).
    eps, seasons, failed = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    for h in e_imp:
        eps[h["seriesId"]].add(h["episodeId"])
        seasons[h["seriesId"]].add((h.get("episode") or {}).get("seasonNumber", 0))
    for h in e_fail:
        failed[h["seriesId"]].add((h.get("episode") or {}).get("seasonNumber", 0))
    miss, miss_seasons = collections.Counter(), collections.defaultdict(set)
    for r in missing["records"]:
        miss[r["seriesId"]] += 1
        series.setdefault(r["seriesId"], (r.get("series") or {}).get("title", "?"))
        miss_seasons[r["seriesId"]].add(r.get("seasonNumber", 0))
    show_rows = []
    for sid in set(eps) | set(failed) | set(miss):
        n, m = len(eps[sid]), miss[sid]
        if n and not m:
            status = ("white_check_mark", f"Imported · {plural(n, 'episode')}")
        elif n:
            status = ("warning", f"Partial · {n} imported, {m} missing")
        elif m:
            status = ("x", f"Not downloaded · {m} missing")
        else:
            status = ("warning", "Partial · upgrade failed, old files kept")
        label = seasons_label(seasons[sid] or failed[sid] or miss_seasons[sid])
        show_rows.append((series.get(sid, "?"), label, status))

    rows = sorted(movie_rows, key=lambda r: r[0].lower()) + sorted(show_rows, key=lambda r: r[0].lower())
    tally = collections.Counter(st[0] for _, _, st in rows)
    n_eps = sum(len(v) for v in eps.values())
    shows_in = sum(1 for v in eps.values() if v)

    summary = (f"*{plural(len(imported_ids), 'movie')}, {plural(n_eps, 'episode')} ({plural(shows_in, 'show')})* · "
               f"{gb:,.0f} GB added · {free:,.1f} TB free on `/data`\n"
               f":white_check_mark: {tally['white_check_mark']} complete   :warning: {tally['warning']} partial   "
               f":x: {tally['x']} not downloaded\n"
               f"{plural(len(m_fail) + len(e_fail), 'failed download')} this week; Sonarr and Radarr grabbed other "
               f"releases · {missing['totalRecords']} aired episodes still missing")
    try:
        reqs, page = [], 0
        while True:
            r = seerr(f"/request?take=100&skip={page * 100}&sort=added")
            batch = [x for x in r["results"] if ts(x["createdAt"]) >= since]
            reqs += batch
            if len(batch) < len(r["results"]) or page + 1 >= r["pageInfo"]["pages"]:
                break
            page += 1
        done = sum(1 for x in reqs if x["media"]["status"] == 5)
        part = sum(1 for x in reqs if x["media"]["status"] == 4)
        summary += f"\n:inbox_tray: Seerr requests: {len(reqs)} made, {done} fulfilled" + (f", {part} partly available" if part else "")
    except Exception as e:
        summary += f"\n:inbox_tray: Seerr requests: Seerr not answering ({type(e).__name__})"

    span = f"{since.astimezone(LOCAL_TZ):%b %-d} – {local_now():%b %-d}"
    text = (f":package: Weekly downloads {span}: {plural(len(imported_ids), 'movie')}, "
            f"{plural(n_eps, 'episode')}, {gb:,.0f} GB")
    legend = context("Partial = some aired episodes of the show are still missing. "
                     "Not downloaded = nothing imported this week and episodes still missing.")
    chunks = [rows[i:i + TABLE_ROWS] for i in range(0, len(rows), TABLE_ROWS)] or [[]]
    for i, chunk in enumerate(chunks):
        top = [header(f":package: Weekly downloads · {span}"), section(summary)] if i == 0 else \
              [section(f"_Table continued ({i + 1}/{len(chunks)})_")]
        tail = [legend] if i == len(chunks) - 1 else []
        table = [table_block(chunk)] if chunk else [section("_Nothing downloaded this week._")]
        post(CHANNELS["weekly"], text if i == 0 else f"{text} (continued)",
             blocks=top + table + tail, fallback=top + (text_table(chunk) if chunk else table) + tail)


# ---------- main ----------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else ""
    if "--local-hour" in sys.argv:
        hour = int(sys.argv[sys.argv.index("--local-hour") + 1])
        if local_now().hour != hour:
            return
    if mode == "test":
        for var in sorted(set(CHANNELS.values())):
            if webhook(var):
                post(var, f"Test message from slack_reports.py ({var}). Reports will arrive here.")
            else:
                log(f"no {var} in {SLACK_ENV}; nothing sent")
        return
    funcs = {"health": health, "digest": digest, "usage": usage, "weekly": weekly}
    if mode not in funcs:
        sys.exit(__doc__)
    funcs[mode]()


if __name__ == "__main__":
    main()
