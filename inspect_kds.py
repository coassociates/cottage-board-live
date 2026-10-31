"""One-off: dump today's Square orders with every timestamp and fulfilment field, redacted,
so we can see which field the KDS expeditor bump writes. Prints to the Actions log, so it
never prints names, notes, amounts, customer ids or full order ids."""
import json, os, sys, urllib.request, urllib.error
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TOKEN = os.environ["SQUARE_ACCESS_TOKEN"]
LOC = os.environ.get("SQUARE_LOCATION_ID", "L6NP58MH414CM")
HDR = {"Authorization": f"Bearer {TOKEN}", "Square-Version": "2025-01-23", "Content-Type": "application/json"}
LONDON = ZoneInfo("Europe/London")

def call(method, path, body=None):
    req = urllib.request.Request("https://connect.squareup.com" + path,
                                 data=json.dumps(body).encode() if body else None, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:400]}

def short(s): return (s or "")[-6:]
def t(s): return s or "-"

now = datetime.now(timezone.utc)
since = (now.astimezone(LONDON) - timedelta(days=int(os.environ.get("DAYS_BACK", "0")))).replace(hour=0, minute=0, second=0, microsecond=0)
body = {"location_ids": [LOC],
        "query": {"filter": {"state_filter": {"states": ["OPEN", "COMPLETED", "CANCELED"]},
                             "date_time_filter": {"created_at": {"start_at": since.astimezone(timezone.utc).isoformat()}}},
                  "sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"}},
        "limit": 500}
orders, cursor = [], None
while True:
    if cursor: body["cursor"] = cursor
    res = call("POST", "/v2/orders/search", body)
    if "_http_error" in res: print("search error", res); sys.exit(1)
    orders += res.get("orders", [])
    cursor = res.get("cursor")
    if not cursor or len(orders) > 2000: break

print(f"=== {len(orders)} orders since {since.isoformat()} at location {LOC}, now {now.isoformat(timespec='seconds')}")
print("sources:", Counter((o.get("source") or {}).get("name") or "(none)" for o in orders))
print("order states:", Counter(o.get("state") for o in orders))
print("fulfilment (type,state) combos:", Counter((f.get("type"), f.get("state")) for o in orders for f in o.get("fulfillments") or []))
print("orders with no fulfilment:", sum(1 for o in orders if not o.get("fulfillments")))

def keys_seen():
    ks = Counter()
    for o in orders:
        for f in o.get("fulfillments") or []:
            for k in f: ks["f." + k] += 1
            for blk in ("pickup_details", "delivery_details", "shipment_details"):
                for k in (f.get(blk) or {}): ks[f"f.{blk}.{k}"] += 1
    return ks
print("fulfilment keys seen:", dict(keys_seen()))
top_keys = Counter(k for o in orders for k in o)
print("order top-level keys seen:", dict(top_keys))

SKIP_PD = {"recipient", "note", "pickup_window_duration", "curbside_pickup_details"}
print("\n=== recent 40 orders (newest first). times UTC. ids truncated.")
for o in orders[:40]:
    src = (o.get("source") or {}).get("name") or "(none)"
    line = [f"#{short(o.get('id'))} {o.get('state'):9} src={src:14}",
            f"created={t(o.get('created_at'))} updated={t(o.get('updated_at'))} closed={t(o.get('closed_at'))}",
            f"ticket={'Y' if o.get('ticket_name') else '-'} tenders={[ (x.get('type'), x.get('created_at')) for x in o.get('tenders') or [] ]}"]
    for f in o.get("fulfillments") or []:
        pd = {k: v for k, v in (f.get("pickup_details") or f.get("delivery_details") or {}).items() if k not in SKIP_PD}
        line.append(f"   fulfil type={f.get('type')} state={f.get('state')} uid={short(f.get('uid'))} details={json.dumps(pd)} "
                    f"entries={len(f.get('line_item_application') or '') if isinstance(f.get('line_item_application'), str) else f.get('line_item_application')} "
                    f"other_keys={[k for k in f if k not in ('type','state','uid','pickup_details','delivery_details','line_item_application')]}")
    print("\n".join(line))

print("\n=== other endpoints the token might reach")
for label, path in [("devices", "/v2/devices?location_id=" + LOC), ("device codes", "/v2/devices/codes?location_id=" + LOC),
                    ("locations", "/v2/locations")]:
    r = call("GET", path)
    if "_http_error" in r:
        print(label, "->", r["_http_error"], r["_body"][:200])
    else:
        items = r.get("devices") or r.get("device_codes") or r.get("locations") or []
        print(label, "->", len(items), "items:", [{k: d.get(k) for k in ("id", "name", "product_type", "status") if k in d}
                                                   | ({"attributes": {k: v for k, v in (d.get('attributes') or {}).items() if k in ('type','model','name','manufacturer')}} if d.get("attributes") else {})
                                                   for d in items][:10])
# Also fetch one OPEN and one COMPLETED order by id, in case search drops fields
for want in ("OPEN", "COMPLETED"):
    o = next((o for o in orders if o.get("state") == want and o.get("fulfillments")), None)
    if o:
        r = call("GET", f"/v2/orders/{o['id']}")
        full = r.get("order", r)
        print(f"\n=== retrieve one {want} order #{short(o['id'])}: top keys {sorted(full.keys())}")
        for f in full.get("fulfillments") or []:
            red = {k: v for k, v in f.items() if k not in ("pickup_details", "delivery_details", "line_item_application")}
            pd = {k: v for k, v in (f.get("pickup_details") or {}).items() if k not in SKIP_PD}
            print("   fulfil:", json.dumps(red), "pickup:", json.dumps(pd))
