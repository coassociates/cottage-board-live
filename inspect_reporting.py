"""One-off: pin down sort syntax and freshness for Counter Expeditor tickets."""
import json, os, urllib.request, urllib.error, time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ["SQUARE_ACCESS_TOKEN"]; LOC = os.environ.get("SQUARE_LOCATION_ID", "L6NP58MH414CM")
BASE = "https://connect.squareup.com/reporting"; HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
now = datetime.now(timezone.utc); today = str(now.astimezone(ZoneInfo("Europe/London")).date()); yday = str(now.astimezone(ZoneInfo("Europe/London")).date() - timedelta(days=1))
T = "KitchenPerformanceTickets"

def load(q):
    for _ in range(10):
        req = urllib.request.Request(BASE + "/v1/load", data=json.dumps({"query": q}).encode(), headers=HDR, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as r: res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e: return e.code, e.read().decode()[:600]
        if res.get("error") == "Continue wait": time.sleep(4); continue
        return 200, res
    return 0, "timed out"

def show(label, q, n=8):
    print(f"\n=== {label}\n{json.dumps(q)}"); st, res = load(q)
    if st != 200: print("->", st, res); return None
    rows = res.get("data", []); print(f"-> 200, {len(rows)} rows")
    for r in rows[:n]: print("  ", {k.split('.')[-1]: (v[-6:] if isinstance(v, str) and k.endswith('_id') else v) for k, v in r.items()})
    return rows

base_dims = [f"{T}.station_type", f"{T}.device_id", f"{T}.order_source", f"{T}.display_on_kds_at", f"{T}.actual_completed_at"]
flt = [{"member": f"{T}.location_id", "operator": "equals", "values": [LOC]}]
td = [{"dimension": f"{T}.actual_completed_at", "dateRange": [yday, today]}]

show("1. order as array", {"dimensions": base_dims, "timeDimensions": td, "filters": flt, "order": [[f"{T}.actual_completed_at", "desc"]], "limit": 5})
show("2. order as object", {"dimensions": base_dims, "timeDimensions": td, "filters": flt, "order": {f"{T}.actual_completed_at": "desc"}, "limit": 5})
show("3. dimensions-only with local_date filter, no order", {"dimensions": base_dims, "filters": flt + [{"member": f"{T}.local_date", "operator": "equals", "values": [today]}], "limit": 5})
show("4. segment expo_tickets + device filter", {"dimensions": base_dims, "timeDimensions": td, "filters": flt + [{"member": f"{T}.device_id", "operator": "endsWith", "values": ["58XYZ8"]}], "segments": [f"{T}.expo_tickets"], "limit": 5})
show("5. dateRange with datetimes", {"dimensions": base_dims, "timeDimensions": [{"dimension": f"{T}.actual_completed_at", "dateRange": [(now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S"), now.strftime("%Y-%m-%dT%H:%M:%S")]}], "filters": flt, "limit": 5})
show("6. local vs utc stamp", {"dimensions": [f"{T}.actual_completed_at", f"{T}.local_reporting_timestamp", f"{T}.local_reporting_timestamp_str"], "timeDimensions": td, "filters": flt, "limit": 3})

# Freshness: pull everything for today and sort client-side
rows = show("7. all of today, unsorted (counting only)", {"dimensions": base_dims, "timeDimensions": [{"dimension": f"{T}.actual_completed_at", "dateRange": [today, today]}], "filters": flt, "limit": 1000}, n=0)
if rows:
    k = f"{T}.actual_completed_at"; rows.sort(key=lambda r: r[k], reverse=True)
    print("now UTC:", now.isoformat(timespec="seconds"))
    print("latest 6 completions today (any station):"); [print("  ", r[f"{T}.station_type"], r[f"{T}.device_id"][-6:], r[f"{T}.display_on_kds_at"], "->", r[k]) for r in rows[:6]]
    ce = [r for r in rows if r[f"{T}.device_id"].endswith("58XYZ8")]
    print(f"Counter Expeditor tickets today: {len(ce)}; latest: {ce[0][k] if ce else None}")
    import statistics
    def sec(r): return (datetime.fromisoformat(r[k]) - datetime.fromisoformat(r[f"{T}.display_on_kds_at"])).total_seconds()
    if ce: print("Counter Expeditor median today (s):", statistics.median(sec(r) for r in ce), "mean:", round(statistics.mean(sec(r) for r in ce)))
