import http.client, time

c = http.client.HTTPConnection("127.0.0.1", 8765, timeout=60)
c.request("GET", "/api/scan")   # 无 fresh，模拟浏览器默认行为
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
    print(f"[{n}] {s.rstrip()[:130]}")
    if s.startswith("data:") and '"done"' in s:
        print(">>> 收到 done")
        break
print("total lines:", n)
