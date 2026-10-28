import http.client, json, time

c = http.client.HTTPConnection("127.0.0.1", 8765, timeout=40)
c.request("GET", "/api/scan")
r = c.getresponse()
print("HTTP", r.status)
t0 = time.time()
while time.time() - t0 < 35:
    line = r.readline().decode("utf-8", "ignore")
    if not line:
        break
    if line.startswith("data:"):
        try:
            d = json.loads(line[5:].strip())
        except Exception:
            continue
        if d.get("type") == "done":
            print("DONE ->", json.dumps(d, ensure_ascii=False)[:600])
            break
        elif d.get("type") == "progress":
            print("progress:", d.get("scanned"), d.get("msg", "")[:40])
        elif d.get("type") == "result":
            items = d.get("items", [])
            tiers = {}
            for it in items:
                tiers[it.get("tier")] = tiers.get(it.get("tier"), 0) + 1
            print(f"result: {len(items)} 项目, 档位分布={tiers}")
print("read-end")
