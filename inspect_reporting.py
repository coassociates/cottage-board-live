"""One-off: load today's KDS tickets from the Reporting API, trying a few query shapes.
Redacted: ids truncated, no ticket names, no money."""
import json, os, sys, urllib.request, urllib.error, time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ["SQUARE_ACCESS_TOKEN"]
LOC = os.environ.get("SQUARE_LOCATION_ID", "L6NP58MH414CM")
BASE = "https://connect.squareup.com/reporting"
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
today = str(datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/London")).date())
yday = str(datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/London")).date() - timedelta(days=1))

def load(q):
    for _ in range(10):
        req = urllib.request.Request(BASE + "/v1/load", data=json.dumps({"query": q}).encode(), headers=HDR, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:800]
        if res.get("error") == "Continue wait":
            time.sleep(4); continue
        return 200, res
    return 0, "timed out waiting"

def red(k, v):
    k = k.split(".")[-1]
    if isinstance(v, str) and (k.endswith("_id") or k.endswith("_key")) and len(v) > 8:
        return "…" + v[-6:]
    return v

def show(label, q, maxrows=40):
    print(f"\n=== {label}\n{json.dumps(q)}")
    st, res = load(q)
    if st != 200:
        print("->", st, res); return None
    rows = res.get("data", [])
    print(f"-> 200, {len(rows)} rows, slowQuery={res.get('slowQuery')}")
    for r in rows[:maxrows]:
        print("  ", {k.split(".")[-1]: red(k, v) for k, v in r.items()})
    return rows

T = "KitchenPerformanceTickets"
# A: station summary for today via KDS view, to learn station names/types
show("A. stations today (KDS view)", {
    "measures": ["KDS.ticket_count", "KDS.avg_ticket_time_seconds", "KDS.median_ticket_time_seconds"],
    "dimensions": ["KDS.station_type", "KDS.device_code_name", "KDS.device_id", "KDS.auto_ready_setting_enabled"],
    "filters": [{"member": "KDS.location_id", "operator": "equals", "values": [LOC]},
                {"member": "KDS.local_date", "operator": "equals", "values": [today, yday]}]})

# B: per-ticket rows, filters only, no timeDimensions
rows = show("B. tickets today, filter on local_date", {
    "dimensions": [f"{T}.chit_id", f"{T}.order_id", f"{T}.order_source", f"{T}.station_type", f"{T}.device_id",
                   f"{T}.chit_created_at", f"{T}.display_on_kds_at", f"{T}.actual_completed_at", f"{T}.line_item_count"],
    "filters": [{"member": f"{T}.location_id", "operator": "equals", "values": [LOC]},
                {"member": f"{T}.local_date", "operator": "equals", "values": [today]}],
    "order": {f"{T}.actual_completed_at": "desc"}, "limit": 40})

if rows is None:
    # C: same with timeDimensions instead of local_date filter
    rows = show("C. tickets via timeDimensions", {
        "dimensions": [f"{T}.chit_id", f"{T}.order_id", f"{T}.order_source", f"{T}.station_type", f"{T}.device_id",
                       f"{T}.display_on_kds_at", f"{T}.actual_completed_at"],
        "timeDimensions": [{"dimension": f"{T}.actual_completed_at", "dateRange": [yday, today]}],
        "filters": [{"member": f"{T}.location_id", "operator": "equals", "values": [LOC]}],
        "limit": 40})
if rows is None:
    # D: minimal
    rows = show("D. minimal", {"dimensions": [f"{T}.station_type", f"{T}.actual_completed_at"], "limit": 20})
if rows is None:
    show("E. measure only", {"measures": [f"{T}.count"], "timeDimensions": [{"dimension": f"{T}.actual_completed_at", "dateRange": "last 7 days", "granularity": "day"}]})

# F: freshness — the most recent completed ticket we can see right now vs the clock
print("\n=== now UTC:", datetime.now(timezone.utc).isoformat(timespec="seconds"))
show("F. latest 5 completions (freshness check)", {
    "dimensions": [f"{T}.station_type", f"{T}.actual_completed_at", f"{T}.display_on_kds_at"],
    "filters": [{"member": f"{T}.location_id", "operator": "equals", "values": [LOC]},
                {"member": f"{T}.local_date", "operator": "equals", "values": [today]}],
    "order": {f"{T}.actual_completed_at": "desc"}, "limit": 5})
