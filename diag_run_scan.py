import server
import traceback

try:
    d, c = server.run_scan(force=True)
    print("run_scan OK, cached =", c)
    items = d.get("items", [])
    print("items:", len(items))
    tiers = {}
    for it in items:
        tiers[it.get("tier")] = tiers.get(it.get("tier"), 0) + 1
    print("tiers:", tiers)
    print("sample:", items[0] if items else "none")
except Exception:
    traceback.print_exc()
