"""Write live.json for The Cottage counter boards: counter wait times from the Square kitchen
display (KDS) via Square's Reporting API, and the Blackpool weather. Runs every five minutes as a GitHub Actions job (see the cottage-board-live
repo) or anywhere with Python 3.9+. No third-party packages.

Environment:
  SQUARE_ACCESS_TOKEN   Square production access token with REPORTING_READ. Omit to skip counter.
  SQUARE_LOCATION_ID    The takeaway location (default: the shop, L6NP58MH414CM).
  KDS_STATION_NAME      KDS station whose bump marks handover (default "Counter Expeditor").
  WEATHER_PROVIDER      "metoffice" (default) or "open-meteo".
  MET_OFFICE_API_KEY    Met Office DataHub site-specific key. Omit to skip weather.
  COUNTER_EXCLUDE_SOURCES  Comma-separated order source names to ignore (default below).

Usage:
  python live_poller.py --out live.json
  python live_poller.py --inspect      # print today's station tickets (times and sources only), no write

Counter wait = time from a ticket appearing on the Counter Expeditor screen to the expeditor
bumping it (Chris, 2026-10-31: the KDS data, not the till). That comes from the Reporting API
cube KitchenPerformanceTickets (via the KDS view), which lags the screen by up to ~15 minutes,
so `as_of` carries the latest bump seen and the "now" window ends there, not at the clock.

Counter figures are counter-only and never include volumes: no order counts or portions sold
are written anywhere (Chris, 2026-10-30). `sample` is carried only so the board can suppress
the panel when the figure is based on too few orders.
"""
import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
SHOP_LAT, SHOP_LON = 53.803134, -3.025281   # 31 Newhouse Road, Blackpool FY4 4JH
REPORTING_URL = "https://connect.squareup.com/reporting/v1/load"
DEFAULT_STATION = "Counter Expeditor"
NOW_WINDOW_MIN = 30
NOW_STALE_MIN = 45        # no bump seen for this long (cube lag included): the 'now' figure is withheld
MIN_WAIT_SEC, MAX_WAIT_SEC = 30, 90 * 60      # outside this a ticket was not a real counter wait
DEFAULT_EXCLUDE = ["Square Online", "Deliveroo", "Just Eat", "Uber Eats"]   # kiosk orders are collected at the counter and go through the KDS


def http_json(url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def parse_ts(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ Square ----
def kds_tickets(token, location, station, since_utc, until_utc):
    """Completed tickets on one KDS station, from Square's Reporting API (the KDS cube).

    Returns [(displayed_at, bumped_at, source)] with UTC datetimes, newest first. One row per
    chit: the KDS view can repeat a ticket per line item, so rows are de-duplicated on chit_id.
    Time dimensions in the Reporting API are UTC and arrive without a zone suffix.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    query = {
        "dimensions": ["KDS.chit_id", "KDS.order_source", "KDS.display_on_kds_at", "KDS.actual_completed_at"],
        "timeDimensions": [{"dimension": "KDS.actual_completed_at", "dateRange": [fmt(since_utc), fmt(until_utc)]}],
        "filters": [{"member": "KDS.location_id", "operator": "equals", "values": [location]},
                    {"member": "KDS.device_code_name", "operator": "equals", "values": [station]}],
        "order": [["KDS.actual_completed_at", "desc"]],
        "limit": 1000,
    }
    res = {}
    for _ in range(10):
        res = http_json(REPORTING_URL, headers, {"query": query}, timeout=90)
        if res.get("error") == "Continue wait":   # long query still running server-side
            time.sleep(3)
            continue
        break
    if res.get("error"):
        raise ValueError(f"reporting api: {res.get('error')}")
    seen, out = set(), []
    for r in res.get("data", []):
        chit = r.get("KDS.chit_id")
        if chit in seen:
            continue
        seen.add(chit)
        shown, bumped = parse_ts(r.get("KDS.display_on_kds_at")), parse_ts(r.get("KDS.actual_completed_at"))
        if shown and bumped:
            out.append((shown, bumped, (r.get("KDS.order_source") or "").strip()))
    return out


def counter_stats(tickets, exclude, now_utc, station):
    excl = {s.strip().lower() for s in exclude if s.strip()}
    rows = []
    for shown, bumped, src in tickets:
        if src.lower() in excl:
            continue
        sec = (bumped - shown).total_seconds()
        if sec < MIN_WAIT_SEC or sec > MAX_WAIT_SEC:
            continue
        rows.append((bumped, sec))

    today = now_utc.astimezone(LONDON).date()
    today_rows = [(e, s) for e, s in rows if e.astimezone(LONDON).date() == today]
    # The cube lags the screen by up to ~15 min, so "now" is the 30 min up to the latest bump seen.
    as_of = max((e for e, _ in today_rows), default=None)
    now_rows = [s for e, s in today_rows if as_of and e >= as_of - timedelta(minutes=NOW_WINDOW_MIN)]
    # The boards check the file's age, not as_of, so withhold a 'now' figure once the latest bump
    # is too old to describe the counter right now (quiet spell, closed, or the cube is behind).
    if as_of and now_utc - as_of > timedelta(minutes=NOW_STALE_MIN):
        now_rows = []

    buckets = {}
    for e, s in today_rows:
        loc = e.astimezone(LONDON)
        key = loc.replace(minute=0 if loc.minute < 30 else 30, second=0, microsecond=0)
        buckets.setdefault(key, []).append(s)
    by_half_hour = [{"t": k.strftime("%H:%M"), "p50_sec": int(statistics.median(v))}
                    for k, v in sorted(buckets.items()) if len(v) >= 2]

    return {
        "station": station,
        "as_of": as_of.isoformat(timespec="seconds") if as_of else None,
        "window_minutes": NOW_WINDOW_MIN,
        "sample": len(now_rows),
        "wait_now_p50_sec": int(statistics.median(now_rows)) if now_rows else None,
        "wait_today_p50_sec": int(statistics.median([s for _, s in today_rows])) if today_rows else None,
        "by_half_hour": by_half_hour,
    }


def inspect_tickets(tickets, station, now_utc):
    """--inspect: times and sources only. No names, ids or money."""
    srcs = {}
    for _, _, s in tickets:
        srcs[s or "(none)"] = srcs.get(s or "(none)", 0) + 1
    print(f"{len(tickets)} tickets on '{station}' today; sources: {srcs}; now {now_utc.isoformat(timespec='seconds')}")
    for shown, bumped, s in tickets[:25]:
        print(f"  {s:14} shown {shown.strftime('%H:%M:%S')}  bumped {bumped.strftime('%H:%M:%S')}  {int((bumped - shown).total_seconds()):>5}s")


# ----------------------------------------------------------------- weather ----
MET = {0: ("moon", "Clear"), 1: ("sun", "Sunny"), 2: ("partly-night", "Partly cloudy"), 3: ("partly", "Partly cloudy"),
       5: ("fog", "Mist"), 6: ("fog", "Fog"), 7: ("cloud", "Cloudy"), 8: ("cloud", "Overcast"),
       9: ("showers", "Light showers"), 10: ("showers", "Light showers"), 11: ("rain", "Drizzle"), 12: ("rain", "Light rain"),
       13: ("showers", "Heavy showers"), 14: ("showers", "Heavy showers"), 15: ("rain", "Heavy rain"),
       16: ("snow", "Sleet showers"), 17: ("snow", "Sleet showers"), 18: ("snow", "Sleet"),
       19: ("rain", "Hail showers"), 20: ("rain", "Hail showers"), 21: ("rain", "Hail"),
       22: ("snow", "Light snow showers"), 23: ("snow", "Light snow showers"), 24: ("snow", "Light snow"),
       25: ("snow", "Heavy snow showers"), 26: ("snow", "Heavy snow showers"), 27: ("snow", "Heavy snow"),
       28: ("thunder", "Thundery showers"), 29: ("thunder", "Thundery showers"), 30: ("thunder", "Thunder")}
WET_ICONS = {"rain", "showers", "snow", "thunder"}


def wmo(code, is_day):
    if code == 0: return ("sun" if is_day else "moon", "Clear")
    if code in (1, 2): return ("partly" if is_day else "partly-night", "Partly cloudy")
    if code == 3: return ("cloud", "Overcast")
    if code in (45, 48): return ("fog", "Fog")
    if code in (51, 53, 55, 56, 57): return ("rain", "Drizzle")
    if code in (61, 66): return ("rain", "Light rain")
    if code == 63: return ("rain", "Rain")
    if code in (65, 67): return ("rain", "Heavy rain")
    if code in (71, 73, 75, 77): return ("snow", "Snow")
    if code in (80, 81): return ("showers", "Showers")
    if code == 82: return ("showers", "Heavy showers")
    if code in (85, 86): return ("snow", "Snow showers")
    if code >= 95: return ("thunder", "Thunder")
    return ("cloud", "Cloudy")


COMPASS = [("N", "north"), ("NNE", "north-north-east"), ("NE", "north-east"), ("ENE", "east-north-east"),
           ("E", "east"), ("ESE", "east-south-east"), ("SE", "south-east"), ("SSE", "south-south-east"),
           ("S", "south"), ("SSW", "south-south-west"), ("SW", "south-west"), ("WSW", "west-south-west"),
           ("W", "west"), ("WNW", "west-north-west"), ("NW", "north-west"), ("NNW", "north-north-west")]


def compass(deg):
    i = int((deg / 22.5) + 0.5) % 16
    return COMPASS[i]


def clock12(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}{'pm' if dt.hour >= 12 else 'am'}"


def hour12(dt):
    h = dt.hour % 12 or 12
    return f"{h}{'pm' if dt.hour >= 12 else 'am'}"


def sunset_local(date, lat, lon):
    """NOAA sunrise/sunset approximation; good to a minute or two."""
    n = date.timetuple().tm_yday
    lng_hour = lon / 15
    t = n + ((18 - lng_hour) / 24)
    m = (0.9856 * t) - 3.289
    L = (m + (1.916 * math.sin(math.radians(m))) + (0.020 * math.sin(math.radians(2 * m))) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
    ra = (ra + (math.floor(L / 90) * 90) - (math.floor(ra / 90) * 90)) / 15
    sin_dec = 0.39782 * math.sin(math.radians(L))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(90.833)) - (sin_dec * math.sin(math.radians(lat)))) / (cos_dec * math.cos(math.radians(lat)))
    if cos_h < -1 or cos_h > 1:
        return None
    h = math.degrees(math.acos(cos_h)) / 15
    T = h + ra - (0.06571 * t) - 6.622
    ut = (T - lng_hour) % 24
    utc = datetime(date.year, date.month, date.day, tzinfo=timezone.utc) + timedelta(hours=ut)
    return utc.astimezone(LONDON)


def derive_line(now_local, now_icon, hours, temp_c, sunset):
    """Short line for Board 1, long line for Board 2. hours: [(local_dt, icon, rain_pct)]."""
    wet = lambda icon, pct: icon in WET_ICONS or (pct is not None and pct >= 50)
    raining = now_icon in WET_ICONS
    upcoming = [(dt, wet(ic, pc)) for dt, ic, pc in hours[:8]]
    line = None
    if raining:
        for i, (dt, w) in enumerate(upcoming):
            if not w and all(not x for _, x in upcoming[i:i + 2]):
                line = f"Dry from {hour12(dt)}"
                break
        line = line or "Rain for a while yet"
    else:
        for dt, w in upcoming[:4]:
            if w:
                line = f"Rain from {hour12(dt)}"
                break
        line = line or ("Dry this evening" if now_local.hour >= 15 else "Dry for a few hours")
    if sunset and sunset > now_local:
        line += f" · sunset {clock12(sunset)}"
    long = line
    dry_soon = not raining and all(not w for _, w in upcoming[:2])
    if dry_soon and temp_c >= 10 and sunset and now_local < sunset + timedelta(minutes=30):
        long += ". Eat them on the prom?"
    return line, long


def weather_metoffice(key, now_utc):
    url = (f"https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point/hourly"
           f"?latitude={SHOP_LAT}&longitude={SHOP_LON}&includeLocationName=true")
    res = http_json(url, {"apikey": key, "accept": "application/json"})
    series = res["features"][0]["properties"]["timeSeries"]
    rows = []
    for p in series:
        t = parse_ts(p.get("time"))
        if not t:
            continue
        icon, desc = MET.get(int(p.get("significantWeatherCode", 7)), ("cloud", "Cloudy"))
        rows.append({"t": t, "icon": icon, "desc": desc, "temp": p.get("screenTemperature"),
                     "feels": p.get("feelsLikeTemperature"), "wind_ms": p.get("windSpeed10m"),
                     "wind_deg": p.get("windDirectionFrom10m"), "rain_pct": p.get("probOfPrecipitation")})
    rows.sort(key=lambda r: r["t"])
    past = [r for r in rows if r["t"] <= now_utc] or rows[:1]
    cur = past[-1]
    future = [r for r in rows if r["t"] > now_utc]
    return build_weather(cur, future, now_utc, cur["wind_ms"] * 2.23694 if cur["wind_ms"] is not None else None)


def weather_open_meteo(now_utc):
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={SHOP_LAT}&longitude={SHOP_LON}"
           "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,is_day"
           "&hourly=temperature_2m,weather_code,precipitation_probability,is_day"
           "&timezone=Europe%2FLondon&wind_speed_unit=mph&forecast_days=2")
    res = http_json(url)
    c = res["current"]
    icon, desc = wmo(int(c["weather_code"]), bool(c.get("is_day", 1)))
    cur = {"t": now_utc, "icon": icon, "desc": desc, "temp": c["temperature_2m"], "feels": c.get("apparent_temperature"),
           "wind_deg": c.get("wind_direction_10m"), "rain_pct": None}
    h = res["hourly"]
    future = []
    for i, ts in enumerate(h["time"]):
        t = datetime.fromisoformat(ts).replace(tzinfo=LONDON)
        if t <= now_utc:
            continue
        ic, ds = wmo(int(h["weather_code"][i]), bool(h["is_day"][i]))
        future.append({"t": t, "icon": ic, "desc": ds, "temp": h["temperature_2m"][i], "rain_pct": h["precipitation_probability"][i]})
    return build_weather(cur, future, now_utc, c.get("wind_speed_10m"))


def build_weather(cur, future, now_utc, wind_mph):
    now_local = now_utc.astimezone(LONDON)
    sunset = sunset_local(now_local.date(), SHOP_LAT, SHOP_LON)
    short, long = derive_line(now_local, cur["icon"], [(r["t"].astimezone(LONDON), r["icon"], r.get("rain_pct")) for r in future],
                              cur["temp"] or 0, sunset)
    hours = [{"t": hour12(r["t"].astimezone(LONDON)), "icon": r["icon"], "temp_c": round(r["temp"]),
              "rain_pct": int(r["rain_pct"]) if r.get("rain_pct") is not None else None} for r in future[:6]]
    d = compass(cur["wind_deg"]) if cur.get("wind_deg") is not None else ("", "")
    return {
        "fetched_at": now_utc.isoformat(timespec="seconds"),
        "temp_c": round(cur["temp"]), "feels_c": round(cur["feels"]) if cur.get("feels") is not None else None,
        "desc": cur["desc"], "icon": cur["icon"],
        "wind_mph": round(wind_mph) if wind_mph is not None else None, "wind_dir": d[0], "wind_dir_long": d[1],
        "hours": hours, "sunset": clock12(sunset) if sunset else None, "line": short, "line_long": long,
    }


# -------------------------------------------------------------------- main ----
def within_hours(spec, now_utc):
    if not spec:
        return True
    lo, hi = (int(x) for x in spec.split("-"))
    return lo <= now_utc.hour < hi


def git_push(path):
    """Commit and push the file if it changed. Quietly tolerates a transient push failure."""
    import subprocess
    run = lambda *cmd: subprocess.run(cmd, capture_output=True, text=True)
    run("git", "add", path)
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        print("no change", file=sys.stderr); return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    run("git", "commit", "-q", "-m", f"live {stamp}")
    run("git", "pull", "--rebase", "-q")
    r = run("git", "push", "-q")
    print("pushed" if r.returncode == 0 else "push failed: " + r.stderr[-200:], file=sys.stderr)


def cycle(args):
    now_utc = datetime.now(timezone.utc)
    out = {"generated_at": now_utc.isoformat(timespec="seconds"), "counter": None, "weather": None}

    token = os.environ.get("SQUARE_ACCESS_TOKEN")
    location = os.environ.get("SQUARE_LOCATION_ID", "L6NP58MH414CM")
    exclude = os.environ.get("COUNTER_EXCLUDE_SOURCES")
    exclude = exclude.split(",") if exclude else DEFAULT_EXCLUDE
    station = os.environ.get("KDS_STATION_NAME", DEFAULT_STATION)
    if token:
        try:
            midnight = now_utc.astimezone(LONDON).replace(hour=0, minute=0, second=0, microsecond=0)
            tickets = kds_tickets(token, location, station, midnight.astimezone(timezone.utc), now_utc)
            if args.inspect:
                inspect_tickets(tickets, station, now_utc)
                return False
            out["counter"] = counter_stats(tickets, exclude, now_utc, station)
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as e:
            body = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else ""
            print("square failed:", e, body, file=sys.stderr)
    else:
        print("SQUARE_ACCESS_TOKEN not set; counter skipped", file=sys.stderr)

    provider = os.environ.get("WEATHER_PROVIDER", "metoffice").lower()
    try:
        if provider == "open-meteo":
            out["weather"] = weather_open_meteo(now_utc)
        elif os.environ.get("MET_OFFICE_API_KEY"):
            out["weather"] = weather_metoffice(os.environ["MET_OFFICE_API_KEY"], now_utc)
        else:
            print("MET_OFFICE_API_KEY not set; weather skipped", file=sys.stderr)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, IndexError) as e:
        print("weather failed:", e, file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", args.out, "counter" if out["counter"] else "-", "weather" if out["weather"] else "-",
          datetime.now(timezone.utc).strftime("%H:%M:%SZ"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="live.json")
    ap.add_argument("--inspect", action="store_true", help="print today's KDS station tickets (times and sources only, no customer details) and exit")
    ap.add_argument("--loop", type=int, default=0, help="seconds between cycles; 0 runs once")
    ap.add_argument("--hours", default="", help="UTC hour range to work in, e.g. 9-23; the loop idles outside it")
    ap.add_argument("--max-minutes", type=int, default=330, help="stop the loop after this long (GitHub jobs are capped at 6 h)")
    ap.add_argument("--push", action="store_true", help="git commit and push --out after each write")
    args = ap.parse_args()

    import time
    if not args.loop:
        if cycle(args) and args.push:
            git_push(args.out)
        return
    started = time.time()
    while time.time() - started < args.max_minutes * 60:
        if within_hours(args.hours, datetime.now(timezone.utc)):
            try:
                if cycle(args) and args.push:
                    git_push(args.out)
            except Exception as e:   # never let one bad cycle end the loop
                print("cycle failed:", e, file=sys.stderr)
        else:
            print("outside hours, idling", file=sys.stderr)
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
