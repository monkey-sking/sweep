import http.client, json, socket, time

def port_open(p, t=0.5):
    s = socket.socket(); s.settimeout(t)
    try:
        s.connect(("127.0.0.1", p)); s.close(); return True
    except Exception:
        return False

def get(p, path):
    c = http.client.HTTPConnection("127.0.0.1", p, timeout=20)
    c.request("GET", path); r = c.getresponse(); d = r.read().decode("utf-8", "ignore")
    c.close(); return d

# 等 8765 就绪
for _ in range(30):
    if port_open(8765): break
    time.sleep(0.5)
print("8765 open:", port_open(8765))
print("8767 open (应为 False):", port_open(8767))

home = get(8765, "/")
print("首页含[对话清理]标签按钮:", "tab-conv-btn" in home)
print("首页含[对话清理]section:", 'id="tab-conv"' in home)
print("首页仍含[扫描概览/重复检测/回收站]:",
      all(k in home for k in ["tab-scan", "tab-dup", "tab-trash"]))

# 对话清理接口（默认 90/50）
d = json.loads(get(8765, "/api/conv/units?age=90&size=50"))
u = d["units"]
cand = [x for x in u if x["candidate"]]
lock = [x for x in u if x["locked"]]
print(f"\n/api/conv/units 默认阈值: 共 {len(u)} 条, 候选 {len(cand)}, 锁定 {len(lock)}")
for x in u[:3]:
    print("  ", x["store"], "|", x["rel"][:40], "|", x["size_mb"], "MB |", x["age_days"], "天 |",
          "候选" if x["candidate"] else ("锁定" if x["locked"] else "保留"))

# 更松阈值
d2 = json.loads(get(8765, "/api/conv/units?age=60&size=30"))
c2 = [x for x in d2["units"] if x["candidate"]]
print(f"\n阈值 60/30: 候选 {len(c2)} 条, 可回收 {sum(x['size_mb'] for x in c2)/1024:.2f} GB")

b = json.loads(get(8765, "/api/conv/backups"))
print("\n/api/conv/backups:", b)
