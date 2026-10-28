#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""枚举各 AI 工具【单条对话】文件，含大小与修改时间(年龄)，验证「按时间/大小删旧聊」可行性。"""
import os, time

HOME = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
ROAMING = os.path.join(HOME, "AppData", "Roaming")

# (标签, 根目录, 递归深度, 是否按「对话文件」枚举)
STORES = [
    ("Antigravity(.gemini/antigravity)", os.path.join(HOME, ".gemini", "antigravity", "conversations"), 1),
    ("Antigravity(.gemini/antigravity-cli)", os.path.join(HOME, ".gemini", "antigravity-cli", "conversations"), 1),
    ("Antigravity(.gemini/antigravity-ide)", os.path.join(HOME, ".gemini", "antigravity-ide", "conversations"), 1),
    ("Antigravity(cockpit/instances)", os.path.join(HOME, ".antigravity_cockpit", "instances"), 3),
    ("Claude(projects/history)", os.path.join(HOME, ".claude", "projects"), 4),
    ("Codex(archived_sessions)", os.path.join(HOME, ".codex", "archived_sessions"), 3),
    ("QClaw(agents)", os.path.join(HOME, ".qclaw", "agents"), 3),
]

now = time.time()
def enum_conv_files(root, max_depth):
    """返回 [(relpath, size_bytes, mtime, is_conv), ...]。is_conv=True 视为单条对话文件。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dp, dirs, files in os.walk(root):
        depth = dp[len(root):].count(os.sep)
        if depth > max_depth:
            dirs[:] = []
            continue
        for f in files:
            fp = os.path.join(dp, f)
            fl = f.lower()
            # 单条对话文件特征：.db / .sqlite / history 下的 .jsonl / session 文件
            is_conv = (fl.endswith(".db") or fl.endswith(".sqlite")
                       or (fl.endswith(".jsonl") and "history" in dp.lower())
                       or fl.endswith(".session"))
            if not is_conv:
                continue
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out.append((os.path.relpath(fp, root), st.st_size, st.st_mtime, True))
        # 若目录本身即单条对话（如 instances 下每个子目录是一个会话）
    return out

# 默认保留策略
AGE_DAYS = 90      # 超过 90 天 = 旧
SIZE_MB = 50       # 超过 50MB = 大

print(f"默认策略：年龄 > {AGE_DAYS} 天 或 大小 > {SIZE_MB} MB 的单条对话 → 候选清理(进回收站)\n")
total_units = 0
total_reclaim = 0
for label, root, depth in STORES:
    units = enum_conv_files(root, depth)
    if not units:
        print(f"### {label}: (无可枚举的单条对话文件)")
        continue
    cands = []
    for rel, sz, mt, _ in units:
        age = (now - mt) / 86400
        sz_mb = sz / 1024 / 1024
        old = age >= AGE_DAYS
        big = sz_mb >= SIZE_MB
        if old or big:
            cands.append((rel, sz, age, old, big))
    reclaim = sum(sz for _, sz, _, _, _ in cands)
    total_units += len(units)
    total_reclaim += reclaim
    print(f"### {label}")
    print(f"    单条对话总数: {len(units)}   | 命中(旧或大): {len(cands)}   | 可回收: {reclaim/1024/1024/1024:.2f} GB")
    # 展示最大的 4 条 & 最老的 4 条
    by_size = sorted(units, key=lambda x: -x[1])[:4]
    by_age = sorted(units, key=lambda x: x[2])[:4]
    print("    最大:")
    for rel, sz, mt, _ in by_size:
        print(f"      {sz/1024/1024:8.1f}MB  {(now-mt)/86400:6.0f}天  {rel}")
    print("    最老:")
    for rel, sz, mt, _ in by_age:
        print(f"      {sz/1024/1024:8.1f}MB  {(now-mt)/86400:6.0f}天  {rel}")

print(f"\n=== 合计：单条对话 {total_units} 个，按默认策略可回收 {total_reclaim/1024/1024/1024:.2f} GB ===")
