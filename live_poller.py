"""Write live.json for The Cottage counter boards: counter wait times from Square, and the
Blackpool weather. Runs every five minutes as a GitHub Actions job (see the cottage-board-live
repo) or anywhere with Python 3.9+. No third-party packages.

Environment:
  SQUARE_ACCESS_TOKEN   Square production access token with ORDERS_READ. Omit to skip counter.
  SQUARE_LOCATION_ID    The takeaway location (default: the shop, L6NP58MH414CM).
  WEATHER_PROVIDER      "metoffice" (default) or "open-meteo".
  MET_OFFICE_API_KEY    Met Office DataHub site-specific key. Omit to skip weather.
  COUNTER_EXCLUDE_SOURCES  Comma-separated order source names to ignore (default below).

Usage:
  python live_poller.py --out live.json
  python live_poller.py --inspect      # print recent orders' timestamps and sources, no write

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
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
SHOP_LAT, SHOP_LON = 53.803134, -3.025281   # 31 Newhouse Road, Blackpool FY4 4JH
SQUARE_VERSION = "2025-01-23"
NOW_WINDOW_MIN = 30
MIN_WAIT_SEC, MAX_WAIT_SEC = 30, 90 * 60      # outside this a ticket was not a real counter wait
DEFAULT_EXCLUDE = ["Square Online", "Square Kiosk", "Kiosk", "Deliveroo", "Just Eat", "Uber Eats"]


def http_json(url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ Square ----
def square_orders(token, location, since_utc):
    headers = {"Authorization": f"Bearer {token}", "Square-Version": SQUARE_VERSION,
               "Content-Type": "application/json"}
    body = {
        "location_ids": [location],
        "query": {
            "filter": {"state_filter": {"states": ["COMPLETED", "OPEN"]},
                       "date_time_filter": {"updated_at": {"start_at": since_utc.isoformat()}}},
            "sort": {"sort_field": "UPDATED_AT", "sort_order": "DESC"},
        },
        "limit": 500, "return_entries": False,
    }
    orders, cursor = [], None
    for _ in range(10):
        if cursor:
            body["cursor"] = cursor
        res = http_json("https://connect.squareup.com/v2/orders/search", headers, body)
        orders += res.get("orders", [])
        cursor = res.get("cursor")
        if not cursor:
            break
    return orders


def order_wait(o):
    """(start, end, end_field) for one order, or None if it has no usable handover time."""
    start = parse_ts(o.get("created_at"))
    end, field = None, None
    for f in o.get("fulfillments", []) or []:
        pd = f.get("pickup_details") or {}
        for key in ("ready_at", "picked_up_at"):
            t = parse_ts(pd.get(key))
            if t and (end is None or t < end):
                end, field = t, key
    if end is None and o.get("state") == "COMPLETED":
        end, field = parse_ts(o.get("closed_at")), "closed_at"
    if not start or not end:
        return None
    return start, end, field


def counter_stats(orders, exclude, now_utc):
    excl = {s.strip().lower() for s in exclude if s.strip()}
    rows = []
    for o in orders:
        src = ((o.get("source") or {}).get("name") or "").strip()
        if src.lower() in excl:
            continue
        w = order_wait(o)
        if not w:
            continue
        start, end, field = w
        sec = (end - start).total_seconds()
        if sec < MIN_WAIT_SEC or sec > MAX_WAIT_SEC:
            continue
        rows.append((end, sec, field))

    today = now_utc.astimezone(LONDON).date()
    today_rows = [(e, s) for e, s, _ in rows if e.astimezone(LONDON).date() == today]
    now_rows = [s for e, s in today_rows if e >= now_utc - timedelta(minutes=NOW_WINDOW_MIN)]

    buckets = {}
    for e, s in today_rows:
        loc = e.astimezone(LONDON)
        key = loc.replace(minute=0 if loc.minute < 30 else 30, second=0, microsecond=0)
        buckets.setdefault(key, []).append(s)
    by_half_hour = [{"t": k.strftime("%H:%M"), "p50_sec": int(statistics.median(v))}
                    for k, v in sorted(buckets.items()) if len(v) >= 2]

    fields = {}
    for _, _, f in rows:
        fields[f] = fields.get(f, 0) + 1
    return {
        "window_minutes": NOW_WINDOW_MIN,
        "sample": len(now_rows),
        "wait_now_p50_sec": int(statistics.median(now_rows)) if now_rows else None,
        "wait_today_p50_sec": int(statistics.median([s for _, s in today_rows])) if today_rows else None,
        "by_half_hour": by_half_hour,
        "end_fields": fields,   # which timestamp stood for "handover"; for checking, not display
    }


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
    if token:
        try:
            midnight = now_utc.astimezone(LONDON).replace(hour=0, minute=0, second=0, microsecond=0)
            orders = square_orders(token, location, midnight.astimezone(timezone.utc))
            if args.inspect:
                srcs = {}
                for o in orders:
                    s_ = (o.get("source") or {}).get("name") or "(none)"
                    srcs[s_] = srcs.get(s_, 0) + 1
                print("sources today:", srcs)
                for o in orders[:12]:
                    w = order_wait(o)
                    print(o.get("id"), o.get("state"), (o.get("source") or {}).get("name"),
                          "created", o.get("created_at"), "closed", o.get("closed_at"),
                          "fulfilment", [(f.get("type"), f.get("state"),
                                          {k: v for k, v in (f.get("pickup_details") or {}).items() if k != "recipient"})
                                         for f in o.get("fulfillments", []) or []],
                          "-> wait", f"{(w[1] - w[0]).total_seconds():.0f}s via {w[2]}" if w else None)
                return False
            out["counter"] = counter_stats(orders, exclude, now_utc)
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as e:
            print("square failed:", e, file=sys.stderr)
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
    ap.add_argument("--inspect", action="store_true", help="print recent Square orders (timestamps and sources only, no customer details) and exit")
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
