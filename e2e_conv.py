import http.client, json, os, socket, time

PORT = 8767
def api(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    data = json.dumps(body or {}).encode() if body is not None else None
    c.request(method, path, body=data,
              headers={"Content-Type": "application/json"} if data else {})
    return json.loads(c.getresponse().read().decode("utf-8"))

# 1) 用更松阈值拿候选
d = api("GET", "/api/conversations?age=60&size=30")
cands = [x for x in d["units"] if x["candidate"]]
print(f"age=60/size=30 候选数={len(cands)}")
# 选最小的 1 条做安全闭环测试
pick = min(cands, key=lambda x: x["size_mb"])
print(f"测试清理: {pick['rel']} ({pick['size_mb']}MB, {pick['age_days']}天) 原路径={pick['path']}")
orig_exists = os.path.exists(pick["path"])

# 2) 清理（移动进回收站）
r = api("POST", "/api/clean", {"paths": [pick["path"]]})
print(f"清理返回: moved={r['moved']} skipped_locked={r['skipped_locked']} failed={r['failed']}")
time.sleep(0.3)
after_clean = os.path.exists(pick["path"])           # 应为 False（已移走）
b = api("GET", "/api/backups")
print(f"回收站备份数={len(b['backups'])} 最新={b['backups'][0]['dir'] if b['backups'] else '无'}")

# 3) 还原
rr = api("POST", "/api/restore", {"dir": b["backups"][0]["dir"]})
print(f"还原返回: ok={rr.get('ok')} restored={rr.get('restored')} failed={rr.get('failed')}")
time.sleep(0.3)
after_restore = os.path.exists(pick["path"])          # 应为 True（已还原）

print("\n闭环结论:")
print("  清理前文件存在 =", orig_exists)
print("  清理后文件存在 =", after_clean, "(应 False)")
print("  还原后文件存在 =", after_restore, "(应 True)")
print("  端到端闭环", "✅ 通过" if (orig_exists and not after_clean and after_restore) else "❌ 异常")
