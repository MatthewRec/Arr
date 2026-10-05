#!/usr/bin/env python3
"""Throttled missing-episode search for Sonarr. Run from cron every 10 minutes.

Why: Sonarr runs at most 3 commands at once. A full-series search on a long
anime searches episode by episode and can hold a slot for hours, so a few of
them block download tracking and imports ("frozen" Activity page).

What it does on each run:
  * Counts search commands already queued or running. If MAX_SEARCHES or more,
    do nothing. Otherwise send up to (MAX_SEARCHES - busy) new searches.
  * Picks missing, monitored, aired episodes that are not in the download queue
    and were not searched in the last RETRY_HOURS hours.
  * Priority: series named in PRIORITY, then series added in the last
    NEW_SERIES_HOURS hours, newest request first, then never-searched episodes, then
    the rest.
  * For a standard (non-anime) series with a whole aired season missing, sends
    one SeasonSearch (season packs: 1 query instead of one per episode).
    Everything else goes in an EpisodeSearch of up to BATCH episodes.
  * Warns in the log if download tracking (RefreshMonitoredDownloads) has been
    waiting longer than STALL_MINUTES.

With MAX_SEARCHES = 2, one of Sonarr's 3 command slots always stays free for
imports and refreshes. Seerr's automatic search for Sonarr is turned off; this
script does the searching.
"""
import collections
import datetime
import json
import re
import sys
import urllib.request

SONARR = "http://<VM_IP>:8989/api/v3"
CONFIG = "/docker/servarr/sonarr/config.xml"
MAX_SEARCHES = 2          # Sonarr has 3 command slots; keep 1 free
BATCH = 10                # episodes per EpisodeSearch
RETRY_HOURS = 24
NEW_SERIES_HOURS = 48
STALL_MINUTES = 15
PRIORITY = []   

KEY = re.search(r"<ApiKey>([^<]+)", open(CONFIG).read()).group(1)
NOW = datetime.datetime.now(datetime.timezone.utc)
DRY_RUN = "--dry-run" in sys.argv   # show what would be sent, send nothing


def api(path, body=None):
    if body is not None and DRY_RUN:
        return {}
    req = urllib.request.Request(SONARR + path, headers={"X-Api-Key": KEY, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req, timeout=60))


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def log(msg):
    print(f"{NOW:%Y-%m-%d %H:%M} {msg}", flush=True)


def label(e):
    return f"{e['series']['title'][:25]} S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}"


commands = api("/command")
active = [c for c in commands if c["status"] in ("queued", "started")]

for c in active:
    if c["name"] == "RefreshMonitoredDownloads" and c["status"] == "queued":
        waited = (NOW - ts(c["queued"])).total_seconds() / 60
        if waited > STALL_MINUTES:
            log(f"WARNING download tracking queued for {waited:.0f} min; command slots are blocked")

busy = [c for c in active if "Search" in c["name"]]
free = MAX_SEARCHES - len(busy)
if DRY_RUN and free <= 0:
    free = MAX_SEARCHES   # dry run: show what would be picked once slots free up
if free <= 0:
    log(f"busy: {len(busy)} search command(s) queued/running, skipping")
    raise SystemExit(0)

series = {s["id"]: s for s in api("/series")}
in_queue = {r["episodeId"] for r in api("/queue?pageSize=2000").get("records", []) if r.get("episodeId")}
missing = api("/wanted/missing?pageSize=5000&monitored=true&includeSeries=true")["records"]
cutoff = NOW - datetime.timedelta(hours=RETRY_HOURS)
new_cutoff = NOW - datetime.timedelta(hours=NEW_SERIES_HOURS)
# Episodes and seasons already covered by a queued/running search.
busy_eps = {i for c in busy for i in (c.get("body", {}).get("episodeIds") or [])}
busy_seasons = {(c["body"].get("seriesId"), c["body"].get("seasonNumber")) for c in busy if c["name"] == "SeasonSearch"}
busy_series = {c["body"].get("seriesId") for c in busy if c["name"] in ("SeriesSearch", "MissingEpisodeSearch")}
due = [e for e in missing
       if e["id"] not in in_queue and e["id"] not in busy_eps
       and (e["seriesId"], e["seasonNumber"]) not in busy_seasons and e["seriesId"] not in busy_series
       and (not e.get("lastSearchTime") or ts(e["lastSearchTime"]) < cutoff)]
if not due:
    log(f"nothing due ({len(missing)} missing, all searched in the last {RETRY_HOURS} h or downloading)")
    raise SystemExit(0)


def rank(e):
    s = series[e["seriesId"]]
    added = ts(s["added"])
    return (s["title"] not in PRIORITY,
            added < new_cutoff,
            -added.timestamp() if added >= new_cutoff else 0,   # newest request first
            e.get("lastSearchTime") is not None,
            e.get("lastSearchTime") or "",
            e["seriesId"], e["seasonNumber"], e["episodeNumber"])


due.sort(key=rank)

# Whole aired seasons missing on standard series -> one SeasonSearch each.
by_season = collections.defaultdict(list)
for e in due:
    by_season[(e["seriesId"], e["seasonNumber"])].append(e)
whole = set()
for (sid, season), eps in by_season.items():
    s = series[sid]
    if s["seriesType"] != "standard" or season == 0:
        continue
    stats = next((x.get("statistics") or {} for x in s["seasons"] if x["seasonNumber"] == season), {})
    aired = stats.get("episodeCount", 0)
    if aired and stats.get("episodeFileCount", 0) == 0 and len(eps) == aired:
        whole.add((sid, season))

# Build the work list in priority order: a whole season counts as one item,
# other episodes are grouped into batches of BATCH.
work, batch, done_seasons = [], [], set()
for e in due:
    key = (e["seriesId"], e["seasonNumber"])
    if key in whole:
        if key not in done_seasons:
            done_seasons.add(key)
            work.append(("season", key))
        continue
    batch.append(e)
    if len(batch) == BATCH:
        work.append(("episodes", batch))
        batch = []
if batch:
    work.append(("episodes", batch))

sent = []
for kind, item in work[:free]:
    if kind == "season":
        sid, season = item
        api("/command", {"name": "SeasonSearch", "seriesId": sid, "seasonNumber": season})
        sent.append(f"SeasonSearch {series[sid]['title'][:25]} S{season:02d} ({len(by_season[item])} eps)")
    else:
        api("/command", {"name": "EpisodeSearch", "episodeIds": [x["id"] for x in item]})
        sent.append(f"EpisodeSearch {len(item)} eps: " + ", ".join(label(x) for x in item))

log(("DRY RUN " if DRY_RUN else "") + f"{len(due)} due ({len(missing)} missing), {len(busy)} busy, sent {len(sent)}: " + " | ".join(sent))
