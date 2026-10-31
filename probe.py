"""Run one Reporting API query (JSON on stdin or in QUERY) and print redacted rows.
Used by the "Inspect KDS tickets" workflow's optional query input. Ids, names and notes are
truncated or dropped because the log is public."""
import json, os, sys, time, urllib.request, urllib.error

TOKEN = os.environ["SQUARE_ACCESS_TOKEN"]
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
q = json.loads(os.environ.get("QUERY") or sys.stdin.read())
if "query" not in q:
    q = {"query": q}
print("query:", json.dumps(q))
for _ in range(10):
    req = urllib.request.Request("https://connect.squareup.com/reporting/v1/load", data=json.dumps(q).encode(), headers=HDR, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("->", e.code, e.read().decode()[:600]); sys.exit(0)
    if res.get("error") == "Continue wait":
        time.sleep(4); continue
    break
rows = res.get("data", [])
print(f"-> {len(rows)} rows")
DROP = ("name", "note", "recipient", "email", "phone")
for r in rows[:int(os.environ.get("MAX_ROWS", "60"))]:
    out = {}
    for k, v in r.items():
        f = k.split(".")[-1]
        if any(d in f.lower() for d in DROP):
            continue
        if isinstance(v, str) and (f.endswith("_id") or f.endswith("_key")) and len(v) > 8:
            v = "…" + v[-6:]
        out[f] = v
    print("  ", out)
