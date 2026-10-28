import socket, time, http.client, json

def wait_port(p, host="127.0.0.1", timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s = socket.create_connection((host, p), 0.3)
            s.close()
            return True
        except Exception:
            time.sleep(0.3)
    return False

PORT = 8767
if not wait_port(PORT):
    print("服务未就绪")
else:
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("GET", "/api/conversations?age=90&size=50")
    d = json.loads(c.getresponse().read().decode("utf-8"))
    u = d["units"]
    cand = [x for x in u if x["candidate"]]
    lock = [x for x in u if x["locked"]]
    print(f"阈值 age={d['age']} size={d['size']}")
    print(f"对话总数={len(u)}  可清理={len(cand)}  锁定={len(lock)}  "
          f"可清理总量={sum(x['size_mb'] for x in cand)/1024:.2f}GB")
    print("\n前 8 条预览：")
    for x in u[:8]:
        why = []
        if x["by_age"]:
            why.append(f"旧{x['age_days']:.0f}天")
        if x["by_size"]:
            why.append(f"大{x['size_mb']:.0f}MB")
        tag = "锁定" if x["locked"] else ("可清:"+"/".join(why) if x["candidate"] else "保留")
        print(f"  [{tag:14s}] {x['size_mb']:7.1f}MB {x['age_days']:>4.0f}天  {x['store']}  {x['rel'][:40]}")
    # 验证 by_age / by_size 过滤（选"旧"）
    old = [x for x in u if x["candidate"] and x["by_age"]]
    big = [x for x in u if x["candidate"] and x["by_size"]]
    print(f"\n按旧可全选 -> {len(old)} 条；按大可全选 -> {len(big)} 条")
    c.request("GET", "/api/backups")
    b = json.loads(c.getresponse().read().decode("utf-8"))
    print(f"回收站备份数={len(b['backups'])}")
