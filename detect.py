#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sweep 全面 AI 探测：枚举本机已安装程序(注册表+开始菜单)、正在运行的进程、
以及一大批已知 AI 工具的程序/模型/数据目录，找出本机真实存在的 AI 资产。
只读，不删除任何文件。
"""
import os
import json
import winreg
import subprocess
import datetime

HOME = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
ROAMING = os.path.join(HOME, "AppData", "Roaming")
PROGDATA = os.environ.get("ProgramData", "C:/ProgramData")

# 用于从全量程序名里筛出"AI 相关"的关键词
AI_KW = [
    "ai", "llm", "gpt", "chatgpt", "claude", "cursor", "ollama", "lm studio",
    "comfyui", "stable diffusion", "midjourney", "deepseek", "kimi", "豆包",
    "通义", "文心", "千问", "qwen", "copilot", "codeium", "tabnine", "jan",
    "perplexity", "gemini", "whisper", "pytorch", "tensorflow", "diffusers",
    "vllm", "anythingllm", "textgen", "openwebui", "trae", "windsurf", "coze",
    "grok", "llama", "embedding", "rag", "nvidia", "cudnn", "onnx", "insightface",
    "facefusion", "sd webui", "automatic1111", "fooocus", "flux", "doubao",
    "yuanbao", "智谱", "glm", "kling", "可灵", "runway", "hedra", "pika",
    "hunyuan", "元宝", "阶跃", "step", "minimax", "moonshot",
]


def is_ai(name):
    n = (name or "").lower()
    return any(k.lower() in n for k in AI_KW)


def dir_size(path):
    total = 0
    try:
        for root, _d, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def enum_registry():
    found = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hkey, path in roots:
        try:
            k = winreg.OpenKey(hkey, path)
            cnt = winreg.QueryInfoKey(k)[0]
            for i in range(cnt):
                sub = winreg.EnumKey(k, i)
                sk = winreg.OpenKey(k, sub)
                try:
                    found.append(winreg.QueryValueEx(sk, "DisplayName")[0])
                except OSError:
                    pass
                try:
                    winreg.CloseKey(sk)
                except OSError:
                    pass
            winreg.CloseKey(k)
        except OSError:
            pass
    return found


def enum_startmenu():
    found = []
    bases = [
        os.path.join(ROAMING, "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(PROGDATA, "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    for base in bases:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".lnk"):
                    found.append(f[:-4])
    return found


def enum_procs():
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


# 扩充的 AI 工具路径检测：(显示名, [候选路径], 类型)
PATHS = [
    ("ChatGPT", [os.path.join(LOCAL, "Programs", "chatgpt"), os.path.join(LOCAL, "ChatGPT"), os.path.join(ROAMING, "ChatGPT")], "app"),
    ("DeepSeek", [os.path.join(LOCAL, "Programs", "DeepSeek"), os.path.join(ROAMING, "DeepSeek")], "app"),
    ("Kimi", [os.path.join(LOCAL, "Programs", "Kimi")], "app"),
    ("豆包/Doubao", [os.path.join(LOCAL, "Programs", "Doubao")], "app"),
    ("通义/千问", [os.path.join(LOCAL, "Programs", "Tongyi"), os.path.join(LOCAL, "Programs", "Qianwen")], "app"),
    ("Trae", [os.path.join(LOCAL, "Programs", "Trae")], "app"),
    ("Windsurf", [os.path.join(LOCAL, "Programs", "Windsurf")], "app"),
    ("Codeium", [os.path.join(LOCAL, "Programs", "Codeium")], "app"),
    ("Tabnine", [os.path.join(LOCAL, "Programs", "Tabnine")], "app"),
    ("Jan", [os.path.join(HOME, ".jan"), os.path.join(LOCAL, "Jan")], "app"),
    ("AnythingLLM", [os.path.join(LOCAL, "AnythingLLM")], "app"),
    ("TextGen WebUI", [os.path.join(HOME, "text-generation-webui")], "app"),
    ("Fooocus", [os.path.join(HOME, "Fooocus")], "app"),
    ("Ollama 程序", [os.path.join(LOCAL, "Ollama"), os.path.join(LOCAL, "Programs", "Ollama")], "app"),
    ("Ollama 模型", [os.path.join(HOME, ".ollama", "models")], "model"),
    ("LM Studio 程序", [os.path.join(LOCAL, "LM Studio"), os.path.join(LOCAL, "Programs", "LM Studio")], "app"),
    ("LM Studio 模型", [os.path.join(HOME, ".lmstudio", "models")], "model"),
    ("Cursor 程序", [os.path.join(LOCAL, "Programs", "cursor"), os.path.join(LOCAL, "cursor")], "app"),
    ("Cursor 数据", [os.path.join(HOME, ".cursor"), os.path.join(LOCAL, "cursor")], "data"),
    ("Claude 程序", [os.path.join(LOCAL, "Programs", "Claude"), os.path.join(LOCAL, "AnthropicClaude")], "app"),
    ("Claude 数据", [os.path.join(HOME, ".claude"), os.path.join(LOCAL, "Claude")], "data"),
    ("ComfyUI", [os.path.join(HOME, "ComfyUI")], "app"),
    ("SD WebUI", [os.path.join(HOME, "stable-diffusion-webui")], "app"),
    ("HuggingFace 缓存", [os.path.join(HOME, ".cache", "huggingface")], "data"),
    ("PyTorch Hub", [os.path.join(HOME, ".cache", "torch")], "data"),
    ("Whisper 缓存", [os.path.join(HOME, ".cache", "whisper")], "data"),
    ("npm 缓存", [os.path.join(HOME, ".npm", "_cacache")], "data"),
    ("pip 缓存", [os.path.join(LOCAL, "pip", "Cache")], "data"),
]

KIND_LABEL = {"app": "程序", "model": "模型", "data": "缓存/数据"}


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def main():
    installed = list(dict.fromkeys(enum_registry() + enum_startmenu()))
    ai_installed = sorted({n for n in installed if is_ai(n)})
    procs = enum_procs()
    ai_procs = sorted({p for p in procs if is_ai(p)})

    rows = []
    for name, paths, kind in PATHS:
        found = [p for p in paths if os.path.exists(p)]
        if not found:
            continue
        size = sum(dir_size(p) for p in found)
        if size == 0:
            continue
        rows.append({
            "name": name, "kind": kind,
            "size_gb": round(size / 1073741824, 1),
            "paths": found,
            "running": any(is_ai(p) for p in [os.path.basename(f) for f in found]),
        })
    rows.sort(key=lambda x: -x["size_gb"])

    total = round(sum(r["size_gb"] for r in rows), 1)

    ai_html = "".join(f'<div class="chip">{html_escape(n)}</div>' for n in ai_installed) or '<div class="muted">未从安装列表识别到 AI 应用</div>'
    proc_html = "".join(f'<div class="chip run">{html_escape(p)}</div>' for p in ai_procs) or '<div class="muted">当前无 AI 进程在运行</div>'
    rows_html = "".join(
        f'''<div class="item"><div class="meta">
          <div class="name">{html_escape(r["name"])} <span class="tag">{KIND_LABEL[r["kind"]]}</span>{'<span class="tag run">运行中</span>' if r["running"] else ''}</div>
          <div class="desc">{html_escape(r["paths"][0])}</div></div>
          <span class="sz">{r["size_gb"]} GB</span></div>'''
        for r in rows
    ) or '<div class="item"><div class="meta"><div class="desc">未检测到已知 AI 资产目录</div></div></div>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>本机 AI 全面探测</title>
<style>
:root{{--bg:#f6f7f9;--panel:#fff;--ink:#1a1d21;--muted:#6b7280;--line:#e6e8eb;--brand:#3D7BF0;--green:#16a34a;}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.5;padding:24px}}
.wrap{{max-width:920px;margin:0 auto}}
.top h1{{font-size:22px}} .top .ts{{color:var(--muted);font-size:12px;margin-top:4px}}
.ov{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}}
.ov .k{{font-size:12px;color:var(--muted)}} .ov .v{{font-size:22px;font-weight:700;color:var(--brand)}}
.section{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}}
.section h3{{font-size:14px;margin-bottom:10px}}
.chip{{display:inline-block;background:#eaf0fe;color:#2563eb;border:1px solid #cfe0fd;border-radius:8px;padding:4px 10px;margin:3px;font-size:13px}}
.chip.run{{background:#eafaf0;color:var(--green);border-color:#bfe9cd}}
.muted{{color:var(--muted);font-size:13px}}
.item{{display:flex;align-items:flex-start;gap:10px;padding:11px 0;border-bottom:1px solid #f1f2f4}}
.item:last-child{{border-bottom:0}}
.meta{{flex:1;min-width:0}} .name{{font-weight:500}} .desc{{font-size:12px;color:var(--muted);margin-top:2px;word-break:break-all}}
.tag{{font-size:11px;color:#2563eb;background:#eaf0fe;border:1px solid #cfe0fd;border-radius:6px;padding:1px 6px;margin-left:4px}}
.tag.run{{color:var(--green);background:#eafaf0;border-color:#bfe9cd}}
.sz{{font-size:13px;color:var(--muted);white-space:nowrap;margin-left:8px}}
.note{{font-size:12px;color:var(--muted);margin-top:8px}}
</style></head>
<body><div class="wrap">
<div class="top"><h1>本机 AI 全面探测</h1><div class="ts">探测时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} · 本机 {html_escape(os.environ.get("COMPUTERNAME","未知"))} · 只读</div></div>

<div class="ov"><div class="k">已知 AI 资产目录总占用（路径探测）</div><div class="v">{total} GB</div></div>

<div class="section"><h3>① 已安装程序中发现 AI 应用（注册表 + 开始菜单，共 {len(ai_installed)} 个）</h3>{ai_html}
<div class="note">↑ 这是从本机全部已安装程序里，按 AI 关键词筛出来的。可能含误判（如名字带 ai 的非 AI 软件）。</div></div>

<div class="section"><h3>② 当前正在运行的 AI 进程（{len(ai_procs)} 个）</h3>{proc_html}</div>

<div class="section"><h3>③ 已知 AI 工具目录体积（按体积降序）</h3>{rows_html}</div>

<div class="note">说明：本探测只读。已安装程序清单覆盖注册表与开始菜单；路径体积覆盖常见 AI 工具的默认目录。仍有 AI 应用可能装在非默认路径、或以浏览器网页形式存在（如网页版 ChatGPT/DeepSeek），无法被文件系统探测捕获。</div>
</div></body></html>'''

    with open("detect.html", "w", encoding="utf-8") as f:
        f.write(html)
    with open("detect.json", "w", encoding="utf-8") as f:
        json.dump({"ai_installed": ai_installed, "ai_procs": ai_procs, "asset_rows": rows}, f, ensure_ascii=False, indent=2)

    print(f"已安装 AI 应用（识别 {len(ai_installed)}）：")
    for n in ai_installed:
        print("  - " + n)
    print(f"\n正在运行的 AI 进程（{len(ai_procs)}）：")
    for p in ai_procs:
        print("  - " + p)
    print(f"\n已知 AI 目录总占用：{total} GB")


if __name__ == "__main__":
    main()
