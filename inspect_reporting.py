"""One-off: discover Reporting API cubes relating to the kitchen display and try to load
today's rows. Redacted: no money, names, or full ids in the log."""
import json, os, sys, urllib.request, urllib.error, time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ["SQUARE_ACCESS_TOKEN"]
LOC = os.environ.get("SQUARE_LOCATION_ID", "L6NP58MH414CM")
BASE = "https://connect.squareup.com/reporting"
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"_body": e.read().decode()[:1500]}

st, meta = call("GET", "/v1/meta")
print("meta ->", st)
if st != 200:
    print(meta); sys.exit(1)
cubes = meta.get("cubes", [])
print(f"{len(cubes)} cubes/views. names: {[c.get('name') for c in cubes]}")
KW = ("kitchen", "kds", "ticket", "fulfil", "device", "station", "prep")
hits = [c for c in cubes if any(k in json.dumps(c).lower() for k in KW)]
print("\n=== cubes mentioning", KW, ":", [c.get("name") for c in hits])
for c in hits:
    print(f"\n--- {c.get('name')} type={c.get('type')} title={c.get('title')} desc={c.get('description')}")
    extra = {k: v for k, v in c.items() if k not in ("name", "type", "title", "description", "measures", "dimensions", "segments")}
    if extra: print("    other keys:", json.dumps(extra)[:600])
    for kind in ("measures", "dimensions", "segments"):
        for m in c.get(kind) or []:
            print(f"    {kind[:-1]:9} {m.get('name'):60} type={m.get('type')} agg={m.get('aggType','')} title={m.get('title')} desc={(m.get('description') or '')[:120]}")

# Try to load today's rows from the most kitchen-looking cube.
target = next((c for c in hits if "kitchen" in c.get("name", "").lower()), None) or next((c for c in hits if "ticket" in c.get("name", "").lower()), None)
if not target:
    print("\nno kitchen/ticket cube found"); sys.exit(0)
name = target["name"]
dims = [d["name"] for d in target.get("dimensions") or []]
timedims = [d["name"] for d in target.get("dimensions") or [] if d.get("type") == "time"]
meas = [m["name"] for m in target.get("measures") or [] if m.get("aggType") in ("count", "countDistinct")][:2]
print(f"\n=== loading {name}: time dims {timedims}")
tdim = next((d for d in timedims if "complet" in d.lower()), None) or (timedims[0] if timedims else None)
today = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/London")).date()
# drop dimensions that would carry personal or money detail
safe_dims = [d for d in dims if not any(x in d.lower() for x in ("name", "note", "money", "amount", "price", "customer", "recipient", "email", "phone"))]
q = {"query": {"dimensions": safe_dims, "measures": meas,
               "timeDimensions": ([{"dimension": tdim, "dateRange": [str(today - timedelta(days=1)), str(today)]}] if tdim else []),
               "limit": 60}}
if tdim:
    q["query"]["order"] = {tdim: "desc"}
print("query:", json.dumps(q))
for attempt in range(8):
    st, res = call("POST", "/v1/load", q)
    if st == 200 and res.get("error") == "Continue wait":
        time.sleep(3); continue
    break
print("load ->", st)
if st != 200:
    print(res); sys.exit(0)
rows = res.get("data", [])
print(f"{len(rows)} rows. annotation keys: {list((res.get('annotation') or {}).keys())}")
def red(k, v):
    if isinstance(v, str) and ("_id" in k.lower() or k.lower().endswith("id") or "uid" in k.lower()) and len(v) > 8:
        return "…" + v[-6:]
    return v
for r in rows[:60]:
    print({k.split(".")[-1]: red(k, v) for k, v in r.items()})
