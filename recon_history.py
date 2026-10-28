import os, time

HOME = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
ROAMING = os.path.join(HOME, "AppData", "Roaming")

CHAT_KW = ("project", "history", "chat", "conversation", "session", "message",
           "composer", "author", "thread", "dialog", "talk", "logs", "记忆", "对话")

def sz(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0

def dir_size(path):
    t = 0
    try:
        for r, _, fs in os.walk(path):
            for f in fs:
                t += sz(os.path.join(r, f))
    except OSError:
        pass
    return t

def gb(b):
    return f"{b/1024**3:.2f} GB" if b >= 1024**2 else f"{b/1024:.0f} KB"

ROOTS = {
    "Claude":      [os.path.join(HOME, ".claude"), os.path.join(LOCAL, "Claude"), os.path.join(ROAMING, "Claude")],
    "Cursor":      [os.path.join(HOME, ".cursor"), os.path.join(LOCAL, "com.cursor-agent.app"), os.path.join(ROAMING, "com.cursor-agent.app"), os.path.join(ROAMING, "Cursor")],
    "Kimi":        [os.path.join(HOME, ".kimi-code"), os.path.join(HOME, ".kimi-webbridge"), os.path.join(HOME, ".kimi-work"), os.path.join(ROAMING, "kimi-desktop")],
    "Trae":        [os.path.join(HOME, ".trae-cn"), os.path.join(ROAMING, "TRAE SOLO CN"), os.path.join(LOCAL, "Programs", "TRAE SOLO CN")],
    "Coze":        [os.path.join(HOME, ".coze"), os.path.join(ROAMING, "Coze")],
    "Gemini":      [os.path.join(HOME, ".gemini")],
    "Codex":       [os.path.join(HOME, ".codex"), os.path.join(HOME, ".codex-temp"), os.path.join(LOCAL, "OpenAI")],
    "QClaw":       [os.path.join(HOME, ".qclaw"), os.path.join(HOME, ".qclaw-backups"), os.path.join(ROAMING, "QClaw")],
    "Zed":         [os.path.join(LOCAL, "Zed"), os.path.join(ROAMING, "Zed")],
    "STranslate":  [os.path.join(LOCAL, "STranslate"), os.path.join(ROAMING, "STranslate")],
    "Antigravity": [os.path.join(HOME, ".antigravity"), os.path.join(HOME, ".antigravity_cockpit"), os.path.join(LOCAL, "antigravity"), os.path.join(ROAMING, "Antigravity")],
}

for tool, roots in ROOTS.items():
    print("=" * 72)
    print(f"■ {tool}")
    found_any = False
    for root in roots:
        if not os.path.isdir(root):
            continue
        print(f"  📂 {root}")
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for e in entries:
            full = os.path.join(root, e)
            en = e.lower()
            is_chat = any(k in en for k in CHAT_KW)
            if os.path.isdir(full):
                b = dir_size(full)
                mark = "  ⬅ 聊天相关?" if is_chat else ""
                print(f"     [dir ] {e:28s} {gb(b):>10s}{mark}")
                found_any = found_any or is_chat
            else:
                b = sz(full)
                mark = "  ⬅ 聊天相关?" if is_chat else ""
                print(f"     [file] {e:28s} {gb(b):>10s}{mark}")
                found_any = found_any or is_chat
    if not found_any:
        print("     (该工具根下未直接命中聊天关键词，可能更深或命名不同)")
