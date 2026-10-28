import http.client, json

c = http.client.HTTPConnection("127.0.0.1", 8765, timeout=90)
c.request("GET", "/api/scan?fresh=1")
r = c.getresponse()
last = None
while True:
    line = r.readline().decode("utf-8", "ignore")
    if not line:
        break
    if line.startswith("data:"):
        payload = line[5:].strip()
        try:
            d = json.loads(payload)
        except Exception:
            continue
        if d.get("done") or "tiers" in d or "items" in d or "total_bytes" in d:
            last = d
print("=== 8765 扫描结果 ===")
if not last:
    print("未收到完成的扫描事件")
else:
    print("keys:", list(last.keys())[:12])
    if "tiers" in last:
        for k, v in last["tiers"].items():
            print(f"  {k}: {v}")
    if "items" in last:
        print("  条目数:", len(last["items"]))
    if "total_bytes" in last:
        print("  总字节:", last["total_bytes"])
    # 打印前 600 字符便于核对
    s = json.dumps(last, ensure_ascii=False)
    print("预览:", s[:600])
