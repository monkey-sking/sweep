#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sweep · 按「时间 / 大小」清理单条 AI 对话记录。

设计：每个 AI 工具对应一个【Provider 适配器】（白名单容器），只在已验证的
会话目录下枚举「一条对话 = 一个文件」，并排除游戏数据 / 运行时缓存 / 设置备份 /
测试夹具等非对话内容。解析每个文件的元数据（标题 / 消息数 / 首末时间）后以统一
会话模型返回。命中 (年龄≥age_days) 或 (大小≥size_mb) 即列为候选，移动到回收站
（可撤销），绝不做永久擦除；SQLite / 嵌入 KV 等聚合存储只枚举不删除（避免整库
损坏或破坏编辑器状态）。

安全四重判定（任一命中即不可清理）：
  1) 文件正被进程独占打开（文件级锁，非进程名整块锁）
  2) 修改时间在 RECENT_GUARD_DAYS 内（疑似进行中）
  3) 命中用户保护列表 PROTECT_PATHS
  4) 该 Provider 适配器声明 cleanable="none"（聚合存储，整库删除风险高）
"""
import os
import time
import json
import shutil
import hashlib
import datetime
import argparse
import sqlite3
import ctypes
from ctypes import wintypes

import sweep_scan as S

HOME = S.HOME
LOCAL = S.LOCAL
ROAMING = S.ROAMING
BACKUP_ROOT = S.BACKUP_ROOT
CONV_BACKUP = os.path.join(BACKUP_ROOT, "conversations")

DEFAULT_AGE_DAYS = 90
DEFAULT_SIZE_MB = 50
RECENT_GUARD_DAYS = 1      # 修改时间在 N 天内视为活跃，不清理
PARSE_MAX_LINES = 1500     # 单文件最多解析行数（性能护栏）
PARSE_MAX_BYTES = 4 * 1024 * 1024

# 用户保护列表：命中绝对不清理。可在 UI / 配置文件里扩展。
PROTECT_PATHS = set()

# ---- 全局排除（黑名单） ----
EXCLUDE_DIR_FRAGMENTS = ("vita_", "fixtures", "fixture", ".tmp", "node_modules",
                         "settings_backup", "backup")
EXCLUDE_FILE_FRAGMENTS = (".trajectory.jsonl",)  # QClaw 产物日志，非对话


# --------------------------------------------------------------------------
# 文件级占用检测（仅当文件此刻正被其他进程独占打开才 True）
# --------------------------------------------------------------------------
def is_locked(fp, procs=None):
    try:
        if os.name != "nt":
            return False
        k32 = ctypes.windll.kernel32
        GENERIC_READ = 0x80000000
        FILE_SHARE_RWD = 0x00000001 | 0x00000002 | 0x00000004  # READ|WRITE|DELETE
        OPEN_EXISTING = 3
        INVALID = wintypes.HANDLE(-1).value
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        h = k32.CreateFileW(fp, GENERIC_READ, FILE_SHARE_RWD, None, OPEN_EXISTING, 0, None)
        if h == INVALID or h is None:
            return True
        k32.CloseHandle(h)
        return False
    except Exception:
        return False


def is_excluded(fp, rel):
    """黑名单：游戏数据 / 设置备份 / 测试夹具 / 产物日志。"""
    low = fp.lower()
    for frag in EXCLUDE_FILE_FRAGMENTS:
        if frag in low:
            return True
    for part in rel.lower().split(os.sep):
        for frag in EXCLUDE_DIR_FRAGMENTS:
            if frag in part:
                return True
    return False


# --------------------------------------------------------------------------
# 元数据解析
# --------------------------------------------------------------------------
def _ts(obj):
    """从对象里尽量抽取时间戳（epoch 秒）。兼容 ISO / 毫秒epoch / 秒epoch。"""
    for k in ("timestamp", "ts", "created_at", "time", "datetime",
              "startTime", "start_time", "updateTime", "updated_at"):
        v = obj.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            # 毫秒级时间戳通常 > 1e12
            return v / 1000.0 if v > 1e12 else v
        if isinstance(v, str):
            s = v.strip().replace("Z", "+00:00")
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    return datetime.datetime.strptime(s, fmt).timestamp()
                except Exception:
                    continue
    return None


def _content_text(content):
    """从 content 字段抽取纯文本（str 或 part 列表）。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict):
                bits.append(part.get("text") or part.get("content") or "")
        return " ".join(b for b in bits if b).strip()
    return ""


def _text(obj):
    """从一条消息对象里抽取纯文本。content 可能是 str 或 part 列表。"""
    return _content_text(obj.get("content"))


def _clean_user_text(t):
    """QClaw 用户消息带 'Sender (untrusted metadata)' 前缀与代码块，取真实诉求。"""
    if not t:
        return t
    if "untrusted metadata" in t:
        idx = t.rfind("] ")
        if idx >= 0:
            t = t[idx + 2:]
    return t.strip()


def _extract_msg(obj):
    """归一化消息对象：返回 (role, content, ts)。
    兼容两种格式：
      - 顶层：{"role":..,"content":..}
      - 信封：{"type":"message","message":{"role":..,"content":..,"timestamp":..}}
    """
    inner = obj.get("message") if isinstance(obj, dict) else None
    if isinstance(inner, dict):
        role = inner.get("role") or obj.get("role")
        content = inner.get("content") if inner.get("content") is not None else obj.get("content")
        ts = _ts(inner) or _ts(obj)
        return role, content, ts
    return obj.get("role"), obj.get("content"), _ts(obj)


def parse_jsonl(fp):
    """解析 QClaw / Claude / Codex / Kimi / Zed 等 jsonl 转录。"""
    title = None
    msg = 0
    fts = lts = None
    conf = "high"
    try:
        sz = os.path.getsize(fp)
        limit_lines = PARSE_MAX_LINES if sz <= PARSE_MAX_BYTES else 400
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= limit_lines:
                    conf = "medium"
                    break
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                role, content, ts = _extract_msg(obj)
                if role in ("user", "assistant", "message", "system", "tool",
                            "human", "ai", "developer"):
                    msg += 1
                    if ts:
                        if fts is None or ts < fts:
                            fts = ts
                        if lts is None or ts > lts:
                            lts = ts
                    if role in ("user", "human") and title is None:
                        t = _clean_user_text(_content_text(content))
                        if t:
                            title = t[:40]
                if isinstance(obj.get("title"), str) and title is None:
                    title = obj["title"][:40]
    except Exception:
        conf = "low"
    if title is None:
        title = os.path.splitext(os.path.basename(fp))[0][:40]
    return title, msg, fts, lts, conf


def parse_json(fp):
    """解析 Gemini 自动会话等单个 JSON 对话文件。"""
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except Exception:
        return os.path.splitext(os.path.basename(fp))[0][:40], 0, None, None, "low"
    if not isinstance(obj, dict):
        return os.path.splitext(os.path.basename(fp))[0][:40], 0, None, None, "low"
    title = obj.get("title") or obj.get("name")
    if isinstance(title, str):
        title = title[:40]
    msgs = (obj.get("messages") or obj.get("chat") or obj.get("conversations")
            or obj.get("turns") or [])
    msg = len(msgs) if isinstance(msgs, list) else 0
    fts = lts = None
    if isinstance(msgs, list) and msgs:
        for m in msgs:
            if isinstance(m, dict):
                ts = _ts(m)
                if ts:
                    if fts is None or ts < fts:
                        fts = ts
                    if lts is None or ts > lts:
                        lts = ts
    if title is None:
        # 取首条用户文本
        for m in msgs if isinstance(msgs, list) else []:
            if isinstance(m, dict) and m.get("role") in ("user", "human"):
                t = _text(m)
                if t:
                    title = t[:40]
                    break
    if title is None:
        title = os.path.splitext(os.path.basename(fp))[0][:40]
    return title, msg, fts, lts, "medium"


def parse_sqlite_conv(fp):
    """Antigravity 会话库(.db)：只读探测，整库不删除。"""
    try:
        con = sqlite3.connect(f"file:{fp}?mode=ro", uri=True)
        cur = con.cursor()
        # 找最可能的会话表
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        title = "(SQLite 会话库)"
        msg = 0
        for t in tables:
            if "conversation" in t.lower() or "session" in t.lower() or "chat" in t.lower():
                try:
                    msg += cur.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                except Exception:
                    pass
        con.close()
        if msg:
            title = f"(SQLite 会话库 · 约 {msg} 行)"
        return title, msg, None, None, "medium"
    except Exception:
        return "(SQLite 会话库)", 0, None, None, "low"


def parse_vscdb(fp, provider):
    """Cursor / Trae 的 state.vscdb：只读解析嵌入聊天，整库不删除。"""
    try:
        con = sqlite3.connect(f"file:{fp}?mode=ro", uri=True)
        cur = con.cursor()
        try:
            rows = cur.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE '%chat%' "
                "OR key LIKE '%composer%'").fetchall()
        except Exception:
            rows = []
        convs = 0
        turns = 0
        for k, v in rows:
            convs += 1
            try:
                o = json.loads(v)
                if isinstance(o, dict):
                    turns += len(o.get("messages") or o.get("turns") or o.get("composer") or [])
                elif isinstance(o, list):
                    turns += len(o)
            except Exception:
                turns += 1
        con.close()
        title = f"{provider} 嵌入聊天(约 {convs} 会话 / {turns} 轮)"
        return title, turns, None, None, "medium"
    except Exception:
        return f"{provider} 嵌入聊天", 0, None, None, "low"


# --------------------------------------------------------------------------
# Provider 适配器（白名单容器）
# --------------------------------------------------------------------------
ADAPTERS = [
    {
        "provider": "QClaw",
        "store": "QClaw 智能体会话",
        "roots": [(os.path.join(HOME, ".qclaw", "agents"), 3)],
        "target": lambda fp, rel: fp.lower().endswith(".jsonl")
        and "sessions" in rel.lower()
        and not rel.lower().endswith(".trajectory.jsonl"),
        "parse": parse_jsonl,
        "cleanable": "file",
    },
    {
        "provider": "Claude Code",
        "store": "Claude 项目历史",
        "roots": [(os.path.join(HOME, ".claude", "projects"), 4)],
        "target": lambda fp, rel: fp.lower().endswith(".jsonl"),
        "parse": parse_jsonl,
        "cleanable": "file",
    },
    {
        "provider": "Codex",
        "store": "Codex 会话",
        "roots": [(os.path.join(HOME, ".codex", "sessions"), 3),
                  (os.path.join(HOME, ".codex", "projects"), 4)],
        "target": lambda fp, rel: fp.lower().endswith(".jsonl")
        and "fixtures" not in rel.lower() and ".tmp" not in rel.lower(),
        "parse": parse_jsonl,
        "cleanable": "file",
    },
    {
        "provider": "Antigravity",
        "store": "Antigravity 实例历史",
        "roots": [(os.path.join(HOME, ".antigravity_cockpit", "instances"), 3)],
        "target": lambda fp, rel: fp.lower().endswith(".jsonl")
        and "history" in rel.lower(),
        "parse": parse_jsonl,
        "cleanable": "file",
    },
    {
        "provider": "Antigravity",
        "store": "Antigravity 会话库(SQLite)",
        "roots": [(os.path.join(HOME, ".gemini", "antigravity", "conversations"), 1),
                  (os.path.join(HOME, ".gemini", "antigravity-cli", "conversations"), 1),
                  (os.path.join(HOME, ".gemini", "antigravity-ide", "conversations"), 1)],
        "target": lambda fp, rel: fp.lower().endswith(".db") or fp.lower().endswith(".sqlite"),
        "parse": parse_sqlite_conv,
        "cleanable": "none",
    },
    {
        "provider": "Cursor",
        "store": "Cursor 工作区状态(嵌入聊天)",
        "roots": [(os.path.join(ROAMING, "Cursor", "User", "workspaceStorage"), 3)],
        "target": lambda fp, rel: fp.lower().endswith("state.vscdb"),
        "parse": lambda fp: parse_vscdb(fp, "Cursor"),
        "cleanable": "none",
    },
    {
        "provider": "Trae",
        "store": "Trae 工作区状态(嵌入聊天)",
        "roots": [(os.path.join(ROAMING, "Trae", "User", "workspaceStorage"), 3),
                  (os.path.join(ROAMING, "Trae", "User", "globalStorage"), 2)],
        "target": lambda fp, rel: fp.lower().endswith("state.vscdb"),
        "parse": lambda fp: parse_vscdb(fp, "Trae"),
        "cleanable": "none",
    },
    {
        "provider": "Gemini CLI",
        "store": "Gemini 自动会话",
        "roots": [(os.path.join(HOME, ".gemini", "tmp"), 4)],
        "target": lambda fp, rel: fp.lower().endswith(".json")
        and "chats" in rel.lower()
        and "settings" not in rel.lower()
        and "vita" not in rel.lower(),
        "parse": parse_json,
        "cleanable": "file",
    },
    {
        "provider": "Kimi",
        "store": "Kimi 会话",
        "roots": [(os.path.join(HOME, ".kimi", "sessions"), 4),
                  (os.path.join(HOME, ".kimi-code", "sessions"), 4)],
        "target": lambda fp, rel: fp.lower().endswith(".jsonl")
        and "vita" not in rel.lower() and "settings" not in rel.lower(),
        "parse": parse_jsonl,
        "cleanable": "file",
    },
    {
        "provider": "Zed",
        "store": "Zed 对话",
        "roots": [(os.path.join(LOCAL, "Zed", "conversations"), 2)],
        "target": lambda fp, rel: not fp.lower().endswith((".db", ".sqlite"))
        and "vita" not in rel.lower(),
        "parse": parse_jsonl,
        "cleanable": "file",
    },
]


# --------------------------------------------------------------------------
# 枚举 / 规划
# --------------------------------------------------------------------------
def enum_units(procs=None):
    """枚举所有单条对话文件，附带大小/年龄/锁/元数据。"""
    if procs is None:
        procs = S.running_procs()
    now = time.time()
    out = []
    for ad in ADAPTERS:
        for root, depth in ad["roots"]:
            if not os.path.isdir(root):
                continue
            for dp, dirs, files in os.walk(root):
                if dp[len(root):].count(os.sep) > depth:
                    dirs[:] = []
                    continue
                for f in files:
                    fp = os.path.join(dp, f)
                    rel = os.path.relpath(fp, root)
                    if not ad["target"](fp, rel):
                        continue
                    if is_excluded(fp, rel):
                        continue
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    title, msg, fts, lts, conf = ad["parse"](fp)
                    is_file = ad["cleanable"] == "file"
                    reason = ""
                    if not is_file:
                        reason = ("SQLite/嵌入聚合存储，整库删除会丢失其他会话或破坏"
                                  "编辑器状态，暂不支持单条清理（仅展示）")
                    out.append({
                        "provider": ad["provider"],
                        "store": ad["store"],
                        "path": fp,
                        "rel": rel,
                        "title": title or os.path.basename(fp),
                        "msg_count": msg,
                        "first_ts": fts,
                        "last_ts": lts,
                        "size_bytes": st.st_size,
                        "size_mb": st.st_size / 1048576,
                        "age_days": (now - st.st_mtime) / 86400,
                        "locked": is_locked(fp, procs),
                        "confidence": conf,
                        "cleanable": is_file,
                        "reason": reason,
                    })
    return out


def plan(age_days=DEFAULT_AGE_DAYS, size_mb=DEFAULT_SIZE_MB, procs=None):
    units = enum_units(procs)
    for u in units:
        u["by_age"] = u["age_days"] >= age_days
        u["by_size"] = u["size_mb"] >= size_mb
        recent = u["age_days"] < RECENT_GUARD_DAYS
        u["protected"] = os.path.normpath(u["path"]) in PROTECT_PATHS
        u["candidate"] = (u["by_age"] or u["by_size"]) and u["cleanable"] \
            and not u["locked"] and not recent and not u["protected"]
        if recent and not u["candidate"]:
            u["reason"] = (u["reason"] + "；" if u["reason"] else "") \
                + f"近 {RECENT_GUARD_DAYS} 天内活跃，疑似进行中"
    return units


# --------------------------------------------------------------------------
# 安全移动（跨卷先复制+哈希校验再删源）
# --------------------------------------------------------------------------
def file_hash(fp, blk=1 << 20):
    h = hashlib.md5()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(blk), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_move(src, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        src_drive = os.path.splitdrive(os.path.abspath(src))[0].lower()
        dest_drive = os.path.splitdrive(os.path.abspath(dest))[0].lower()
        if src_drive and dest_drive and src_drive != dest_drive:
            shutil.copy2(src, dest)
            if file_hash(src) == file_hash(dest):
                os.remove(src)
            else:
                raise IOError("跨卷复制哈希校验失败，已保留源文件")
        else:
            shutil.move(src, dest)
    except Exception:
        # 兜底：直接 move（同卷安全）
        shutil.move(src, dest)


# --------------------------------------------------------------------------
# CLI 预览 / 执行
# --------------------------------------------------------------------------
def clean_conversations(age_days=DEFAULT_AGE_DAYS, size_mb=DEFAULT_SIZE_MB, dry_run=True):
    units = plan(age_days, size_mb)
    cands = [u for u in units if u["candidate"]]
    locked = [u for u in units if u["locked"]]
    notclean = [u for u in units if not u["cleanable"]]
    total_mb = sum(u["size_mb"] for u in cands)

    print(f"策略：年龄 ≥ {age_days} 天 或 大小 ≥ {size_mb} MB 的单条对话 → 清理(进回收站)\n")
    print(f"枚举到对话单元 {len(units)} 个（其中聚合存储仅展示 {len(notclean)} 个）")
    print(f"命中候选 {len(cands)} 个，可回收 {total_mb/1024:.2f} GB")
    if locked:
        print(f"🔒 跳过 {len(locked)} 个（关联应用运行中，已锁定）")
    print("-" * 72)

    for u in sorted(cands, key=lambda x: -x["size_mb"]):
        why = []
        if u["by_age"]:
            why.append(f"旧({u['age_days']:.0f}天)")
        if u["by_size"]:
            why.append(f"大({u['size_mb']:.1f}MB)")
        print(f"  [{','.join(why):14s}] {u['size_mb']:8.1f}MB  {u['provider']} · {u['title']}")
        print(f"        {u['rel']}")

    if not cands:
        print("  无命中候选。可调低 age_days / size_mb 阈值。")
        return

    if dry_run:
        print(f"\n[dry-run] 未执行任何移动。加 --execute 并在提示输入 y 才移动到回收站(可撤销)。")
        return

    ans = input("确认将以上候选移动到回收站（可撤销）？输入 y 继续：").strip().lower()
    if ans != "y":
        print("已取消。")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = os.path.join(CONV_BACKUP, f"conv_{ts}")
    manifest = []
    moved = failed = 0
    for u in cands:
        safe_store = u["store"].split("(")[0].strip().replace(" ", "_")
        dest = os.path.join(dest_root, safe_store, *u["rel"].split(os.sep))
        try:
            safe_move(u["path"], dest)
            moved += 1
            manifest.append({"store": u["store"], "rel": u["rel"],
                             "src": u["path"], "dest": dest,
                             "size_bytes": u["size_bytes"]})
            print(f"  已移动: {u['rel']}")
        except Exception as e:
            failed += 1
            print(f"  失败: {u['rel']}: {e}")
    with open(os.path.join(dest_root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"type": "conv_cleanup", "age_days": age_days, "size_mb": size_mb,
                   "moved_at": ts, "items": manifest}, f, ensure_ascii=False, indent=2)
    print(f"\n完成：移动 {moved} 条，失败 {failed} 条。")
    print(f"回收站：{dest_root}（可手动移回或用还原功能恢复）")


def main():
    ap = argparse.ArgumentParser(description="按时间/大小清理单条 AI 对话记录")
    ap.add_argument("--age", type=int, default=DEFAULT_AGE_DAYS, help="年龄阈值(天)，默认 90")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE_MB, help="大小阈值(MB)，默认 50")
    ap.add_argument("--execute", action="store_true", help="真正执行移动(默认仅预览)")
    ap.add_argument("--whatif", action="store_true", help="用一组更激进的阈值演示命中量")
    args = ap.parse_args()

    if args.whatif:
        print("=== 敏感性演示：不同阈值下的命中量 ===")
        procs = S.running_procs()
        for a, s in [(90, 50), (60, 30), (30, 20)]:
            us = plan(a, s, procs)
            c = [u for u in us if u["candidate"]]
            nc = [u for u in us if not u["cleanable"]]
            print(f"  年龄>{a}天 或 大小>{s}MB → 候选 {len(c)} 条 / {sum(u['size_mb'] for u in c)/1024:.2f} GB"
                  f"；聚合仅展示 {len(nc)} 条")
        return

    clean_conversations(age_days=args.age, size_mb=args.size, dry_run=not args.execute)


if __name__ == "__main__":
    main()
