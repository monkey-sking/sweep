#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定位各家真实「对话文件」并解析内部结构（角色/消息/标题/时间）。"""
import os, json, glob, collections

HOME = os.path.expanduser("~")

def show_jsonl(path, n=3):
    roles = collections.Counter()
    titles = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 2000:
                    break
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                r = obj.get("role") or obj.get("type") or (obj.get("message", {}).get("role"))
                if r:
                    roles[str(r)] += 1
                if isinstance(obj.get("title"), str):
                    titles.append(obj["title"])
    except Exception as e:
        return f"<err {e}>"
    return f"roles={dict(roles)} titles_sample={titles[:3]}"

def show_json(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except Exception as e:
        return f"<err {e}>"
    if isinstance(obj, dict):
        keys = list(obj.keys())[:12]
        # 找消息列表
        msgs = obj.get("messages") or obj.get("chat") or obj.get("conversations") or obj.get("turns")
        info = f"keys={keys}"
        if isinstance(msgs, list):
            info += f" | messages={len(msgs)}"
            if msgs and isinstance(msgs[0], dict):
                info += f" | msg_keys={list(msgs[0].keys())[:8]}"
        if "title" in obj:
            info += f" | title={str(obj['title'])[:40]}"
        return info
    return f"type={type(obj).__name__}"

# ---- QClaw ----
print("===== QClaw 会话 jsonl 定位 =====")
qj = []
for dp, dirs, files in os.walk(os.path.join(HOME, ".qclaw")):
    for f in files:
        if f.endswith(".jsonl"):
            qj.append(os.path.join(dp, f))
print(f"  共 {len(qj)} 个 jsonl；样例目录层: {os.path.relpath(os.path.dirname(qj[0]), HOME) if qj else '无'}")
if qj:
    # 取最大的几个看结构
    qj.sort(key=lambda p: -os.path.getsize(p))
    for p in qj[:2]:
        print(f"  [{os.path.relpath(p, HOME)}]  {os.path.getsize(p)//1024}KB")
        print("    ", show_jsonl(p))

# ---- Codex ----
print("\n===== Codex 会话 jsonl 定位 =====")
cj = []
for dp, dirs, files in os.walk(os.path.join(HOME, ".codex")):
    for f in files:
        if f.endswith(".jsonl"):
            cj.append(os.path.join(dp, f))
print(f"  共 {len(cj)} 个 jsonl")
for p in cj[:3]:
    print(f"  [{os.path.relpath(p, HOME)}]  {os.path.getsize(p)//1024}KB")
    print("    ", show_jsonl(p))

# ---- Antigravity ----
print("\n===== Antigravity 会话 jsonl 定位 =====")
aj = []
for base in [os.path.join(HOME, ".antigravity_cockpit"), os.path.join(HOME, ".gemini", "antigravity"), os.path.join(HOME, ".gemini", "antigravity-cli")]:
    for dp, dirs, files in os.walk(base):
        for f in files:
            if f.endswith(".jsonl"):
                aj.append(os.path.join(dp, f))
print(f"  共 {len(aj)} 个 jsonl")
for p in aj[:3]:
    print(f"  [{os.path.relpath(p, HOME)}]  {os.path.getsize(p)//1024}KB")
    print("    ", show_jsonl(p))

# ---- .claude 真实结构 ----
print("\n===== .claude 真实结构(找会话) =====")
cl = os.path.join(HOME, ".claude")
if os.path.isdir(cl):
    for dp, dirs, files in os.walk(cl):
        depth = dp[len(cl):].count(os.sep)
        if depth <= 2:
            rel = os.path.relpath(dp, cl)
            print(f"  [{rel or '.'}] dirs={dirs[:6]} files={[f for f in files][:6]}")
    # 找 jsonl
    cj2 = []
    for dp, dirs, files in os.walk(cl):
        for f in files:
            if f.endswith(".jsonl"):
                cj2.append(os.path.join(dp, f))
    print(f"  .claude 下 jsonl 共 {len(cj2)} 个")
    for p in cj2[:2]:
        print(f"    [{os.path.relpath(p, cl)}] {os.path.getsize(p)//1024}KB : {show_jsonl(p)}")
else:
    print("  .claude 不存在")

# ---- Gemini 找真正对话 ----
print("\n===== Gemini 找真正对话(排除 settings 备份) =====")
gj = []
for dp, dirs, files in os.walk(os.path.join(HOME, ".gemini")):
    for f in files:
        if f.endswith(".json") and "backup" not in f and "settings" not in f.lower():
            gj.append(os.path.join(dp, f))
gj.sort(key=lambda p: -os.path.getsize(p))
print(f"  非备份 json 共 {len(gj)} 个；最大的几个:")
for p in gj[:5]:
    print(f"  [{os.path.relpath(p, HOME)}]  {os.path.getsize(p)//1024}KB")
    print("    ", show_json(p)[:160])
