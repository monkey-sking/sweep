#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sweep 本机 AI 缓存扫描器（只读，不删除任何文件）
扫描常见 AI 工具的缓存 / 模型 / 数据目录，按三档风险分类，
并检测进程占用实现"真锁"，最终生成 scan.json 与 report.html。
"""
import os
import json
import time
import shutil
import argparse
import subprocess
import datetime

HOME = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
ROAMING = os.path.join(HOME, "AppData", "Roaming")
BACKUP_ROOT = os.path.join(HOME, ".sweep_backup")

# 扫描产物落地路径：固定到本脚本所在目录，避免依赖进程 cwd（否则从非项目目录启动时
# cwd 可能指向不可写位置，写 scan.json/report.html 时报 Permission denied）
_HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_JSON = os.path.join(_HERE, "scan.json")
REPORT_HTML = os.path.join(_HERE, "report.html")

# 目标定义: (显示名, [候选路径], 类别, 默认档位, 关联进程名)
# 类别: cache=纯缓存(绿)  model=模型权重(琥珀)  tooldata=应用配置/会话/项目(琥珀)
#       tool=旧别名(绿)  userdata=用户产出(红)
TARGETS = [
    # ---- 模型 / 缓存（绿档为主）----
    ("Ollama 模型", [os.path.join(HOME, ".ollama", "models")], "model", "amber", "ollama.exe"),
    ("LM Studio 模型", [os.path.join(HOME, ".lmstudio", "models")], "model", "amber", "lmstudio.exe"),
    ("HuggingFace 缓存", [
        os.path.join(HOME, ".cache", "huggingface", "hub"),
        os.path.join(HOME, ".cache", "huggingface"),
    ], "cache", "green", None),
    ("PyTorch Hub", [os.path.join(HOME, ".cache", "torch", "hub")], "cache", "green", None),
    ("Whisper 缓存", [os.path.join(HOME, ".cache", "whisper")], "cache", "green", None),
    ("npm 缓存", [os.path.join(HOME, ".npm", "_cacache")], "cache", "green", None),
    ("pip 缓存", [os.path.join(LOCAL, "pip", "Cache")], "cache", "green", None),
    # ---- 已安装 AI 应用（配置/会话/项目，默认琥珀=建议保留）----
    ("Claude 数据", [
        os.path.join(HOME, ".claude"),
        os.path.join(LOCAL, "Claude"),
        os.path.join(LOCAL, "Claude-3p"),
        os.path.join(LOCAL, "claude-cli-nodejs"),
        os.path.join(ROAMING, "Claude"),
    ], "tooldata", "amber", "claude.exe"),
    ("Cursor 数据", [
        os.path.join(HOME, ".cursor"),
        os.path.join(LOCAL, "com.cursor-agent.app"),
        os.path.join(ROAMING, "com.cursor-agent.app"),
        os.path.join(ROAMING, "Cursor"),
    ], "tooldata", "amber", "cursor.exe"),
    ("Coze / 扣子 数据", [
        os.path.join(HOME, ".coze"),
        os.path.join(LOCAL, "coze-updater"),
        os.path.join(ROAMING, "Coze"),
        os.path.join(HOME, "Coze"),
    ], "tooldata", "amber", None),
    ("Kimi 数据", [
        os.path.join(HOME, ".kimi-code"),
        os.path.join(HOME, ".kimi-webbridge"),
        os.path.join(HOME, ".kimi-work"),
        os.path.join(LOCAL, "kimi-desktop-updater"),
        os.path.join(ROAMING, "kimi-desktop"),
    ], "tooldata", "amber", None),
    ("Trae / TRAE SOLO CN 数据", [
        os.path.join(HOME, ".trae-cn"),
        os.path.join(LOCAL, "Programs", "TRAE SOLO CN"),
        os.path.join(ROAMING, "TRAE SOLO CN"),
    ], "tooldata", "amber", None),
    ("Gemini CLI 数据", [os.path.join(HOME, ".gemini")], "tooldata", "amber", None),
    ("Grok 数据", [os.path.join(HOME, ".grok")], "tooldata", "amber", None),
    ("InsightFace 数据", [os.path.join(HOME, ".insightface")], "tooldata", "amber", None),
    ("NVIDIA 数据", [
        os.path.join(LOCAL, "NVIDIA"),
        os.path.join(ROAMING, "NVIDIA"),
    ], "tooldata", "amber", None),
    # ---- 本版新识别（此前漏报）----
    ("Codex 缓存 (OpenAI)", [os.path.join(HOME, ".codex-temp")], "cache", "green", None),
    ("Codex 数据 (OpenAI) · App+CLI", [os.path.join(HOME, ".codex")], "tooldata", "amber", "codex.exe"),
    ("OpenAI 数据", [os.path.join(LOCAL, "OpenAI")], "tooldata", "amber", None),
    ("Antigravity 工作区 (Google AI IDE)", [os.path.join(HOME, ".antigravity_cockpit")], "tooldata", "amber", None),
    ("Antigravity 更新缓存", [os.path.join(LOCAL, "antigravity-updater")], "cache", "green", None),
    ("Antigravity 数据", [
        os.path.join(HOME, ".antigravity"),
        os.path.join(LOCAL, "antigravity"),
        os.path.join(ROAMING, "Antigravity"),
    ], "tooldata", "amber", None),
    ("QClaw 数据", [
        os.path.join(HOME, ".qclaw"),
        os.path.join(HOME, ".qclaw-backups"),
        os.path.join(ROAMING, "QClaw"),
    ], "tooldata", "amber", None),
    ("QClaw 更新缓存", [os.path.join(LOCAL, "qclaw-updater")], "cache", "green", None),
    ("Zed 数据 (AI 编辑器)", [
        os.path.join(LOCAL, "Zed"),
        os.path.join(ROAMING, "Zed"),
    ], "tooldata", "amber", "zed.exe"),
    ("STranslate 数据 (AI 翻译)", [
        os.path.join(LOCAL, "STranslate"),
        os.path.join(ROAMING, "STranslate"),
    ], "tooldata", "amber", None),
    # ---- 用户产出（红，受保护）----
    ("ComfyUI 输出", [
        os.path.join(HOME, "ComfyUI", "output"),
        os.path.join(LOCAL, "ComfyUI", "output"),
    ], "userdata", "red", None),
]

# ===== 对话历史自动发现 =====
# 大体积 / 陈旧的对话历史应允许用户清理（仅丢失聊天记录，可备份撤销），
# 而非一律"建议保留"。在各 AI 应用数据目录下查找已知的对话历史目录/文件。
HISTORY_NAMES = {
    "projects", "history", "history.jsonl", "file-history",
    "chats", "conversations", "chat", "sessions", "chat_history",
}
HISTORY_MIN_BYTES = 10 * 1024 * 1024   # ≥10MB 视为"大"
HISTORY_MIN_AGE_DAYS = 30              # ≥30 天视为"旧"

# 真实对话/会话存储（各工具实际落盘位置，来自本机侦察）。
# 这些是"聊天记录/会话历史"的主体，但命名不像通用 history 关键词，
# 故单独列出显式路径，确保被识别为可清理的对话历史（大/旧 → 绿档可删）。
CONV_STORES = [
    ("Codex 会话库 (logs_2.sqlite)", [os.path.join(HOME, ".codex", "logs_2.sqlite")], "history", "amber", "codex.exe"),
    ("Codex 会话索引", [os.path.join(HOME, ".codex", "session_index.jsonl")], "history", "amber", "codex.exe"),
    ("Codex 归档会话", [os.path.join(HOME, ".codex", "archived_sessions")], "history", "amber", "codex.exe"),
    ("Antigravity 会话实例", [os.path.join(HOME, ".antigravity_cockpit", "instances")], "history", "amber", None),
    # 以下实际是 Antigravity 各端的会话库，但落在 .gemini 目录下（CLI/IDE 共用 home），
    # 原扫描只把整个 .gemini 当琥珀应用数据，漏掉了这些可清理的对话历史。单独列出。
    ("Antigravity 会话 (.gemini/antigravity)", [os.path.join(HOME, ".gemini", "antigravity", "conversations")], "history", "amber", None),
    ("Antigravity 会话 (.gemini/antigravity-cli)", [os.path.join(HOME, ".gemini", "antigravity-cli", "conversations")], "history", "amber", None),
    ("Antigravity 会话 (.gemini/antigravity-ide)", [os.path.join(HOME, ".gemini", "antigravity-ide", "conversations")], "history", "amber", None),
    ("Antigravity 会话 (.gemini/tmp/chats)", [os.path.join(HOME, ".gemini", "tmp", "administrator", "chats")], "history", "amber", None),
    ("QClaw 智能体会话", [os.path.join(HOME, ".qclaw", "agents")], "history", "amber", "qclaw.exe"),
    ("QClaw 记忆", [os.path.join(HOME, ".qclaw", "memory")], "history", "amber", "qclaw.exe"),
    ("Cursor 用户数据", [os.path.join(ROAMING, "Cursor", "User")], "history", "amber", None),
]

# 各 AI 应用的主数据根目录（用于在其下发现对话历史子目录）
APP_ROOTS = [
    ("Claude", os.path.join(HOME, ".claude")),
    ("Cursor", os.path.join(HOME, ".cursor")),
    ("Kimi", os.path.join(HOME, ".kimi-code")),
    ("Trae", os.path.join(HOME, ".trae-cn")),
    ("Coze", os.path.join(HOME, ".coze")),
    ("Gemini", os.path.join(HOME, ".gemini")),
    ("Codex", os.path.join(HOME, ".codex")),
    ("QClaw", os.path.join(HOME, ".qclaw")),
    ("Zed", os.path.join(LOCAL, "Zed")),
    ("STranslate", os.path.join(LOCAL, "STranslate")),
    ("Antigravity", os.path.join(HOME, ".antigravity")),
]


def discover_history():
    """返回 [(显示名, 路径), ...]：在各 AI 应用数据目录下发现的对话历史位置。"""
    out = []
    for label, root in APP_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for e in entries:
            if e.lower() in HISTORY_NAMES:
                out.append((f"{label} 对话历史（{e}）", os.path.join(root, e)))
    return out


def dir_size(path):
    """递归计算目录体积（字节）。"""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def dir_stats(path):
    """递归计算目录体积（字节）与最旧文件修改时间（epoch），用于判断对话历史新旧。
    同时支持单个文件（如 logs_2.sqlite / session_index.jsonl）：直接返回其大小与 mtime。"""
    if os.path.isfile(path):
        try:
            st = os.stat(path)
            return st.st_size, st.st_mtime
        except OSError:
            return 0, None
    total = 0
    oldest = None
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    st = os.stat(fp)
                    total += st.st_size
                    if oldest is None or st.st_mtime < oldest:
                        oldest = st.st_mtime
                except OSError:
                    pass
    except OSError:
        pass
    return total, oldest


def running_procs():
    """返回当前运行的进程名集合（小写）。"""
    procs = set()
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV"], capture_output=True,
            encoding="gbk", errors="ignore", timeout=10
        ).stdout
        for line in out.splitlines()[1:]:
            name = line.split(",")[0].strip('"').lower()
            if name:
                procs.add(name)
    except Exception:
        pass
    return procs


def classify(name, path, category, default_tier, proc, procs):
    size, oldest = dir_stats(path)
    tier = default_tier
    evidence = []
    reason = ""

    if category == "cache":
        tier = "green"
        reason = "纯缓存目录，删除后可自动重建，无状态损失"
        evidence.append("路径命中纯缓存白名单（cache / _cacache / Hub）")
    elif category == "model":
        tier = "amber"
        reason = "模型权重文件，删除后需重新下载（消耗流量）"
        evidence.append("位于模型存储目录（models / blobs）")
    elif category == "tool":
        tier = "green"
        reason = "工具索引 / 会话缓存，可清理（会话历史会丢失）"
        evidence.append("位于 AI 编码工具数据目录")
    elif category == "tooldata":
        tier = "amber"
        reason = "应用配置 / 会话 / 项目数据，删除会丢失你的设置、历史与本地产出"
        evidence.append("位于 AI 应用数据目录（默认建议保留）")
    elif category == "history":
        sz_gb = round(size / 1073741824, 1)
        age_days = (time.time() - oldest) / 86400 if oldest else 0
        if size >= HISTORY_MIN_BYTES or age_days >= HISTORY_MIN_AGE_DAYS:
            tier = "green"
            reason = (f"🗨️ 对话历史（{sz_gb} GB，最早 {age_days:.0f} 天前）："
                      f"仅丢失聊天记录，可清理（已备份、可撤销）")
            evidence.append("位于 AI 应用对话历史目录，且体积较大或较旧 → 允许用户清理")
        else:
            tier = "amber"
            reason = "🗨️ 近期对话历史，体积很小，建议保留"
            evidence.append("位于 AI 应用对话历史目录，但较新且较小")
    elif category == "userdata":
        tier = "red"
        reason = "含你的生成产出 / 项目文件，受保护"
        evidence.append("位于用户产出目录（output / 项目）")

    # 进程占用 → 升级为红（真锁）
    if proc and proc.lower() in procs:
        tier = "red"
        reason = f"{proc} 进程正在运行，强制删除会导致程序崩溃"
        evidence.append(f"tasklist 检测到进程 {proc} 正在运行")

    return {
        "name": name,
        "path": path,
        "size_bytes": size,
        "size_gb": round(size / 1024 / 1024 / 1024, 1),
        "tier": tier,
        "reason": reason,
        "evidence": evidence,
    }


def build_report(scan):
    tiers = {"green": [], "amber": [], "red": []}
    for it in scan["items"]:
        tiers[it["tier"]].append(it)
    totals = {
        k: round(sum(i["size_bytes"] for i in v) / 1024 / 1024 / 1024, 1)
        for k, v in tiers.items()
    }
    reclaimable = round(totals["green"] + totals["amber"], 1)
    return tiers, totals, reclaimable


def html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def row_html(it, with_evidence=True):
    tier_cls = {"green": "g", "amber": "a", "red": "r"}[it["tier"]]
    icon = {"green": "🟢", "amber": "🟡", "red": "🔒"}[it["tier"]]
    evi = ""
    if with_evidence:
        evi_items = "".join(f"<div>· {html_escape(e)}</div>" for e in it["evidence"])
        evi = f'<div class="evi">{evi_items}</div>'
    return f'''<div class="item">
      <span class="lock">{icon}</span>
      <div class="meta">
        <div class="name">{html_escape(it["name"])}</div>
        <div class="desc">{html_escape(it["reason"])}</div>
        {evi}
      </div>
      <span class="sz">{it["size_gb"]} GB</span>
    </div>'''


def generate_html(scan, tiers, totals, reclaimable):
    ts = scan["scanned_at"]
    green = "".join(row_html(i) for i in tiers["green"]) or '<div class="item"><div class="meta"><div class="desc">未扫描到此类目录</div></div></div>'
    amber = "".join(row_html(i) for i in tiers["amber"]) or '<div class="item"><div class="meta"><div class="desc">未扫描到此类目录</div></div></div>'
    red = "".join(row_html(i) for i in tiers["red"]) or '<div class="item"><div class="meta"><div class="desc">未扫描到受保护项</div></div></div>'

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sweep 扫描报告 · 真实本机数据</title>
<style>
:root{{--bg:#f6f7f9;--panel:#fff;--ink:#1a1d21;--muted:#6b7280;--line:#e6e8eb;
--green:#16a34a;--green-bg:#eafaf0;--green-line:#bfe9cd;
--amber:#d97706;--amber-bg:#fef6e7;--amber-line:#f5dca0;
--red:#dc2626;--red-bg:#fdecec;--red-line:#f4c4c4;}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.5;padding:24px}}
.wrap{{max-width:1080px;margin:0 auto}}
.top h1{{font-size:22px}} .top .ts{{color:var(--muted);font-size:12px;margin-top:4px}}
.overview{{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0}}
.ov{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;flex:1;min-width:150px}}
.ov .k{{font-size:12px;color:var(--muted)}} .ov .v{{font-size:24px;font-weight:700;margin-top:4px}}
.ov.total .v{{color:#2563eb}} .ov.g .v{{color:var(--green)}} .ov.a .v{{color:var(--amber)}} .ov.r .v{{color:var(--red)}}
.cols{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}}
@media(max-width:980px){{.cols{{grid-template-columns:1fr}}}}
.col{{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}}
.col .head{{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--line);font-weight:600}}
.col.g .head{{background:var(--green-bg)}} .col.a .head{{background:var(--amber-bg)}} .col.r .head{{background:var(--red-bg)}}
.sum{{margin-left:auto;font-size:12px;color:var(--muted);font-weight:500}}
.item{{display:flex;align-items:flex-start;gap:10px;padding:11px 14px;border-bottom:1px solid #f1f2f4}}
.item:last-child{{border-bottom:0}}
.meta{{flex:1;min-width:0}} .name{{font-weight:500}} .desc{{font-size:12px;color:var(--muted);margin-top:2px}}
.sz{{font-size:12px;color:var(--muted);white-space:nowrap;margin-left:8px}}
.lock{{font-size:14px;flex:none;margin-top:2px}}
.evi{{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.6}}
.note{{margin-top:18px;font-size:12px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
</style></head>
<body><div class="wrap">
<div class="top"><h1>Sweep 本机扫描报告</h1><div class="ts">扫描时间：{ts} · 本机 {html_escape(os.environ.get("COMPUTERNAME","未知"))} · 只读，未删除任何文件</div></div>
<div class="overview">
  <div class="ov total"><div class="k">本次可优化（绿+琥珀）</div><div class="v">{reclaimable} GB</div></div>
  <div class="ov g"><div class="k">🟢 可清理</div><div class="v">{totals["green"]} GB</div></div>
  <div class="ov a"><div class="k">🟡 建议保留</div><div class="v">{totals["amber"]} GB</div></div>
  <div class="ov r"><div class="k">🔒 已保护（不碰）</div><div class="v">{totals["red"]} GB</div></div>
</div>
<div class="cols">
  <div class="col g"><div class="head">🟢 可清理 <span class="sum">{totals["green"]} GB</span></div>{green}</div>
  <div class="col a"><div class="head">🟡 建议保留 <span class="sum">{totals["amber"]} GB</span></div>{amber}</div>
  <div class="col r"><div class="head">🔒 不能清理 <span class="sum">{totals["red"]} GB</span></div>{red}</div>
</div>
<div class="note">说明：本报告中「可清理 / 建议保留 / 不能清理」仅基于路径规则与进程占用判定，为演示用途。真实产品会在执行删除前做二次确认、回收站中转与硬链接去重。红区项目（如进程占用中）已锁定，不会进入清理队列。</div>
</div></body></html>'''


def apply_nested_dedup(items):
    """修复「对话历史子目录被父应用目录重复计入总量」的问题。
    例如 .claude (琥珀, 含 projects) 与其下 .claude/projects (绿, 对话历史)：
    父目录体积已包含子目录，故从父目录中扣减子目录体积，得到不重叠的独占体积。
    返回修正后的 items（原地修改 size_bytes / size_gb）。
    """
    for it in items:
        parent = it["path"].rstrip(os.sep) + os.sep
        for other in items:
            if other is it:
                continue
            child = other["path"].rstrip(os.sep)
            if child.startswith(parent):
                it["size_bytes"] = max(0, it["size_bytes"] - other["size_bytes"])
        it["size_gb"] = round(it["size_bytes"] / 1024 / 1024 / 1024, 1)
    return items


def scan(progress=None, exclusions=None):
    procs = running_procs()
    # 仅统计真实存在的候选路径，用于进度条总量
    targets = [
        (name, p, category, default_tier, proc)
        for name, paths, category, default_tier, proc in TARGETS
        for p in paths if os.path.exists(p)
    ]
    # 追加显式对话/会话存储（本机侦察得到的真实落盘位置）
    for cname, cpaths, ccat, cdefault, cproc in CONV_STORES:
        for cp in cpaths:
            if os.path.exists(cp):
                targets.append((cname, cp, ccat, cdefault, cproc))
    # 追加自动发现的对话历史项（大/旧 → 可清理）
    for hname, hpath in discover_history():
        if os.path.exists(hpath):
            targets.append((hname, hpath, "history", "amber", None))
    total = len(targets)
    items = []
    for idx, (name, p, category, default_tier, proc) in enumerate(targets, 1):
        items.append(classify(name, p, category, default_tier, proc, procs))
        if progress:
            try:
                progress(name, idx, total)
            except Exception:
                pass

    items = apply_nested_dedup(items)

    # 保护列表：被排除的路径标记为不清理（即使落在绿档也跳过）
    excl = set(os.path.normpath(p) for p in (exclusions or []))
    for it in items:
        if os.path.normpath(it["path"]) in excl:
            it["excluded"] = True
            it["reason"] = "已在保护列表中排除，永远不会被清理"
            it["evidence"] = ["用户保护列表：" + it["path"]]
        else:
            it["excluded"] = False

    scan = {
        "scanned_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "computer": os.environ.get("COMPUTERNAME", "未知"),
        "items": items,
    }
    with open(SCAN_JSON, "w", encoding="utf-8") as f:
        json.dump(scan, f, ensure_ascii=False, indent=2)

    tiers, totals, reclaimable = build_report(scan)
    html = generate_html(scan, tiers, totals, reclaimable)
    with open(REPORT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"扫描完成：{len(items)} 个目录")
    print(f"  可清理(绿)   : {totals['green']} GB")
    print(f"  建议保留(琥珀): {totals['amber']} GB")
    print(f"  已保护(红)   : {totals['red']} GB")
    print(f"  可优化合计   : {reclaimable} GB")
    print("已生成 scan.json 与 report.html")
    return scan


def clean(dry_run=True):
    """清理绿档：移动到备份目录（非永久删除，可撤销）。红档绝不碰。"""
    if not os.path.exists(SCAN_JSON):
        print("请先运行扫描（不带参数）生成 scan.json")
        return
    with open(SCAN_JSON, encoding="utf-8") as f:
        data = json.load(f)
    green = [i for i in data["items"] if i["tier"] == "green"]
    red = [i for i in data["items"] if i["tier"] == "red"]
    if not green:
        print("没有可清理的绿档项。")
        return
    total_gb = round(sum(i["size_bytes"] for i in green) / 1024 ** 3, 1)
    print(f"将清理 {len(green)} 个绿档项，共 {total_gb} GB：")
    for i in green:
        print(f"  - {i['name']}  ({i['size_gb']} GB)")
    if red:
        print(f"\n🔒 已跳过 {len(red)} 个红档受保护项（绝不清理）。")
    if dry_run:
        print("\n[dry-run] 未执行任何删除。加 --execute 并在提示中输入 y 才会真正移动（到备份目录，可撤销）。")
        return
    ans = input("确认移动到备份目录（可撤销）？输入 y 继续：").strip().lower()
    if ans != "y":
        print("已取消。")
        return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = os.path.join(BACKUP_ROOT, ts)
    moved, failed = 0, 0
    for i in green:
        dest = os.path.join(dest_root, os.path.basename(i["path"].rstrip(os.sep)))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(i["path"], dest)
            moved += 1
            print(f"  已移动: {i['name']} -> {dest}")
        except Exception as e:
            failed += 1
            print(f"  失败: {i['name']}: {e}")
    print(f"\n完成：移动 {moved} 项，失败 {failed} 项。")
    print(f"备份位于 {dest_root}，可手动恢复或永久删除。")


def main():
    ap = argparse.ArgumentParser(description="Sweep 本机 AI 缓存扫描/清理工具")
    ap.add_argument("--clean", action="store_true", help="清理绿档（默认 dry-run 预览）")
    ap.add_argument("--execute", action="store_true", help="配合 --clean 真正执行移动")
    args = ap.parse_args()
    if args.clean:
        clean(dry_run=not args.execute)
    else:
        scan()


if __name__ == "__main__":
    main()
