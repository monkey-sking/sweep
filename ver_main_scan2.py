import http.client, time

c = http.client.HTTPConnection("127.0.0.1", 8765, timeout=60)
c.request("GET", "/api/scan?fresh=1")
r = c.getresponse()
print("status", r.status, "| ctype", r.getheader("Content-Type"))
t0 = time.time()
n = 0
while time.time() - t0 < 50:
    line = r.readline()
    if not line:
        print("[EOF] 连接关闭，无更多数据")
        break
    s = line.decode("utf-8", "ignore")
    n += 1
    head = s.rstrip()[:130]
    print(f"[{n}] {head}")
    if s.startswith("data:") and '"done"' in s:
        print(">>> 收到 done 事件，停止")
        break
print("total lines:", n)
