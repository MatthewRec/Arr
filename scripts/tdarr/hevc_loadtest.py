#!/usr/bin/env python3
"""Measure how many remote Jellyfin viewers the server can carry.

Combines three measurements into one report:
  1. Library mix (from Tdarr's file database): which files can direct play
     under the remote bitrate cap and which need a transcode.
  2. Real playback (from Jellyfin): plays vs transcodes over the last days.
  3. GPU load test: N parallel Quick Sync transcodes with Jellyfin's ffmpeg.

viewers ~= min(GPU slots / share needing transcode, usable upload / cap)

Usage:
  hevc_loadtest.py --label baseline     run now, write a report
  hevc_loadtest.py --when-done          cron mode: only runs once Tdarr has no
                                        queued files and no non-HEVC file above
                                        4 Mbps is left in the CONVERTED
                                        libraries; then writes the final
                                        report (compared with the baseline) and
                                        removes its own crontab line.
Reports: ~/hevc-loadtest/
"""
import argparse, glob, json, os, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

HOST_IP = os.environ.get("HOST_IP", "<VM_IP>")
TDARR = f"http://{HOST_IP}:8265/api/v2"
ENV_FILE = "/docker/servarr/.env"
JF_DB = "/docker/jellyfin/config/data/data/jellyfin.db"
JF_LOGS = "/docker/jellyfin/config/log"
FFMPEG = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
OUT = os.path.expanduser("~/hevc-loadtest")
UPLOAD_MBPS = 200 * 0.8          # measured upload, 80% usable
CAPS = (4, 6)                    # remote bitrate caps to evaluate (Mbps)
EFFICIENT = ("hevc", "av1", "vp9")
PLAYABLE = ("h264",) + EFFICIENT
DAYS = 3                         # Jellyfin keeps ffmpeg logs for about this long
CONVERTED = ("/data/shows/",)     # libraries whose Tdarr flow converts to HEVC (movies are subtitle-only)


def tdarr_files():
    key = open(ENV_FILE).read().split("TDARR_API_KEY=")[1].split()[0]
    body = json.dumps({"data": {"collection": "FileJSONDB", "mode": "getAll"}}).encode()
    req = urllib.request.Request(f"{TDARR}/cruddb", data=body,
                                 headers={"x-api-key": key, "content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def library_mix(files):
    vids = [f for f in files if f.get("fileMedium") == "video" and f.get("bit_rate")]
    mbps = lambda f: f["bit_rate"] / 1e6
    out = {"video_files": len(vids),
           "efficient_codec_share": round(sum(f.get("video_codec_name") in EFFICIENT for f in vids) / len(vids), 3),
           "non_hevc_over_4mbps": sum(1 for f in vids if f.get("video_codec_name") not in EFFICIENT and mbps(f) > 4),
           "queued": sum(1 for f in files if f.get("TranscodeDecisionMaker") == "Queued"),
           "errors": sum(1 for f in files if f.get("TranscodeDecisionMaker") == "Transcode error"),
           "library_gb": round(sum(f.get("file_size", 0) for f in vids) / 1000)}
    for cap in CAPS:
        direct = lambda f: f.get("video_codec_name") in PLAYABLE and mbps(f) <= cap
        out[f"transcode_share_at_{cap}mbps"] = round(1 - sum(map(direct, vids)) / len(vids), 3)
    return out


def real_playback():
    """Plays (Jellyfin activity log) vs transcodes (ffmpeg logs) over DAYS days."""
    tmp = "/tmp/hevc_loadtest_jf.db"
    shutil.copy(JF_DB, tmp)
    if os.path.exists(JF_DB + "-wal"):
        shutil.copy(JF_DB + "-wal", tmp + "-wal")
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    db = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    plays = db.execute("select count(distinct ItemId) from ActivityLogs where Type='VideoPlayback' and DateCreated >= ?",
                       (since.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
    db.close()
    for p in (tmp, tmp + "-wal"):
        if os.path.exists(p):
            os.remove(p)
    titles = set()   # one title can create several logs (seeks, restarts)
    for p in glob.glob(f"{JF_LOGS}/FFmpeg.Transcode*"):
        if os.path.getmtime(p) >= since.timestamp():
            head = open(p, errors="replace").read(4000)
            titles.add(head.split('"Path":"', 1)[-1].split('"', 1)[0])
    return {"days": DAYS, "titles_played": plays, "titles_transcoded": len(titles),
            "observed_transcode_share": round(min(len(titles) / plays, 1), 3) if plays else None}


def sample_files(files, n=4):
    """Pick median-size 1080p sources of the most common codec (typical, not worst case)."""
    vids = [f for f in files if f.get("fileMedium") == "video" and f.get("video_resolution") == "1080p"]
    codec = max(PLAYABLE, key=lambda c: sum(f.get("video_codec_name") == c for f in vids))
    pick = sorted((f for f in vids if f.get("video_codec_name") == codec), key=lambda f: f.get("file_size", 0))
    mid = max(0, len(pick) // 2 - n // 2)
    return codec, [f["file"] for f in pick[mid:mid + n]]


def gpu_test(sources, streams, width, height):
    """Run `streams` parallel 60 s QSV transcodes, return total x-realtime."""
    cmd = lambda src, i: ["docker", "exec", "jellyfin", FFMPEG, "-hide_banner", "-nostats", "-loglevel", "error",
                          "-init_hw_device", "vaapi=va:/dev/dri/renderD128", "-init_hw_device", "qsv=qs@va",
                          "-filter_hw_device", "qs", "-hwaccel", "qsv", "-hwaccel_output_format", "qsv",
                          "-ss", str(300 + i * 30), "-t", "60", "-i", src, "-map", "0:v:0",
                          "-vf", f"vpp_qsv=w={width}:h={height}:format=nv12", "-c:v", "h264_qsv",
                          "-preset", "veryfast", "-b:v", "4M", "-maxrate", "4M", "-bufsize", "8M",
                          "-an", "-f", "null", "-"]
    start = time.time()
    procs = [subprocess.Popen(cmd(sources[i % len(sources)], i), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) for i in range(streams)]
    ok = all(p.wait() == 0 for p in procs)
    return round(streams * 60 / (time.time() - start), 1), ok


def viewers(slots, share, cap):
    by_gpu = slots / share if share else float("inf")
    return int(min(by_gpu, UPLOAD_MBPS / cap))


def run(label):
    files = tdarr_files()
    mix, real = library_mix(files), real_playback()
    codec, sources = sample_files(files)
    slots = {}
    for h, w in ((1080, 1920), (720, 1280)):
        x, ok = gpu_test(sources, 8, w, h)
        slots[f"{h}p"] = {"x_realtime_total": x, "slots": x, "all_ok": ok}
    res = {"label": label, "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "library": mix, "real_playback": real, "gpu_sample_codec": codec, "gpu": slots}
    res["viewers"] = {f"cap_{cap}mbps_{h}": viewers(slots[h]["slots"], mix[f"transcode_share_at_{cap}mbps"], cap)
                      for cap in CAPS for h in ("1080p", "720p")}
    os.makedirs(OUT, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    json.dump(res, open(f"{OUT}/{label}-{stamp}.json", "w"), indent=1)
    return res


def report(final, base):
    lines = [f"# HEVC load test, {final['when']}", ""]
    rows = [("Video files", "library", "video_files"),
            ("Share in HEVC/AV1/VP9", "library", "efficient_codec_share"),
            ("Non-HEVC files above 4 Mbps", "library", "non_hevc_over_4mbps"),
            ("Library size (GB)", "library", "library_gb"),
            ("Share needing transcode, 4 Mbps cap", "library", "transcode_share_at_4mbps"),
            ("Share needing transcode, 6 Mbps cap", "library", "transcode_share_at_6mbps"),
            (f"Titles played, last {DAYS} days", "real_playback", "titles_played"),
            (f"Titles transcoded, last {DAYS} days", "real_playback", "titles_transcoded"),
            ("Observed transcode share", "real_playback", "observed_transcode_share")]
    lines += ["| Metric | Before | After |", "| --- | --- | --- |"]
    for name, sect, key in rows:
        lines.append(f"| {name} | {base[sect].get(key) if base else '-'} | {final[sect].get(key)} |")
    for h in ("1080p", "720p"):
        lines.append(f"| GPU slots {h} | {base['gpu'][h]['slots'] if base else '-'} | {final['gpu'][h]['slots']} |")
    for k in final["viewers"]:
        lines.append(f"| Viewers {k} | {base['viewers'][k] if base else '-'} | {final['viewers'][k]} |")
    path = f"{OUT}/report-{datetime.now():%Y%m%d}.md"
    open(path, "w").write("\n".join(lines) + "\n")
    return path


def conversion_done(files):
    """No queued files, and every file left to convert in a CONVERTED library has errored."""
    if any(f.get("TranscodeDecisionMaker") == "Queued" for f in files):
        return False
    left = [f for f in files if f.get("file", "").startswith(CONVERTED) and f.get("fileMedium") == "video"
            and (f.get("bit_rate") or 0) > 4e6 and f.get("video_codec_name") not in EFFICIENT]
    return all(f.get("TranscodeDecisionMaker") == "Transcode error" for f in left)


def remove_cron_line():
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    new = "".join(l + "\n" for l in cur.splitlines() if "hevc_loadtest.py" not in l)
    subprocess.run(["crontab", "-"], input=new, text=True, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="manual")
    ap.add_argument("--when-done", action="store_true")
    a = ap.parse_args()
    if a.when_done:
        if not conversion_done(tdarr_files()):
            print(f"{datetime.now():%F %T} conversion not finished yet")
            return
        final = run("final")
        bases = sorted(glob.glob(f"{OUT}/baseline-*.json"))
        print("report:", report(final, json.load(open(bases[-1])) if bases else None))
        remove_cron_line()
        return
    res = run(a.label)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    sys.exit(main())
