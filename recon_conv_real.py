#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实地侦察：各家 AI 工具真实的「对话」落盘到底由什么文件构成。"""
import os, json, collections

HOME = os.path.expanduser("~")
ROAMING = os.path.join(HOME, "AppData", "Roaming")

ROOTS = {
    "Claude Code": os.path.join(HOME, ".claude", "projects"),
    "Codex": os.path.join(HOME, ".codex"),
    "Gemini(.gemini)": os.path.join(HOME, ".gemini"),
    "Antigravity": os.path.join(HOME, ".antigravity_cockpit"),
    "QClaw": os.path.join(HOME, ".qclaw"),
    "Cursor": os.path.join(ROAMING, "Cursor", "User"),
    "Coze": os.path.join(HOME, ".coze"),
}

def ext_of(p):
    base = os.path.basename(p)
    if "." not in base:
        return "<noext>"
    return base.rsplit(".", 1)[1].lower()

def walk_ext_stats(root, max_depth=4):
    stats = collections.defaultdict(lambda: [0, 0])  # ext -> [count, bytes]
    file_count = 0
    for dp, dirs, files in os.walk(root):
        depth = dp[len(root):].count(os.sep)
        if depth > max_depth:
            dirs[:] = []
            continue
        for f in files:
            fp = os.path.join(dp, f)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            stats[ext_of(fp)][0] += 1
            stats[ext_of(fp)][1] += sz
            file_count += 1
    return stats, file_count

def peek(p, n=600):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(n)
    except Exception as e:
        return f"<read err {e}>"

print("=" * 72)
for name, root in ROOTS.items():
    if not os.path.isdir(root):
        print(f"\n### {name}: 目录不存在\n"); continue
    stats, fc = walk_ext_stats(root)
    print(f"\n### {name}  ({root})")
    print(f"    文件总数: {fc}")
    # 按总大小排序输出类型
    for ext, (cnt, sz) in sorted(stats.items(), key=lambda kv: -kv[1][1])[:12]:
        mb = sz / 1048576
        print(f"    .{ext:10s}  {cnt:6d} 个   {mb:9.1f} MB")
    # 找几个代表性样例
    samples = []
    for dp, dirs, files in os.walk(root):
        if dp[len(root):].count(os.sep) > 3:
            dirs[:] = []
            continue
        for f in files:
            if ext_of(f) in ("jsonl", "json", "md", "txt", "db", "sqlite"):
                samples.append(os.path.join(dp, f))
                if len(samples) >= 4:
                    break
        if len(samples) >= 4:
            break
    if samples:
        print("    --- 样例内容(前600字符) ---")
        for s in samples[:3]:
            print(f"    [{os.path.relpath(s, root)}]")
            print("       " + peek(s).replace("\n", "\n       ")[:600])
