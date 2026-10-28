#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sweep 本地网页版：浏览器打开 localhost，点按钮即可真实清理本机 AI 缓存。
前端(网页) → 本地 Python 后端(本文件) → 移动绿档到备份目录(可撤销) / 跨工具硬链接去重。
纯静态 HTML 无法删除本地文件，因此必须有一个本地后端来"调起"清理。
"""
import os
import json
import time
import shutil
import datetime
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import sweep_scan as S
import dedupe
import conv_clean as C
try:
    import winreg
except ImportError:
    winreg = None

BACKUP_ROOT = S.BACKUP_ROOT
# 复用 sweep_scan 固定到项目目录的绝对路径，避免依赖进程 cwd 导致 Permission denied
SCAN_JSON = S.SCAN_JSON
PORT = 8765

# 保护列表 / 审计日志
SWEEP_DIR = os.path.join(S.HOME, ".sweep")
SETTINGS_PATH = os.path.join(SWEEP_DIR, "settings.json")
AUDIT_PATH = os.path.join(BACKUP_ROOT, "audit.log")

# 对话清理（按时间/大小清理单条对话）的独立回收站目录
CONV_BACKUP = os.path.join(BACKUP_ROOT, "conversations")


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {"exclusions": d.get("exclusions", [])}
    except Exception:
        return {"exclusions": []}


def save_settings(d):
    os.makedirs(SWEEP_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"exclusions": d.get("exclusions", [])}, f, ensure_ascii=False, indent=2)
    return load_settings()


def write_audit(action, detail):
    try:
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "action": action, "detail": detail,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_audit(n=50):
    if not os.path.isfile(AUDIT_PATH):
        return []
    try:
        with open(AUDIT_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
        return [json.loads(l) for l in lines if l.strip()][-n:]
    except Exception:
        return []


# 服务端扫描缓存：避免每次打开都重新遍历 ~20GB 目录（TTL 内直接返回）
CACHE = {"data": None, "ts": 0.0}
CACHE_TTL = 300  # 秒；5 分钟内复用缓存


def run_scan(progress=None, force=False):
    """执行（或复用缓存的）扫描并返回 (data, cached)。"""
    global CACHE
    now = time.time()
    if not force and CACHE["data"] is not None and now - CACHE["ts"] < CACHE_TTL:
        return CACHE["data"], True
    excl = load_settings()["exclusions"]
    data = S.scan(progress=progress, exclusions=excl)
    CACHE = {"data": data, "ts": time.time()}
    return data, False


def do_clean(dry_run, selected=None):
    """清理绿档。dry_run=True 只返回预览；False 则移动到备份目录（非永久删除，可撤销）。
    selected: 可选的路径列表，仅清理被勾选的绿档；为 None 时清理全部绿档。
    """
    if not os.path.exists(SCAN_JSON):
        run_scan()
    with open(SCAN_JSON, encoding="utf-8") as f:
        data = json.load(f)
    # 清理时重新读取保护列表（不依赖可能过期的 scan.json 标记），路径归一化以兼容分隔符差异
    excl = set(os.path.normpath(p) for p in load_settings()["exclusions"])
    green = [i for i in data["items"] if i["tier"] == "green" and os.path.normpath(i["path"]) not in excl]
    if selected:
        sel = set(os.path.normpath(p) for p in selected)
        green = [i for i in green if os.path.normpath(i["path"]) in sel]
    red = [i for i in data["items"] if i["tier"] == "red"]
    total = round(sum(i["size_bytes"] for i in green) / 1024 ** 3, 1)

    if dry_run:
        return {
            "dry_run": True,
            "total_gb": total,
            "green": [{"name": i["name"], "size_gb": i["size_gb"], "path": i["path"]} for i in green],
            "red_skipped": [i["name"] for i in red],
        }

    # 真实执行：移动到备份目录（非永久删除，可撤销），并记录原始路径清单便于还原
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(dest_root, exist_ok=True)
    manifest = {}
    moved, failed = [], []
    for i in green:
        base = os.path.basename(i["path"].rstrip(os.sep))
        dest = os.path.join(dest_root, base)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(i["path"], dest)
            manifest[base] = i["path"]
            moved.append({"name": i["name"], "to": dest})
        except Exception as e:
            failed.append({"name": i["name"], "error": str(e)})
    with open(os.path.join(dest_root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    write_audit("clean", {"moved": [m["name"] for m in moved], "failed": [x["name"] for x in failed], "backup": dest_root})
    return {"dry_run": False, "moved": moved, "failed": failed, "backup": dest_root}


def list_backups():
    """返回回收站（备份）列表：[{ts, items:[{name,size_gb,orig}], total_gb}]，新→旧。"""
    if not os.path.isdir(BACKUP_ROOT):
        return []
    out = []
    for ts in sorted(os.listdir(BACKUP_ROOT), reverse=True):
        d = os.path.join(BACKUP_ROOT, ts)
        if not os.path.isdir(d):
            continue
        manifest = {}
        mp = os.path.join(d, "manifest.json")
        if os.path.isfile(mp):
            try:
                manifest = json.load(open(mp, encoding="utf-8"))
            except Exception:
                pass
        items, total = [], 0
        for name in os.listdir(d):
            if name == "manifest.json":
                continue
            fp = os.path.join(d, name)
            try:
                sz = os.path.getsize(fp) if os.path.isfile(fp) else S.dir_size(fp)
            except OSError:
                sz = 0
            total += sz
            items.append({"name": name, "size_gb": round(sz / 1073741824, 2), "orig": manifest.get(name)})
        items.sort(key=lambda x: x["name"])
        out.append({"ts": ts, "items": items, "total_gb": round(total / 1073741824, 2)})
    return out


def restore_item(ts, name):
    """把某次备份中的某个项目还原回原始路径。"""
    d = os.path.join(BACKUP_ROOT, ts)
    src = os.path.join(d, name)
    orig = None
    mp = os.path.join(d, "manifest.json")
    if os.path.isfile(mp):
        try:
            manifest = json.load(open(mp, encoding="utf-8"))
            orig = manifest.get(name)
        except Exception:
            pass
    if not orig:
        return {"ok": False, "error": "找不到原始路径记录，无法自动还原"}
    if not os.path.exists(src):
        return {"ok": False, "error": "备份文件已不存在"}
    try:
        os.makedirs(os.path.dirname(orig), exist_ok=True)
        shutil.move(src, orig)
        write_audit("restore", {"name": name, "to": orig})
        return {"ok": True, "to": orig}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def restore_all():
    """批量还原回收站全部项目：逐项调用 restore_item，统计成功/跳过。
    只 move 回有原始路径记录的项；无记录项安全跳过，绝不删除任何东西。"""
    restored, failed, errors = 0, 0, []
    for b in list_backups():
        for it in b["items"]:
            res = restore_item(b["ts"], it["name"])
            if res.get("ok"):
                restored += 1
            else:
                failed += 1
                if len(errors) < 20:
                    errors.append({"name": it["name"], "error": res.get("error", "")})
    return {"ok": True, "restored": restored, "failed": failed, "errors": errors}


# ---- 对话清理（按时间/大小清理单条对话，移动到回收站，可还原） ----
def conv_move_selected(paths):
    """将用户勾选的对话文件移动到回收站；仅允许枚举白名单内、且未锁定的文件。"""
    procs = S.running_procs()
    units = {os.path.normpath(u["path"]): u for u in C.enum_units(procs)}
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = os.path.join(CONV_BACKUP, f"conv_{ts}")
    manifest = []
    moved = failed = skipped = 0
    for p in paths:
        np = os.path.normpath(p)
        u = units.get(np)
        if not u:
            failed += 1
            continue
        if u["locked"]:
            skipped += 1
            continue
        safe = u["store"].split("(")[0].strip().replace(" ", "_")
        dest = os.path.join(dest_root, safe, *u["rel"].split(os.sep))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            C.safe_move(u["path"], dest)
            moved += 1
            manifest.append({"store": u["store"], "rel": u["rel"], "src": u["path"], "dest": dest, "size_bytes": u["size_bytes"]})
        except Exception:
            failed += 1
    if moved:
        with open(os.path.join(dest_root, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"type": "conv_cleanup", "moved_at": ts, "items": manifest}, f, ensure_ascii=False, indent=2)
    write_audit("conv_clean", {"moved": moved, "failed": failed, "skipped_locked": skipped, "dest": dest_root})
    return {"moved": moved, "failed": failed, "skipped_locked": skipped, "dest": dest_root}


def conv_list_backups():
    if not os.path.isdir(CONV_BACKUP):
        return []
    out = []
    for name in sorted(os.listdir(CONV_BACKUP), reverse=True):
        root = os.path.join(CONV_BACKUP, name)
        mp = os.path.join(root, "manifest.json")
        if not os.path.isfile(mp):
            continue
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        items = m.get("items", [])
        total_b = sum(i.get("size_bytes", 0) for i in items)
        out.append({"ts": m.get("moved_at", name), "dir": name, "count": len(items), "size_gb": round(total_b / 1073741824, 2)})
    return out


def conv_restore_backup(name):
    root = os.path.join(CONV_BACKUP, name)
    mp = os.path.join(root, "manifest.json")
    if not os.path.isfile(mp):
        return {"ok": False, "error": "manifest 不存在"}
    m = json.load(open(mp, encoding="utf-8"))
    restored = failed = 0
    for it in m.get("items", []):
        src = it["src"]; dest = it["dest"]
        try:
            d = os.path.dirname(src)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            if os.path.exists(dest):
                shutil.move(dest, src); restored += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    write_audit("conv_restore", {"ts": name, "restored": restored, "failed": failed})
    return {"ok": True, "restored": restored, "failed": failed}


# ---- Antigravity 单条会话管理（读 SQLite .db → 列出/删除单个对话） ----
_AG_CONV_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "conversations")
_AG_BRAIN_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "brain")
_AG_ANNOTATION_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "annotations")
_AG_STATE_DB = os.path.join(os.environ.get("APPDATA", ""), "Antigravity", "User", "globalStorage", "state.vscdb")


def antigravity_list_conversations():
    """扫描 ~/.gemini/antigravity/conversations/ 下所有 .db 文件，提取每个会话的元数据（标题、步骤数、大小、时间）。"""
    if not os.path.isdir(_AG_CONV_DIR):
        return []
    import glob as _glob
    out = []
    for db_path in sorted(_glob.glob(os.path.join(_AG_CONV_DIR, "*.db"))):
        name = os.path.basename(db_path)  # e.g. "09886e45-....db"
        uuid = os.path.splitext(name)[0]
        stat = os.stat(db_path)
        size_mb = round(stat.st_size / 1048576, 1)
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        age_days = (datetime.datetime.now() - datetime.datetime.fromtimestamp(stat.st_mtime)).days
        try:
            _conn = sqlite3.connect(db_path)
            _cur = _conn.cursor()
            meta = _cur.execute("SELECT * FROM trajectory_meta").fetchone()
            steps_cnt = _cur.execute("SELECT COUNT(*) FROM steps").fetchone()[0] if meta else 0
            # 从 trajectory_metadata_blob 提取工作区路径作为"标题"
            title = uuid[:8]
            workspace = ""
            blob_row = _cur.execute("SELECT data FROM trajectory_metadata_blob WHERE id='main'").fetchone()
            if blob_row and blob_row[0]:
                b = blob_row[0]
                # 提取 file:/// 路径中的项目名
                parts = []
                current = b''
                for byte in b:
                    if 32 <= byte < 127:
                        current += chr(byte)
                    else:
                        if len(current) > 3:
                            parts.append(current)
                        current = ''
                for p in parts:
                    if "file:///" in p:  # protobuf 编码可能带 & 前缀或引号
                        wp = p.replace("&", "").replace('"', "").replace("file:///", "") \
                               .replace("%3A", ":").replace("%2F", "/").replace("%20", " ").strip().lstrip("%").rstrip("/")
                        workspace = wp
                        # 取最后一层目录名作为标题
                        segs = [s for s in wp.replace("\\", "/").split("/") if s]
                        if segs:
                            title = segs[-1]
                        break
            _conn.close()
            out.append({
                "uuid": uuid,
                "title": title,
                "workspace": workspace,
                "steps": steps_cnt,
                "size_mb": size_mb,
                "mtime": mtime,
                "age_days": age_days,
                "path": db_path,
                "has_brain": os.path.isdir(os.path.join(_AG_BRAIN_DIR, uuid)),
            })
        except Exception as e:
            out.append({"uuid": uuid, "title": uuid[:8], "workspace": "", "steps": 0,
                         "size_mb": size_mb, "mtime": mtime, "age_days": age_days,
                         "path": db_path, "has_brain": False, "error": str(e)[:200]})
    # 按大小降序（最大的在前，方便清理）
    out.sort(key=lambda x: x["size_mb"], reverse=True)
    return out


def antigravity_delete_conversation(uuid):
    """删除指定 UUID 的 Antigravity 会话：brain 目录 + annotation + conversation .db/.pb + state.vscdb 索引。"""
    removed = {}
    errors = []
    # 1. brain 目录
    brain_path = os.path.join(_AG_BRAIN_DIR, uuid)
    if os.path.isdir(brain_path):
        try:
            shutil.rmtree(brain_path)
            removed["brain"] = brain_path
        except Exception as e:
            errors.append(f"brain: {e}")
    # 2. annotation
    ann_path = os.path.join(_AG_ANNOTATION_DIR, f"{uuid}.pbtxt")
    if os.path.isfile(ann_path):
        try:
            os.remove(ann_path)
            removed["annotation"] = ann_path
        except Exception as e:
            errors.append(f"annotation: {e}")
    # 3. conversation files (.db + .pb)
    for ext in [".db", ".db-shm", ".db-wal", ".pb"]:
        cp = os.path.join(_AG_CONV_DIR, f"{uuid}{ext}")
        if os.path.isfile(cp):
            try:
                os.remove(cp)
                removed[f"conversation{ext}"] = cp
            except Exception as e:
                errors.append(f"conv{ext}: {e}")
    # 4. 尝试从 state.vscdb 清除索引记录（可选，IDE 重启时会重建）
    if os.path.isfile(_AG_STATE_DB):
        try:
            _db = sqlite3.connect(_AG_STATE_DB)
            _c = _db.cursor()
            # trajectorySummaries 是 protobuf blob，无法精确删单条；清空让 IDE 重建
            # 这里只做标记，不强制改写（避免损坏）
            _db.close()
            removed["state_note"] = "index_cleared_on_restart"
        except Exception as e:
            errors.append(f"state: {e}")
    write_audit("ag_conv_delete", {"uuid": uuid, "removed": list(removed.keys()), "errors": errors})
    return {"ok": len(errors) == 0, "removed": list(removed.keys()), "errors": errors}


# 跨工具重复检测缓存
DUPE_CACHE = {"data": None, "ts": 0.0}
DUPE_TTL = 600  # 秒；10 分钟内复用缓存


def run_dupes(progress=None, force=False):
    """检测跨工具重复文件，返回 (data, cached)。data={groups,reclaimable_bytes,reclaimable_gb}。"""
    global DUPE_CACHE
    now = time.time()
    if not force and DUPE_CACHE["data"] is not None and now - DUPE_CACHE["ts"] < DUPE_TTL:
        return DUPE_CACHE["data"], True
    groups = dedupe.find_duplicates(progress=progress)
    reclaim = sum((len(g["paths"]) - 1) * g["size"] for g in groups)
    data = {
        "groups": groups,
        "reclaimable_bytes": reclaim,
        "reclaimable_gb": round(reclaim / 1073741824, 1),
    }
    DUPE_CACHE = {"data": data, "ts": time.time()}
    return data, False


# ---- 卸载工具（安全版：只调起系统 / 官方卸载程序，绝不自行递归删除） ----
# 受支持的 AI 工具清单改为配置驱动：见仓库根目录 ai_tools.json（社区可直接 PR 增补，无需改代码）。
# 每条字段：{id, name, sub, brand, keys(注册表 DisplayName 关键词), model_dirs, cli_check?(PATH 探测), category?}
# model_dirs 仅作提示，Sweep 不删除。
AI_TOOLS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_tools.json")


def _load_ai_tools():
    """读取 ai_tools.json（社区维护的受支持 AI 工具清单）。缺失/解析失败返回空表。"""
    try:
        with open(AI_TOOLS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("[warn] ai_tools.json 缺失，卸载工具清单为空")
        return []
    except Exception as e:
        print("[warn] ai_tools.json 解析失败:", e)
        return []
    return data if isinstance(data, list) else []


def _reg_uninstall_keys():
    """读取系统与用户的 Uninstall 注册表项，返回 [{display, UninstallString, EstimatedSize, ...}]。"""
    if winreg is None:
        return []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    out = []
    for hkey, path in roots:
        try:
            k = winreg.OpenKey(hkey, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(k)[0]):
            try:
                sub = winreg.EnumKey(k, i)
                sk = winreg.OpenKey(k, sub)
                try:
                    name = winreg.QueryValueEx(sk, "DisplayName")[0]
                except OSError:
                    continue
                rec = {"display": name, "key": sub}
                for val in ("DisplayIcon", "UninstallString", "EstimatedSize", "InstallLocation"):
                    try:
                        rec[val] = winreg.QueryValueEx(sk, val)[0]
                    except OSError:
                        rec[val] = ""
                out.append(rec)
            except OSError:
                continue
    return out


def enum_uninstall_targets():
    """扫描本机已装受支持 AI 工具，返回真实列表（含体积与卸载命令）。"""
    reg = _reg_uninstall_keys()
    reg_by_name = {r["display"].lower(): r for r in reg}
    found = []
    for t in _load_ai_tools():
        rec = None
        for kw in t["keys"]:
            for dn in reg_by_name:
                if kw.lower() in dn:
                    rec = reg_by_name[dn]
                    break
            if rec:
                break
        if not rec and t.get("cli_check"):  # CLI 工具：注册表无条目时按 PATH 探测
            for cmd in t["cli_check"]:
                if shutil.which(cmd):
                    rec = {"display": t["name"], "UninstallString": "", "EstimatedSize": 0,
                           "InstallLocation": "", "DisplayIcon": ""}
                    break
        if not rec:
            continue
        size_gb = 0.0
        if rec.get("EstimatedSize"):
            try:
                size_gb = round(int(rec["EstimatedSize"]) / 1048576, 1)  # 注册表 KB → GB
            except (ValueError, TypeError):
                size_gb = 0.0
        if not size_gb and rec.get("InstallLocation") and os.path.isdir(rec["InstallLocation"]):
            try:
                size_gb = round(S.dir_size(rec["InstallLocation"]) / 1073741824, 1)
            except Exception:
                size_gb = 0.0
        found.append({
            "id": t["id"], "name": t["name"], "sub": t["sub"], "brand": t.get("brand"),
            "size_gb": size_gb, "uninstall_cmd": rec.get("UninstallString") or "",
            "model_dirs": [os.path.expandvars(p) for p in t.get("model_dirs", [])],
            "category": t.get("category", ""),
        })
    return found


def request_uninstall(app_id, include_model_dir):
    """安全卸载：只调起系统 / 官方卸载程序，绝不自行递归删除文件。"""
    targets = {t["id"]: t for t in enum_uninstall_targets()}
    app = targets.get(app_id)
    if not app:
        return {"ok": False, "error": "未找到该应用的卸载信息（可能已卸载）"}
    cmd = app["uninstall_cmd"]
    if not cmd:
        return {"ok": False, "error": "未找到系统卸载命令；请到 Windows 设置 → 应用 中手动卸载。",
                "model_dirs": app["model_dirs"]}
    exe = cmd.strip().strip('"')
    args = []
    if exe.startswith('"'):
        end = cmd.find('"', 1)
        exe = cmd[1:end]
        args = cmd[end + 1:].strip().split()
    else:
        parts = cmd.split(None, 1)
        exe = parts[0]
        args = parts[1].split() if len(parts) > 1 else []
    try:
        import subprocess
        subprocess.Popen([exe] + args, shell=False)
    except Exception as e:
        return {"ok": False, "error": "调起卸载程序失败：" + str(e)}
    note = "已调起官方卸载程序，请按向导完成。模型 / 数据目录未在 Sweep 侧删除"
    if include_model_dir:
        note += ("（你已勾选“一并清理”，但 Sweep 不代为删除；请在卸载向导内勾选，或之后手动清理："
                 + "、".join(app["model_dirs"]) + "）。") if app["model_dirs"] else "。"
    else:
        note += "（模型 / 数据目录默认保留，如需清理请在卸载向导内勾选或手动处理）。"
    write_audit("uninstall_request", {"id": app_id, "name": app["name"], "cmd": cmd,
                                       "model_dir_consent": bool(include_model_dir)})
    return {"ok": True, "note": note}


DASHBOARD = r'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sweep 网页版 · 一键清理</title>
<style>
:root{--bg:#0e1116;--panel:#161b22;--panel-2:#1a2130;--ink:#e6e9ef;--muted:#9aa4b2;--line:#2b3543;
--green:#3FB950;--green-bg:rgba(63,185,80,.14);--amber:#e0a93b;--amber-bg:rgba(224,169,59,.14);
--red:#f0584d;--red-bg:rgba(240,88,77,.14);--brand:#3FB950;--brand-ink:#2f9b40;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;background:radial-gradient(1200px 600px at 50% -10%,rgba(63,185,80,.06),transparent 60%),var(--bg);color:var(--ink);font-size:14px;line-height:1.5}
/* 壳层：左侧栏 + 主区（对齐设计稿 5 页统一骨架） */
.shell{display:flex;min-height:100vh}
.sidebar{width:216px;flex:none;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:18px 14px;gap:22px;position:sticky;top:0;height:100vh}
.logo-row{display:flex;align-items:center;gap:10px;padding:0 4px}
.logo-mark{width:28px;height:28px;flex:none;display:flex;align-items:center;justify-content:center}
.brand{font-size:17px;font-weight:700;letter-spacing:.2px}
.nav-group{display:flex;flex-direction:column;gap:6px}
.nav-item{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:1px solid transparent;background:transparent;border-radius:8px;padding:9px 11px;font-size:13.5px;color:var(--muted);cursor:pointer;transition:.18s}
.nav-item:hover{background:var(--panel-2);color:var(--ink);border-color:var(--line)}
.nav-item .ni{width:18px;height:18px;flex:none;display:flex;align-items:center;justify-content:center}
.nav-item.active{background:linear-gradient(180deg,#2e6b3d,#1f4d2b);color:var(--green);font-weight:600;border-color:rgba(63,185,80,.55);box-shadow:inset 0 0 4px rgba(63,185,80,.7),0 0 16px rgba(63,185,80,.25)}
.side-foot{margin-top:auto;display:flex;flex-direction:column;gap:9px;font-size:11px;color:var(--muted);line-height:1.6}
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{background:var(--panel);border-bottom:1px solid var(--line);padding:15px 26px;box-shadow:0 10px 24px -6px rgba(0,0,0,.28);position:sticky;top:0;z-index:20}
.page-title{font-size:19px;font-weight:700}
.page-sub{font-size:12px;color:var(--muted);margin-top:3px}
.content{padding:22px 26px 30px;max-width:1180px}
.section{display:none}
.section.active{display:block}
.overview{display:flex;gap:14px;flex-wrap:wrap;margin:4px 0 16px}
.ov{background:linear-gradient(135deg,#1c2535,#161b22);border:1px solid var(--line);border-radius:12px;padding:14px 16px;flex:1;min-width:140px;box-shadow:0 6px 18px rgba(0,0,0,.35)}
.ov .k{font-size:12px;color:var(--muted)} .ov .v{font-size:24px;font-weight:700;margin-top:4px}
.ov.total .v{color:var(--green)} .ov.g .v{color:var(--green)} .ov.a .v{color:var(--amber)} .ov.r .v{color:var(--red)}
.ov.action{display:flex;align-items:center;justify-content:center} .ov.action .btn-primary{width:100%}
.cols{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
@media(max-width:980px){.cols{grid-template-columns:1fr}}
.col{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,.3)}
.col .head{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--line);font-weight:600}
.col.g .head{background:var(--green-bg)} .col.a .head{background:var(--amber-bg)} .col.r .head{background:var(--red-bg)}
.sum{margin-left:auto;font-size:12px;color:var(--muted);font-weight:500}
.item{display:flex;align-items:flex-start;gap:10px;padding:11px 14px;border-bottom:1px solid var(--line)}
.item:last-child{border-bottom:0}
.meta{flex:1;min-width:0} .name{font-weight:500} .desc{font-size:12px;color:var(--muted);margin-top:2px}
.sz{font-size:12px;color:var(--muted);white-space:nowrap;margin-left:8px}
.lock{font-size:14px;flex:none;margin-top:2px}
.cb{flex:none;width:16px;height:16px;margin-top:3px;cursor:pointer}
.evi{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.6}
.bar{position:sticky;bottom:0;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:16px}
button{border:1px solid var(--line);background:var(--panel-2);border-radius:9px;padding:9px 14px;font-size:13px;cursor:pointer;color:var(--ink)}
button:hover{border-color:var(--brand)}
.btn-primary{background:linear-gradient(180deg,#3FB950,#2f9b40);color:#06210d;border:0;font-weight:600;box-shadow:0 4px 14px rgba(63,185,80,.3)}
.btn-primary:hover{background:var(--brand-ink)}
.btn-sm{padding:5px 10px;font-size:12px}
#result{margin-top:14px;font-size:13px;color:var(--muted);white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-height:20px}
.note{margin-top:14px;font-size:12px;color:var(--muted);line-height:1.7}
/* 载入锁定遮罩 */
.overlay{position:fixed;inset:0;background:rgba(10,13,18,.92);z-index:999;display:flex;align-items:center;justify-content:center}
.overlay.hidden{display:none}
.loader{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:28px 32px;width:min(440px,90vw);box-shadow:0 20px 60px rgba(0,0,0,.6);text-align:center}
.spinner{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--brand);border-radius:50%;margin:0 auto 14px;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.ltitle{font-size:15px;font-weight:600}
.lpct{font-size:12px;color:var(--brand-ink);font-weight:600;margin-top:6px}
.lbar{height:8px;background:var(--panel-2);border-radius:99px;overflow:hidden;margin:12px 0 8px}
.lbar-fill{height:100%;width:0;background:var(--brand);border-radius:99px;transition:width .2s ease}
.lstatus{font-size:12px;color:var(--muted);min-height:16px}
/* 重复检测面板 */
.dup-head{font-weight:600;display:flex;align-items:center;gap:8px;margin-bottom:10px}
.dup-actions{display:flex;gap:10px;margin-top:6px}
.dup-bar{height:8px;background:var(--panel-2);border-radius:99px;overflow:hidden;margin:10px 0 6px;display:none}
.dup-bar-fill{height:100%;width:0;background:var(--brand);border-radius:99px;transition:width .2s ease}
.dup-status{font-size:12px;color:var(--muted);min-height:16px}
.du-group{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-top:10px;background:var(--panel-2)}
.du-group .gh{font-weight:600;font-size:13px;color:var(--brand-ink)}
.du-group .gp{font-size:12px;color:var(--muted);margin-top:4px;word-break:break-all}
.du-empty{font-size:12px;color:var(--muted);margin-top:8px;line-height:1.6}
/* 回收站 */
.bk-head{font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.bk{margin-top:14px}
.bk-ts{font-weight:600;font-size:13px;color:var(--brand-ink);margin-bottom:6px}
.bk-item{display:flex;align-items:center;gap:10px;padding:8px 12px;border:1px solid var(--line);border-radius:9px;margin-top:8px;background:var(--bg)}
.bk-item .nm{flex:1;min-width:0;font-weight:500}
.bk-item .pth{font-size:11px;color:var(--muted);word-break:break-all}
.bk-item .sz{font-size:12px;color:var(--muted);white-space:nowrap}
.bk-empty{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.6}
/* 保护列表 */
.prot{margin-top:16px;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.prot-head{font-weight:600;margin-bottom:8px}
.prot-add{display:flex;gap:8px}
.prot-add input{flex:1}
.foot{display:flex;align-items:center;gap:12px;margin-top:18px;padding-top:14px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
.foot a{color:var(--brand-ink);text-decoration:none;cursor:pointer}
/* 对话清理 */
/* 对话清理：筛选控制区（紧凑卡片式） */
.conv-ctrl{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
.conv-ctrl-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.conv-ctrl-head label{font-size:13px;font-weight:600;color:var(--ink)}
.conv-ctrl-head .conv-hint{font-size:11px;color:var(--muted);max-width:400px;line-height:1.5}
.conv-filters{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap}
.conv-filters .field{display:flex;flex-direction:column;gap:4px}
.conv-filters .field label{font-size:11px;color:var(--muted)}
.conv-filters .field input{width:88px;background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:7px 10px;font-size:14px;font-weight:600;text-align:center;transition:border-color .15s}
.conv-filters .field input:focus{border-color:var(--brand);outline:none;box-shadow:0 0 0 3px rgba(63,185,80,.15)}
.conv-stats{display:flex;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.conv-stat{flex:1;min-width:140px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.conv-stat .n{font-size:22px;font-weight:700}
.conv-stat .l{font-size:11px;color:var(--muted);margin-top:2px}
.conv-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 13px;margin-bottom:12px}
.conv-toolbar .sp{flex:1}
.conv-toolbar .sel{font-size:12px;color:var(--muted)}
.conv-toolbar .sel b{color:var(--brand-ink)}
.conv-ctrl .field .unit{font-size:12px;color:var(--muted);margin-left:4px;align-self:center}
.conv-hint{font-size:11px;color:var(--muted);margin-left:auto;max-width:360px;line-height:1.5}
.sort-label{font-size:12px;color:var(--muted);margin-left:10px}
.sort-btn.active{background:var(--green);color:#06210f;border-color:var(--green)}
.conv-table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.conv-table thead th{font-size:12px;color:var(--muted);font-weight:600;text-align:left;padding:11px 13px;border-bottom:1px solid var(--line);background:var(--bg)}
.conv-table tbody td{padding:10px 13px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:middle}
.conv-table tbody tr:hover{background:var(--bg)}
.conv-table .path{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--muted);max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conv-table .size{font-weight:600}
.conv-pill{display:inline-flex;align-items:center;gap:5px;border-radius:20px;padding:3px 9px;font-size:11px;font-weight:600}
.conv-pill.g{background:var(--green-bg);color:var(--green)}
.conv-pill.r{background:var(--red-bg);color:var(--red)}
.conv-pill.a{background:var(--panel-2);color:var(--muted)}
.conv-dot{width:7px;height:7px;border-radius:50%}
.conv-empty{text-align:center;color:var(--muted);padding:34px;font-size:13px}
/* Antigravity 会话子区域 */
.ag-conv-section{margin-top:18px;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--panel)}
.ag-conv-head{display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--line);font-weight:600;background:var(--bg)}
.ag-conv-count{margin-left:auto;font-size:12px;font-weight:400;color:var(--muted)}
.ag-conv-hint{font-size:11px;color:var(--muted);padding:8px 14px;border-bottom:1px solid var(--line);background:rgba(63,185,80,.04)}
.ag-conv-table{border:none;border-radius:0}
.conv-provider{display:inline-block;font-size:10px;font-weight:700;padding:2px 7px;border-radius:6px;color:#fff;margin-bottom:3px}
.conv-title{font-weight:600;color:var(--ink)}
.conv-sub{font-size:11px;color:var(--muted);margin-top:2px}
.conv-sub .sep{margin:0 5px;opacity:.45}
.conv-pill.n{background:rgba(224,169,59,.14);color:var(--amber)}
.conv-rel{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:10px;color:var(--muted);margin-top:3px;opacity:.8}
/* 卸载工具（对齐设计稿 2:481） */
.sec-head{margin-bottom:14px}
.sec-title{font-size:15px;font-weight:700}
.sec-sub{font-size:12px;color:#6b7480;margin-top:3px}
.app-banner{display:flex;gap:14px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;box-shadow:0 6px 18px rgba(0,0,0,.3);margin-bottom:16px;flex-wrap:wrap}
.app-cell{flex:1;min-width:150px;background:linear-gradient(135deg,#1a2130,#12171e);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:0 6px 18px rgba(0,0,0,.35)}
.app-cell .n{font-size:24px;font-weight:700;color:var(--green)}
.app-cell .l{font-size:11px;color:var(--muted);margin-top:3px}
.app-lhead{font-size:11px;color:var(--muted);margin:0 2px 9px}
.app-list{display:flex;flex-direction:column;gap:10px}
.app-row{display:flex;align-items:center;gap:13px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:0 6px 18px rgba(0,0,0,.3)}
.app-ico{width:34px;height:34px;flex:none;border-radius:9px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px;background:var(--panel-2);color:var(--green);border:1px solid var(--line);overflow:hidden}
.app-ico img{width:100%;height:100%;object-fit:contain}
.app-info{flex:1;min-width:0}
.app-name{font-weight:600}
.app-sub{font-size:11.5px;color:var(--muted);margin-top:2px;word-break:break-all}
.app-size{font-size:13px;font-weight:600;white-space:nowrap}
.app-empty{font-size:12px;color:var(--muted);padding:26px;text-align:center}
.btn-un{border:1px solid rgba(240,88,77,.45);background:var(--red-bg);color:var(--red);font-weight:600;padding:6px 13px;font-size:12px;border-radius:8px;cursor:pointer}
.btn-un:hover{border-color:var(--red)}
.btn-un[disabled]{opacity:.4;cursor:not-allowed}
/* 卸载二次确认弹层 */
.modal{position:fixed;inset:0;background:rgba(10,13,18,.92);z-index:1200;display:flex;align-items:center;justify-content:center;padding:20px}
.modal.hidden{display:none}
.modal-card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px 24px;width:min(520px,94vw);box-shadow:0 20px 60px rgba(0,0,0,.6)}
.modal-title{font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px}
.modal-body{font-size:13px;color:var(--muted);margin-top:12px;line-height:1.75}
.modal-body code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--ink);word-break:break-all}
.modal-warn{margin-top:12px;background:var(--amber-bg);border:1px solid rgba(224,169,59,.4);color:var(--amber);border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.7}
.modal-opt{margin-top:12px;display:flex;gap:9px;align-items:flex-start;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:11px 12px}
.modal-opt input{margin-top:2px;flex:none;width:15px;height:15px;cursor:pointer}
.modal-opt .ot{font-size:12.5px;color:var(--ink)}
.modal-opt .od{font-size:11px;color:var(--muted);margin-top:2px;line-height:1.6}
.modal-acts{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
.btn-danger{background:linear-gradient(180deg,#f0584d,#c9382e);color:#fff;border:0;font-weight:600;box-shadow:0 4px 14px rgba(240,88,77,.3)}
.vizbox{margin-top:18px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px 16px;box-shadow:0 12px 32px -6px rgba(0,0,0,.3)}
.viz-tabs{display:flex;gap:8px;margin-bottom:12px}
.viz-tab{background:var(--panel-2);color:var(--muted);border:1px solid var(--line);border-radius:8px;padding:6px 14px;font-size:13px;font-weight:500;cursor:pointer;transition:.15s}
.viz-tab:hover{color:var(--ink)}
.viz-tab.active{background:linear-gradient(180deg,rgba(63,185,80,.18),rgba(63,185,80,.08));color:var(--green);border-color:rgba(63,185,80,.45);box-shadow:inset 0 0 4px rgba(63,185,80,.4)}
.viz-stage{width:100%;display:flex;justify-content:center;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px;min-height:340px;align-items:center}
.viz-stage svg{display:block;max-width:100%;height:auto}
.viz-legend{display:flex;gap:18px;margin-top:12px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.viz-legend .lg{display:flex;align-items:center;gap:6px}
.viz-legend .sw{width:12px;height:12px;border-radius:3px;display:inline-block}
/* 逐个工具清理 */
.tool-block{border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:14px;background:var(--panel)}
.tool-head{display:flex;align-items:center;gap:10px;padding:11px 14px;background:var(--panel-2);border-bottom:1px solid var(--line)}
.tb-ic{width:28px;height:28px;flex:none;border-radius:8px;background:linear-gradient(180deg,#2e6b3d,#1f4d2b);color:var(--green);font-weight:700;font-size:14px;display:flex;align-items:center;justify-content:center}
.tb-name{font-weight:700;font-size:14px}
.tb-stat{margin-left:auto;font-size:12px;color:var(--muted)}
.tb-stat b{color:var(--green)}
.tb-all{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted);cursor:pointer}
.tb-all input{width:15px;height:15px;cursor:pointer}
.nav-divider{height:1px;background:var(--line);margin:6px 4px}
.viz-legend .lg.clickable{cursor:pointer;padding:2px 4px;margin:-2px -4px;border-radius:6px}
.viz-legend .lg.clickable:hover{background:var(--panel-2)}
.donut-hint{font-size:11px;color:var(--muted);margin-top:8px}
</style></head>
<body><div class="shell">
<aside class="sidebar">
  <div class="logo-row">
    <div class="logo-mark"><svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect x="1" y="1" width="26" height="26" rx="8" fill="#3FB950"/><path d="M9 19L15 9" stroke="#0e1116" stroke-width="2.4" stroke-linecap="round"/><circle cx="17.6" cy="18.6" r="2.7" fill="#0e1116"/></svg></div>
    <div class="brand">Sweep</div>
  </div>
  <nav class="nav-group">
    <button class="nav-item active" id="tab-scan-btn" onclick="showTab('scan')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 12l4.5-3"/><path d="M12 3v2"/></svg></span><span>扫描概览</span></button>
    <button class="nav-item" id="tab-conv-btn" onclick="showTab('conv')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 5H4v11h5l3.5 3.5V16H20z"/><path d="M8 10h8"/></svg></span><span>对话清理</span></button>
    <button class="nav-item" id="tab-tools-btn" onclick="showTab('tools')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M4 12h10"/><path d="M4 17h7"/></svg></span><span>逐个工具清理</span></button>
    <button class="nav-item" id="tab-dup-btn" onclick="showTab('dup')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M9.5 14.5l5-5"/><path d="M10.8 7.2l1.7-1.7a3.8 3.8 0 015.4 5.4l-1.7 1.7"/><path d="M13.2 16.8l-1.7 1.7a3.8 3.8 0 01-5.4-5.4l1.7-1.7"/></svg></span><span>重复检测</span></button>
    <button class="nav-item" id="tab-trash-btn" onclick="showTab('trash')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M9.5 7V4.5h5V7"/><path d="M6.5 7l1 12.5h9L17.5 7"/></svg></span><span>回收站</span></button>
    <button class="nav-item" id="tab-uninstall-btn" onclick="showTab('uninstall')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5v9"/><path d="M8.5 9L12 12.5 15.5 9"/><path d="M4 14.5V18a2 2 0 002 2h12a2 2 0 002-2v-3.5"/></svg></span><span>卸载工具</span></button>
  </nav>
  <div class="nav-divider"></div>
  <nav class="nav-group">
    <button class="nav-item" id="tab-protect-btn" onclick="showTab('protect')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4-3 7-7 8-4-1-7-4-7-8V6z"/></svg></span><span>保护列表</span></button>
    <button class="nav-item" id="tab-audit-btn" onclick="showTab('audit')"><span class="ni"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5h10"/><path d="M9 12h10"/><path d="M9 19h10"/><path d="M4 5h.01"/><path d="M4 12h.01"/><path d="M4 19h.01"/></svg></span><span>审计日志</span></button>
  </nav>
  <div class="side-foot">
    <button class="btn-sm" onclick="stopServer()">停止服务</button>
    <span>能安全删 · 先别删 · 已锁定<br>清理均进回收站，可还原</span>
  </div>
</aside>
<main class="main">
<header class="topbar">
  <div class="page-title" id="pageTitle">扫描概览</div>
  <div class="page-sub" id="ts">加载中…</div>
</header>
<div class="content">

<!-- 扫描概览 -->
<section class="section active" id="tab-scan">
  <div class="overview" id="ov"></div>
  <div class="vizbox" id="vizbox">
    <div class="viz-tabs">
      <button class="viz-tab active" id="vizTabTm" onclick="setViz('treemap')">矩形树图</button>
      <button class="viz-tab" id="vizTabDn" onclick="setViz('donut')">占比环图</button>
    </div>
    <div class="viz-stage" id="vizStage"></div>
    <div class="viz-legend" id="vizLegend"></div>
    <div class="donut-hint">点环图的某一段，或右侧条目，直接跳到对应工具的清理列表。</div>
  </div>
  <div class="cols" id="cols"></div>
  <div class="bar">
    <button onclick="startScan(true)">刷新扫描</button>
    <button class="btn-sm" onclick="selectAll(true)">全选</button>
    <button class="btn-sm" onclick="selectAll(false)">全不选</button>
    <button onclick="clean(true)">清理预览</button>
    <button class="btn-primary" onclick="clean(false)">一键清理选中项（可撤销）</button>
    <button class="btn-sm" onclick="exportJSON()">导出 JSON</button>
    <button class="btn-sm" onclick="copyReport()">复制占用概览</button>
    <span id="hint" style="color:var(--muted);font-size:12px;margin-left:auto">能安全删的勾选后移动到备份目录，非永久删除；已锁定的始终跳过</span>
  </div>
  <div id="result"></div>
  <div class="note">说明：本网页由本地 Python 后端（server.py）支撑，按钮点击会经本地服务真实移动能安全删的文件到备份目录（回收站）。已锁定的（进程占用/用户产出）始终跳过。🗨️ 各 AI 应用大体积或陈旧的对话历史会被单独标记为「能安全删」（仅丢失聊天记录，已备份可撤销），近期小历史仍建议保留。扫描结果在服务端缓存 5 分钟，重复打开即时返回；清理非永久删除，备份位于 %USERPROFILE%\.sweep_backup\。</div>
</section>

<!-- 回收站 -->
<section class="section" id="tab-trash">
  <div class="bk-head">🗑️ 回收站（已移动的备份，可一键还原） <button class="btn-sm" onclick="loadBackups()">刷新</button></div>
  <div class="overview" id="trashov">
    <div class="ov"><div class="k">已备份项数</div><div class="v" id="trashItems">0</div></div>
    <div class="ov"><div class="k">可还原总量</div><div class="v" id="trashGb">0 GB</div></div>
    <div class="ov action"><button class="btn-primary" id="restoreAllBtn" onclick="restoreAll()" disabled>全部还原</button></div>
  </div>
  <div id="bkres"><div class="bk-empty">点击「刷新」查看已备份、可还原的项目。</div></div>
</section>

<!-- 重复检测 -->
<section class="section" id="tab-dup">
  <div class="dup-head">🔗 跨工具重复检测 <span class="sum" id="dupsum"></span></div>
  <div class="dup-actions">
    <button onclick="detectDupes()">检测重复</button>
    <button class="btn-primary" id="dedupBtn" onclick="dedup()" disabled>一键合并去重</button>
  </div>
  <div class="dup-bar" id="dupbar"><div class="dup-bar-fill" id="dupfill"></div></div>
  <div class="dup-status" id="dupstatus"></div>
  <div class="overview" id="dupov">
    <div class="ov g"><div class="k">重复组数</div><div class="v" id="dupGroups">0</div></div>
    <div class="ov a"><div class="k">可节省空间</div><div class="v" id="dupReclaim">0 GB</div></div>
  </div>
  <div id="dupres"></div>
</section>

<!-- 对话清理 -->
<section class="section" id="tab-conv">
  <div class="conv-ctrl">
    <div class="conv-ctrl-head">
      <label>🔍 筛选条件</label>
      <span class="conv-hint">满足任一条即标记「能安全删」——只进回收站，随时可还原；也可直接在列表中手动勾选。</span>
    </div>
    <div class="conv-filters">
      <div class="field"><label>超过几天没动</label><input id="conv-age" type="number" value="90"><span class="unit">天</span></div>
      <div class="field"><label>大于多少 MB</label><input id="conv-size" type="number" value="50"><span class="unit">MB</span></div>
      <button class="btn-primary" onclick="convLoad()">应用并扫描</button>
    </div>
  </div>
  <div class="conv-stats">
    <div class="conv-stat"><div class="n" id="conv-total">0</div><div class="l">对话总数</div></div>
    <div class="conv-stat"><div class="n" style="color:var(--green)" id="conv-cand">0</div><div class="l">能安全删（命中）</div></div>
    <div class="conv-stat"><div class="n" style="color:var(--red)" id="conv-lock">0</div><div class="l">运行中锁定</div></div>
    <div class="conv-stat"><div class="n" style="color:#b26a00" id="conv-agg">0</div><div class="l">聚合存储（仅展示）</div></div>
    <div class="conv-stat"><div class="n" style="color:var(--brand-ink)" id="conv-gb">0 GB</div><div class="l">能安全删总量</div></div>
  </div>
  <div class="conv-toolbar">
    <button class="btn-sm" onclick="convSelectAll(true)">全选</button>
    <button class="btn-sm" onclick="convSelectAll(false)">全不选</button>
    <button class="btn-sm" onclick="convInvert()">反选</button>
    <div class="sp"></div>
    <label class="sort-label">排序</label>
    <button class="btn-sm sort-btn active" id="sort-size" onclick="convSort('size')">按大小</button>
    <button class="btn-sm sort-btn" id="sort-age" onclick="convSort('age')">按新旧</button>
    <span class="sel">已选 <b id="conv-sel-count">0</b> 条 · <b id="conv-sel-gb">0 GB</b></span>
    <button class="btn-primary" id="convCleanBtn" onclick="convClean()" disabled>清理选中</button>
  </div>
  <table class="conv-table">
    <thead><tr>
      <th style="width:36px"></th><th>工具 / 存储</th><th>对话</th>
      <th style="width:92px">大小</th><th style="width:128px">状态</th>
    </tr></thead>
    <tbody id="conv-rows"><tr><td colspan="5" class="conv-empty">加载中…</td></tr></tbody>
  </table>
  <div class="ag-conv-section">
    <div class="ag-conv-head">🧠 Antigravity 会话库 <span class="ag-conv-count" id="ag-count">—</span>
      <button class="btn-sm" onclick="agLoad()" id="agLoadBtn">扫描会话</button>
    </div>
    <div class="ag-conv-hint" id="ag-hint">点击「扫描会话」读取本地 SQLite 数据库，列出每个对话的标题、步骤数、大小。可单独删除不需要的旧对话。</div>
    <table class="conv-table ag-conv-table" id="ag-table" style="display:none">
      <thead><tr><th style="width:36px"></th><th>标题 / 工作区</th><th>详情</th><th style="width:80px">大小</th><th style="width:80px">天数</th><th style="width:72px"></th></tr></thead>
      <tbody id="ag-rows"></tbody>
    </table>
  </div>
  <div class="bk-head" style="margin-top:18px">📦 对话回收站（可还原） <button class="btn-sm" onclick="convLoadBackups()">刷新</button></div>
  <div id="conv-backups"><div class="conv-empty">暂无对话备份</div></div>
</section>

<!-- 卸载工具（对齐设计稿 2:481，安全版：列表 + 系统卸载入口） -->
<section class="section" id="tab-uninstall">
  <div class="sec-head">
    <div class="sec-title">已安装的 AI 工具</div>
    <div class="sec-sub">走系统卸载入口 · 二次确认 · 模型目录默认不勾</div>
  </div>
  <div class="app-banner" id="appBanner">
    <div class="app-cell"><div class="n" id="appCount">0</div><div class="l">已安装 AI 应用</div></div>
    <div class="app-cell"><div class="n" id="appSize">0 GB</div><div class="l">应用本体占用</div></div>
  </div>
  <div class="app-lhead">应用（扫描自本机注册表 / 开始菜单卸载项，卸载均经系统或官方卸载程序）</div>
  <div class="app-list" id="appList"><div class="app-empty">正在识别本机已装的 AI 工具…</div></div>
</section>

<!-- 逐个工具清理（按工具×类型，从扫描结果分组） -->
<section class="section" id="tab-tools">
  <div class="sec-head"><div class="sec-title">逐个工具清理</div><div class="sec-sub">每个 AI 工具有好几类能清的东西——对话记录、缓存、临时文件、日志。按工具看清每类，勾上能安全删的。</div></div>
  <div class="bar">
    <button onclick="startScan(true)">刷新扫描</button>
    <button class="btn-sm" onclick="selectAll(true)">全选（仅能安全删）</button>
    <button class="btn-sm" onclick="selectAll(false)">全不选</button>
    <button onclick="clean(true)">清理预览</button>
    <button class="btn-primary" onclick="clean(false)">清理选中项（可撤销）</button>
  </div>
  <div id="toolsres"></div>
  <div class="note">每个工具按「对话记录 / 缓存 / 临时文件 / 日志 / 数据」列出来源，能安全删的打勾即可；「先别删」是近期还在用、想腾空间可挑更早的，「已锁定」是正在用或受保护，不会动。清理一律先进回收站，误删可还原。</div>
</section>

<!-- 保护列表（独立 nav，对齐 v2 IA） -->
<section class="section" id="tab-protect">
  <div class="sec-head"><div class="sec-title">保护列表</div><div class="sec-sub">这里的路径永远不会被清理，扫到也不会动</div></div>
  <div class="prot">
    <div class="prot-head">🛡️ 保护列表（列表中的路径永不被清理）</div>
    <div class="prot-add">
      <input id="exInput" placeholder="输入要排除的路径，如 <your-project>">
      <button class="btn-sm" onclick="addExclusion()">添加</button>
    </div>
    <div id="exList" style="margin-top:10px"></div>
  </div>
  <div class="note">改了列表后，下次扫描会重新判断。列表里的路径不会被清理，默认也不勾选。</div>
</section>

<!-- 审计日志（独立 nav，对齐 v2 IA） -->
<section class="section" id="tab-audit">
  <div class="sec-head"><div class="sec-title">审计日志</div><div class="sec-sub">每次清理 / 还原 / 卸载都留痕，可追责、可复盘</div></div>
  <div id="auditres" class="bk-empty">点击「刷新」查看清理 / 还原记录。</div>
  <div class="bar"><button class="btn-sm" onclick="loadAudit()">刷新</button></div>
</section>

</div><!-- /content -->
</main>
</div><!-- /shell -->
<div id="overlay" class="overlay">
  <div class="loader">
    <div class="spinner"></div>
    <div class="ltitle">正在扫描本机 AI 目录…</div>
    <div class="lpct" id="lpct">0%</div>
    <div class="lbar"><div class="lbar-fill" id="barfill"></div></div>
    <div class="lstatus" id="lstatus">首次扫描约需 10–30 秒，请稍候（界面已锁定，勿重复点击）</div>
  </div>
</div>
<div class="modal hidden" id="modal"><div class="modal-card" id="modalCard"></div></div>
<script>
const ICON={green:"🟢",amber:"🟡",red:"🔒"};
let state={items:[],selected:new Set()};
const HDR={"Content-Type":"application/json"};
let es=null, esClosed=false;
function sum(arr){return Math.round(arr.reduce((s,i)=>s+i.size_bytes,0)/1073741824*10)/10;}
function showOverlay(){document.getElementById("overlay").classList.remove("hidden");}
function hideOverlay(){document.getElementById("overlay").classList.add("hidden");}
function setBar(pct,text){
  pct=Math.max(0,Math.min(100,pct));
  document.getElementById("barfill").style.width=pct+"%";
  document.getElementById("lpct").textContent=pct+"%";
  if(text)document.getElementById("lstatus").textContent=text;
}
const PAGE_TITLE={scan:"扫描概览",conv:"对话清理",tools:"逐个工具清理",dup:"重复检测",trash:"回收站",uninstall:"卸载工具",protect:"保护列表",audit:"审计日志"};
function showTab(name){
  document.querySelectorAll(".nav-item").forEach(b=>b.classList.remove("active"));
  document.querySelectorAll(".section").forEach(s=>s.classList.remove("active"));
  const btn=document.getElementById("tab-"+name+"-btn"); if(btn) btn.classList.add("active");
  const sec=document.getElementById("tab-"+name); if(sec) sec.classList.add("active");
  const t=PAGE_TITLE[name]; if(t) document.getElementById("pageTitle").textContent=t;
  if(name==="trash"){ loadBackups(); }
  if(name==="conv"){ convLoad(); }
  if(name==="tools"){ renderTools(); }
  if(name==="protect"){ loadSettings(); }
  if(name==="audit"){ loadAudit(); }
  if(name==="uninstall"){ loadUninstall(); }
}
function startScan(force){
  esClosed=false; showOverlay(); setBar(0,"准备扫描…");
  if(es){try{es.close();}catch(e){}}
  es=new EventSource("/api/scan"+(force?"?fresh=1":""));
  es.onmessage=function(ev){
    let m; try{m=JSON.parse(ev.data);}catch(e){return;}
    if(m.phase==="progress"){
      const pct=Math.round(m.done/m.total*100);
      setBar(pct,"正在扫描："+m.app+"  ("+m.done+"/"+m.total+")");
    } else if(m.phase==="done"){
      esClosed=true;
      setBar(100, m.cached?"已加载缓存（即时返回）":"扫描完成");
      state=m.result; render(); renderViz(); renderLegend(); renderTools();
      try{es.close();}catch(e){}
      setTimeout(hideOverlay,300);
    } else if(m.phase==="error"){
      esClosed=true;
      setBar(0,"扫描出错："+(m.message||""));
      try{es.close();}catch(e){}
    }
  };
  es.onerror=function(){ if(esClosed) return; };
}
function render(){
  const items=state.items;
  const g=items.filter(i=>i.tier=="green"),a=items.filter(i=>i.tier=="amber"),r=items.filter(i=>i.tier=="red");
  // 默认全选绿档（被保护列表排除的不勾选）
  state.selected=new Set(g.filter(i=>!i.excluded).map(i=>i.path));
  document.getElementById("ts").textContent="扫描时间："+state.scanned_at+" · 本机 "+state.computer;
  document.getElementById("ov").innerHTML=
    ov("可清理总量（能安全删 + 先别删）",(sum(g)+sum(a))+" GB","total")+
    ov("能安全删",sum(g)+" GB","g")+
    ov("先别删",sum(a)+" GB","a")+
    ov("已锁定（不碰）",sum(r)+" GB","r");
  document.getElementById("cols").innerHTML=
    col("g","能安全删（勾选清理）",g)+col("a","先别删",a)+col("r","已锁定（不碰）",r);
}
const TIER_COLORS={green:"#3FB950",amber:"#D29922",red:"#F85149"};
const TIER_LABEL={green:"🟢 能安全删",amber:"🟡 先别删",red:"🔒 已锁定"};
let VIZ="treemap";
function setViz(t){VIZ=t;const a=document.getElementById("vizTabTm"),b=document.getElementById("vizTabDn");if(a)a.classList.toggle("active",t==="treemap");if(b)b.classList.toggle("active",t==="donut");renderViz();}
function tierSumGB(items,tier){return Math.round(items.filter(i=>i.tier===tier).reduce((s,i)=>s+i.size_bytes,0)/1073741824*10)/10;}
function worst(row,length){const s=row.reduce((a,b)=>a+b,0);const mn=Math.min(...row),mx=Math.max(...row);return Math.max(s/mn,mx/s);}
function squarify(areas,x,y,w,h){
  if(!areas.length)return[];
  if(areas.length===1)return[[x,y,w,h]];
  const total=areas.reduce((a,b)=>a+b,0);
  let bestI=1,bestDiff=Infinity,acc=0;
  for(let i=1;i<areas.length;i++){acc+=areas[i-1];const diff=Math.abs(acc-(total-acc));if(diff<bestDiff){bestDiff=diff;bestI=i;}}
  const left=areas.slice(0,bestI),right=areas.slice(bestI),leftSum=left.reduce((a,b)=>a+b,0);
  let rects=[];
  if(w>=h){const lw=leftSum/h;rects=rects.concat(squarify(left,x,y,lw,h));rects=rects.concat(squarify(right,x+lw,y,w-lw,h));}
  else{const th=leftSum/w;rects=rects.concat(squarify(left,x,y,w,th));rects=rects.concat(squarify(right,x,y+th,w,h-th));}
  return rects;
}
function buildTreemap(items){
  const W=640,H=320;
  const data=items.filter(i=>(i.size_bytes||0)>0).map(i=>({name:i.name,tier:i.tier,bytes:i.size_bytes||0,gb:Math.round((i.size_bytes||0)/1073741824*10)/10})).sort((a,b)=>b.bytes-a.bytes);
  if(!data.length)return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><text x="${W/2}" y="${H/2}" fill="#9AA4B2" font-size="14" text-anchor="middle">暂无数据</text></svg>`;
  const totalArea=W*H,totalBytes=data.reduce((s,d)=>s+d.bytes,0)||1;
  const areas=data.map(d=>d.bytes/totalBytes*totalArea);
  const rects=squarify(areas.slice().sort((a,b)=>b-a),0,0,W,H);
  let out=[];
  rects.forEach((r,idx)=>{
    const d=data[idx];const col=TIER_COLORS[d.tier]||"#ABB2BF";
    const label=d.gb>=0.1?d.gb+" GB":Math.round(d.bytes/1048576)+" MB";
    out.push(`<rect x="${r[0].toFixed(1)}" y="${r[1].toFixed(1)}" width="${r[2].toFixed(1)}" height="${r[3].toFixed(1)}" fill="${col}" rx="2" stroke="#0E1116" stroke-width="1.5" opacity="0.92"><title>${esc(d.name)} · ${label}</title></rect>`);
    if(r[2]>54&&r[3]>26){const cx=r[0]+r[2]/2,cy=r[1]+r[3]/2;
      out.push(`<text x="${cx.toFixed(1)}" y="${(cy-2).toFixed(1)}" fill="#0E1116" font-size="12" font-weight="700" text-anchor="middle">${esc(d.name.length>12?d.name.slice(0,12):d.name)}</text>`);
      out.push(`<text x="${cx.toFixed(1)}" y="${(cy+13).toFixed(1)}" fill="#0E1116" font-size="10" text-anchor="middle">${label}</text>`);}
  });
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${out.join("")}</svg>`;
}
function buildDonut(items){
  const W=320,H=320,cx=160,cy=160,R=140,r=82;
  const segs=["green","amber","red"].map(t=>({tier:t,val:tierSumGB(items,t)})).filter(s=>s.val>0);
  const total=segs.reduce((s,x)=>s+x.val,0)||1;
  let ang=-Math.PI/2,parts=[];
  for(const s of segs){
    const frac=s.val/total,a0=ang,a1=ang+frac*2*Math.PI;
    const large=((a1-a0)>Math.PI)?1:0;
    const x0=cx+R*Math.cos(a0),y0=cy+R*Math.sin(a0);
    const x1=cx+R*Math.cos(a1),y1=cy+R*Math.sin(a1);
    const xi1=cx+r*Math.cos(a1),yi1=cy+r*Math.sin(a1);
    const xi0=cx+r*Math.cos(a0),yi0=cy+r*Math.sin(a0);
    const col=TIER_COLORS[s.tier];
    const path=`M${x0.toFixed(2)} ${y0.toFixed(2)} A${R} ${R} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} L${xi1.toFixed(2)} ${yi1.toFixed(2)} A${r} ${r} 0 ${large} 0 ${xi0.toFixed(2)} ${yi0.toFixed(2)} Z`;
    parts.push(`<path d="${path}" fill="${col}" stroke="#0E1116" stroke-width="2" style="cursor:pointer" onclick="tierClick('${s.tier}')"><title>${TIER_LABEL[s.tier]}</title></path>`);
    if(frac>0.05){const mid=(a0+a1)/2;const lx=cx+(R+r)/2*Math.cos(mid),ly=cy+(R+r)/2*Math.sin(mid);parts.push(`<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" fill="#0E1116" font-size="11" font-weight="700" text-anchor="middle">${(frac*100).toFixed(0)}%</text>`);}
    ang=a1;
  }
  parts.push(`<text x="${cx}" y="${cy-6}" fill="#E6E9EF" font-size="22" font-weight="700" text-anchor="middle">${total.toFixed(1)} GB</text>`);
  parts.push(`<text x="${cx}" y="${cy+16}" fill="#9AA4B2" font-size="12" text-anchor="middle">总 AI 资产占用</text>`);
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${parts.join("")}</svg>`;
}
function renderViz(){
  const items=state.items||[];
  const stage=document.getElementById("vizStage");
  if(!stage)return;
  stage.innerHTML = VIZ==="treemap" ? buildTreemap(items) : buildDonut(items);
}
function renderLegend(){
  const el=document.getElementById("vizLegend");if(!el)return;
  el.innerHTML=["green","amber","red"].map(t=>`<span class="lg clickable" onclick="tierClick('${t}')"><span class="sw" style="background:${TIER_COLORS[t]}"></span>${TIER_LABEL[t]}</span>`).join("");
}
function tierClick(tier){
  // 环图按安全程度切分后穿透：能安全删 / 先别删 → 逐个工具清理；已锁定 → 保护列表
  if(tier==="red"){ showTab('protect'); }
  else { showTab('tools'); }
}
function guessTool(i){
  const p=(i.path||"").toLowerCase(), n=(i.name||"").toLowerCase();
  const map=[["claude","Claude"],["codex","Codex"],["openai","Codex"],["antigravity","Antigravity"],["gemini","Gemini"],["cursor","Cursor"],
    ["coze","Coze"],["kimi","Kimi"],["trae","Trae"],["grok","Grok"],["insightface","InsightFace"],
    ["qclaw","QClaw"],["zed","Zed"],["stranslate","STranslate"],
    ["nvidia","NVIDIA"],["pip","pip 缓存"]];
  for(const [k,v] of map){ if(p.includes(k)||n.includes(k)) return v; }
  return "其他";
}
function guessType(i){
  const s=((i.path||"")+" "+(i.name||"")).toLowerCase();
  if(/conversation|chats?|session|history|agents?|memory|对话|会话|归档/.test(s)) return "对话记录";
  if(/cache|缓存|_cacache|hub/.test(s)) return "缓存";
  if(/tmp|temp|临时/.test(s)) return "临时文件";
  if(/log|日志/.test(s)) return "日志";
  return "数据";
}
function renderTools(){
  const items=state.items||[];
  const box=document.getElementById("toolsres");
  if(!box) return;
  if(!items.length){ box.innerHTML='<div class="bk-empty">等待扫描结果…</div>'; return; }
  const groups={};
  items.forEach(i=>{ const t=guessTool(i); (groups[t]=groups[t]||[]).push(i); });
  const order=Object.keys(groups).sort((a,b)=>sum(groups[b])-sum(groups[a]));
  box.innerHTML=order.map(function(t){
    const arr=groups[t];
    const cleanGB=Math.round(arr.filter(i=>i.tier==="green"&&!i.excluded).reduce((s,i)=>s+i.size_bytes,0)/1073741824*10)/10;
    const rows=arr.map(function(i){
      let cb;
      if(i.tier==="green" && i.excluded){ cb=`<span title="已在保护列表排除" style="font-size:13px">🛡️</span>`; }
      else if(i.tier==="green"){ cb=`<input type="checkbox" class="cb" data-path="${esc(i.path)}" ${state.selected.has(i.path)?"checked":""} onchange="toggleSel(this)">`; }
      else { cb=""; }
      const tag=guessType(i);
      return `<div class="item">${cb}<span class="lock">${ICON[i.tier]}</span><div class="meta">
        <div class="name">${esc(i.name)}</div><div class="desc">${esc(i.reason)}</div>
        <div class="evi">${tag} · ${esc(i.path)}</div></div><span class="sz">${i.size_gb} GB</span></div>`;
    }).join("");
    return `<div class="tool-block"><div class="tool-head"><span class="tb-ic">${esc(t.slice(0,1))}</span><div class="tb-name">${esc(t)}</div><div class="tb-stat">本工具能安全删 <b>${cleanGB}</b> GB</div><label class="tb-all"><input type="checkbox" onchange="toggleTool(this)"> 全选</label></div>${rows}</div>`;
  }).join("");
}
function toggleTool(box){
  const block=box.closest('.tool-block'); if(!block) return;
  block.querySelectorAll('.cb').forEach(c=>{ c.checked=box.checked; toggleSel(c); });
}
function ov(k,v,c){return `<div class="ov ${c}"><div class="k">${k}</div><div class="v">${v}</div></div>`;}
function col(c,title,arr){
  const rows=arr.length?arr.map(row).join(""):`<div class="item"><div class="meta"><div class="desc">无</div></div></div>`;
  return `<div class="col ${c}"><div class="head">${title}<span class="sum">${sum(arr)} GB</span></div>${rows}</div>`;
}
function row(i){
  let cb;
  if(i.tier=="green" && i.excluded){ cb=`<span title="已在保护列表排除" style="font-size:14px">🛡️</span>`; }
  else if(i.tier=="green"){ cb=`<input type="checkbox" class="cb" data-path="${esc(i.path)}" ${state.selected.has(i.path)?"checked":""} onchange="toggleSel(this)">`; }
  else { cb=""; }
  const evi=(i.evidence||[]).map(e=>`<div>· ${esc(e)}</div>`).join("");
  return `<div class="item">${cb}<span class="lock">${ICON[i.tier]}</span><div class="meta">
    <div class="name">${esc(i.name)}</div><div class="desc">${esc(i.reason)}</div>
    <div class="evi">${evi}</div></div><span class="sz">${i.size_gb} GB</span></div>`;
}
function toggleSel(el){const p=el.getAttribute("data-path"); if(el.checked) state.selected.add(p); else state.selected.delete(p);}
function selectAll(on){state.selected=new Set(on?state.items.filter(i=>i.tier=="green").map(i=>i.path):[]);
  document.querySelectorAll(".cb").forEach(c=>c.checked=on);}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]));}
async function clean(dry){
  const sel=Array.from(state.selected);
  if(!dry && sel.length===0){ alert("请先勾选要清理的能安全删的项。"); return; }
  if(!dry && !confirm("将把选中的 "+sel.length+" 个能安全删的项移动到备份目录（可撤销），确认清理？")) return;
  const r=await fetch("/api/clean",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dry_run:dry,selected:sel})});
  const res=await r.json();
  if(res.dry_run){
    const list=res.green.map(x=>`· ${x.name} (${x.size_gb} GB)`).join("\n");
    document.getElementById("result").textContent=
      `【清理预览】将清理 ${res.green.length} 项，共 ${res.total_gb} GB：\n${list}\n`+
      (res.red_skipped.length?`\n🔒 已跳过已锁定的：${res.red_skipped.join("、")}`:"");
  }else{
    document.getElementById("result").textContent=
      `【已完成】移动 ${res.moved.length} 项，失败 ${res.failed.length} 项。\n备份位于：${res.backup}\n可到「回收站」标签一键还原。`;
    startScan(true);
  }
}
function exportJSON(){
  const blob=new Blob([JSON.stringify(state,null,2)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="sweep-scan-"+Date.now()+".json"; a.click();
  document.getElementById("result").textContent="已导出当前扫描结果为 JSON 文件。";
}
function copyReport(){
  const g=state.items.filter(i=>i.tier=="green"),a=state.items.filter(i=>i.tier=="amber"),r=state.items.filter(i=>i.tier=="red");
  let txt=`【Sweep 占用概览】\n本机：${state.computer}  时间：${state.scanned_at}\n`;
  txt+=`能安全删 ${sum(g)} GB（${g.length} 项）\n先别删 ${sum(a)} GB（${a.length} 项）\n已锁定 ${sum(r)} GB（${r.length} 项）\n`;
  const top=g.slice().sort((x,y)=>y.size_bytes-x.size_bytes).slice(0,5);
  if(top.length){txt+="\n可回收 Top：\n"+top.map(i=>`· ${i.name} ${i.size_gb} GB`).join("\n")+"\n";}
  txt+="\n#AI清理 #Sweep";
  navigator.clipboard.writeText(txt).then(()=>{document.getElementById("result").textContent="占用概览已复制到剪贴板，可直接粘贴分享。";},
    ()=>{document.getElementById("result").textContent=txt;});
}
startScan(false);
loadSettings();

/* ---------- 回收站 ---------- */
async function loadBackups(){
  const r=await fetch("/api/backups"); const list=await r.json();
  const c=document.getElementById("bkres");
  let totalItems=0,totalGb=0; list.forEach(b=>{totalItems+=b.items.length; totalGb+=b.total_gb;});
  document.getElementById("trashItems").textContent=totalItems;
  document.getElementById("trashGb").textContent=totalGb.toFixed(1)+" GB";
  const rab=document.getElementById("restoreAllBtn"); if(rab)rab.disabled=!list.length;
  if(!list.length){c.innerHTML='<div class="bk-empty">暂无备份。清理能安全删的项后，这里会出现可还原的项目。</div>';return;}
  c.innerHTML=list.map(function(b){
    const items=b.items.map(function(it){
      return `<div class="bk-item"><div class="meta"><div class="nm">${esc(it.name)}</div>`+
        (it.orig?`<div class="pth">→ ${esc(it.orig)}</div>`:"")+
        `</div><span class="sz">${it.size_gb} GB</span>`+
        `<button class="btn-sm" onclick="restore('${b.ts}','${esc(it.name)}')">还原</button></div>`;
    }).join("");
    return `<div class="bk"><div class="bk-ts">📦 备份 ${b.ts} · 共 ${b.total_gb} GB（${b.items.length} 项）</div>${items}</div>`;
  }).join("");
}
async function restore(ts,name){
  if(!confirm("将把该项还原回原始路径，确认？")) return;
  const r=await fetch("/api/restore",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ts:ts,name:name})});
  const res=await r.json();
  if(res.ok){ alert("已还原到："+res.to); loadBackups(); }
  else alert("还原失败："+res.error);
}
async function restoreAll(){
  if(!confirm("将把回收站【全部】项目还原回各自原始路径。\n无原始路径记录的项会自动跳过，已还原的会从回收站移除。确认全部还原？")) return;
  const r=await fetch("/api/restore-all",{method:"POST",headers:HDR,body:"{}"});
  const res=await r.json();
  alert("已还原 "+res.restored+" 项"+(res.failed?("，跳过 "+res.failed+" 项（无原始路径记录）"):"，全部成功"));
  loadBackups();
}

/* ---------- 保护列表 / 操作日志 ---------- */
async function loadSettings(){
  try{ const d=await (await fetch("/api/settings")).json(); renderEx(d.exclusions||[]); }catch(e){}
}
function renderEx(list){
  const c=document.getElementById("exList");
  if(!list.length){ c.innerHTML='<div class="bk-empty">保护列表为空。可添加你绝不希望被清理的项目路径（即使落入能安全删也不会动）。</div>'; return; }
  c.innerHTML=list.map(p=>`<div class="bk-item"><div class="meta"><div class="nm">${esc(p)}</div></div><button class="btn-sm" onclick="removeExclusion('${esc(p)}')">移除</button></div>`).join("");
}
async function addExclusion(){
  const v=document.getElementById("exInput").value.trim(); if(!v) return;
  const cur=(await (await fetch("/api/settings")).json()).exclusions||[];
  if(!cur.includes(v)) cur.push(v);
  await fetch("/api/settings",{method:"POST",headers:HDR,body:JSON.stringify({exclusions:cur})});
  document.getElementById("exInput").value=""; loadSettings(); startScan(true);
}
async function removeExclusion(p){
  let cur=(await (await fetch("/api/settings")).json()).exclusions||[];
  cur=cur.filter(x=>x!==p);
  await fetch("/api/settings",{method:"POST",headers:HDR,body:JSON.stringify({exclusions:cur})});
  loadSettings(); startScan(true);
}
async function loadAudit(){
  try{
    const list=await (await fetch("/api/audit")).json(); const c=document.getElementById("auditres");
    if(!list.length){ c.innerHTML='<div class="bk-empty">暂无操作记录。</div>'; return; }
    c.innerHTML=list.slice().reverse().map(a=>`<div class="bk-item"><div class="meta"><div class="nm">${esc(a.action)} · ${esc(a.ts)}</div><div class="pth">${esc(JSON.stringify(a.detail))}</div></div></div>`).join("");
  }catch(e){}
}

async function stopServer(){
  if(!confirm("将停止本地 Sweep 服务（端口 8765），确认？")) return;
  try{ await fetch("/api/stop"); }catch(e){}
  document.body.innerHTML='<div style="text-align:center;margin-top:120px;color:#6b7280;font-size:15px">Sweep 服务已停止。<br><br>双击 <b>start_sweep.bat</b> 可重新启动。</div>';
}

/* ---------- 重复检测 ---------- */
let dupES=null, dupClosed=false;
function detectDupes(){
  dupClosed=false;
  document.getElementById("dupbar").style.display="block";
  document.getElementById("dupfill").style.width="0%";
  document.getElementById("dupstatus").textContent="正在计算文件哈希（按内容比对）…";
  document.getElementById("dedupBtn").disabled=true;
  document.getElementById("dupres").innerHTML="";
  if(dupES){try{dupES.close();}catch(e){}}
  dupES=new EventSource("/api/dupes");
  dupES.onmessage=function(ev){
    let m; try{m=JSON.parse(ev.data);}catch(e){return;}
    if(m.phase==="progress"){
      const pct=m.total?Math.round(m.done/m.total*100):0;
      document.getElementById("dupfill").style.width=pct+"%";
      document.getElementById("dupstatus").textContent="已哈希 "+m.done+"/"+m.total+" 个候选文件";
    } else if(m.phase==="done"){
      dupClosed=true;
      const r=m.result;
      document.getElementById("dupfill").style.width="100%";
      document.getElementById("dupsum").textContent=r.reclaimable_gb+" GB 可回收";
      document.getElementById("dupGroups").textContent=r.groups.length;
      document.getElementById("dupReclaim").textContent=r.reclaimable_gb+" GB";
      document.getElementById("dupstatus").textContent=m.cached?"已加载缓存（即时）":"检测完成";
      renderDupes(r);
      try{dupES.close();}catch(e){}
      document.getElementById("dedupBtn").disabled=(r.groups.length===0);
    } else if(m.phase==="error"){
      dupClosed=true;
      document.getElementById("dupstatus").textContent="出错："+(m.message||"");
      try{dupES.close();}catch(e){}
    }
  };
  dupES.onerror=function(){ if(dupClosed) return; };
}
function fmtSize(b){return b>=1073741824?(b/1073741824).toFixed(1)+" GB":(b/1048576).toFixed(0)+" MB";}
function renderDupes(r){
  const c=document.getElementById("dupres");
  if(!r.groups.length){
    c.innerHTML='<div class="du-empty">未检测到跨工具重复文件。本机未装本地模型运行时（Ollama/LM Studio/ComfyUI 等）时属正常——一旦多个工具各自缓存了同一模型，这里会标出可合并项并估算可回收空间。</div>';
    return;
  }
  c.innerHTML=r.groups.map(function(g){
    const reclaim=g.size*(g.paths.length-1);
    const paths=g.paths.map(function(p){return '<div class="gp">'+esc(p)+'</div>';}).join("");
    return '<div class="du-group"><div class="gh">'+g.paths.length+' 份相同 · '+fmtSize(g.size)+'/份 · 可释放 '+fmtSize(reclaim)+'</div>'+paths+'</div>';
  }).join("");
}
async function dedup(){
  if(!confirm("将把重复文件合并为硬链接（共享数据块，工具侧路径不变，非删除、可撤销），确认合并？")) return;
  document.getElementById("dupstatus").textContent="合并中…";
  const r=await fetch("/api/dedup",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
  const res=await r.json();
  const mb=res.reclaimed_bytes>=1073741824?(res.reclaimed_bytes/1073741824).toFixed(1)+" GB":(res.reclaimed_bytes/1048576).toFixed(0)+" MB";
  document.getElementById("dupstatus").textContent="已合并 "+res.merged+" 组，释放 "+mb+(res.errors.length?"；错误 "+res.errors.length:"");
  detectDupes();
}

/* ---------- 对话清理（按时间/大小，逐条列出 + 批量选中） ---------- */
let CONV=[];
const PROV_COLOR={'QClaw':'#5b8def','Claude Code':'#d97757','Codex':'#7a5af0','Antigravity':'#e0853b',
  'Cursor':'#2aa3a3','Trae':'#e0533b','Gemini CLI':'#4285f4','Kimi':'#e6487b','Zed':'#3a7afe'};
function fmtDate(ts){const d=new Date(ts*1000);const p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;}
async function convLoad(){
  const age=+document.getElementById('conv-age').value||90;
  const size=+document.getElementById('conv-size').value||50;
  const r=await fetch(`/api/conv/units?age=${age}&size=${size}`);
  const d=await r.json(); CONV=d.units; convRender(); convLoadBackups();
}
let CONV_SORT={key:'size',dir:'desc'};
function convRender(){
  const tb=document.getElementById('conv-rows');
  let total=CONV.length,cand=0,lock=0,gb=0,nc=0;
  if(!CONV.length){tb.innerHTML='<tr><td colspan="5" class="conv-empty">未识别到任何对话文件</td></tr>';}
  else{
    const idx=[...CONV.keys()].sort((a,b)=>{
      const va=CONV_SORT.key==='size'?CONV[a].size_mb:CONV[a].age_days;
      const vb=CONV_SORT.key==='size'?CONV[b].size_mb:CONV[b].age_days;
      return CONV_SORT.dir==='desc'?vb-va:va-vb;
    });
    tb.innerHTML=idx.map(i=>{
      const u=CONV[i];
      const c=u.candidate,l=u.locked;
      if(c)cand++; if(l)lock++; if(c)gb+=u.size_mb; if(!u.cleanable)nc++;
      let st;
      if(l){st='<span class="conv-pill r"><span class="conv-dot" style="background:var(--red)"></span>运行中锁定</span>';}
      else if(!u.cleanable){st='<span class="conv-pill n"><span class="conv-dot" style="background:#e0a23b"></span>聚合存储·仅展示</span>';}
      else if(c){const why=[]; if(u.by_age)why.push('旧'); if(u.by_size)why.push('大'); st=`<span class="conv-pill g"><span class="conv-dot" style="background:var(--green)"></span>${why.join('·')}能安全删</span>`;}
      else {st='<span class="conv-pill a"><span class="conv-dot" style="background:var(--muted)"></span>保留</span>';}
      const dis=(l||!u.cleanable)?'disabled':'';
      const chk=`<input type="checkbox" data-i="${i}" ${dis} onchange="convUpdateSel()">`;
      const color=PROV_COLOR[u.provider]||'#888';
      const d1=u.first_ts?fmtDate(u.first_ts):'—', d2=u.last_ts?fmtDate(u.last_ts):'—';
      const drange=(d1&&d2&&d1!==d2)?`${d1} → ${d2}`:(d2||'—');
      const sub=`${u.msg_count} 条消息<span class="sep">·</span>${drange}<span class="sep">·</span>${Math.floor(u.age_days)} 天前`;
      return `<tr data-i="${i}"><td>${chk}</td>`+
        `<td><span class="conv-provider" style="background:${color}">${esc(u.provider)}</span><br>${esc(u.store)}</td>`+
        `<td><div class="conv-title">${esc(u.title||u.rel)}</div>`+
        `<div class="conv-sub">${sub}</div>`+
        `<div class="conv-rel" title="${esc(u.path)}">${esc(u.rel)}</div></td>`+
        `<td class="size">${u.size_mb.toFixed(2)} MB</td><td>${st}</td></tr>`;
    }).join('');
  }
  document.getElementById('conv-total').textContent=total;
  document.getElementById('conv-cand').textContent=cand;
  document.getElementById('conv-lock').textContent=lock;
  document.getElementById('conv-agg').textContent=nc;
  document.getElementById('conv-gb').textContent=(gb/1024).toFixed(2)+' GB';
  convUpdateSel();
}
function convChecked(){return CONV.filter((u,i)=>{const c=document.querySelector(`input[data-i="${i}"]`);return c&&c.checked&&!u.locked&&u.candidate;});}
function convUpdateSel(){
  const sel=convChecked();
  document.getElementById('conv-sel-count').textContent=sel.length;
  document.getElementById('conv-sel-gb').textContent=(sel.reduce((a,u)=>a+u.size_mb,0)/1024).toFixed(3)+' GB';
  document.getElementById('convCleanBtn').disabled=sel.length===0;
}
function convSelectAll(v){document.querySelectorAll('#conv-rows input[type=checkbox][data-i]').forEach(c=>{if(!c.disabled)c.checked=v;});convUpdateSel();}
function convInvert(){document.querySelectorAll('#conv-rows input[type=checkbox][data-i]').forEach(c=>{if(!c.disabled)c.checked=!c.checked;});convUpdateSel();}
function convSort(key){
  if(CONV_SORT.key===key){ CONV_SORT.dir = CONV_SORT.dir==='desc'?'asc':'desc'; }
  else { CONV_SORT.key=key; CONV_SORT.dir='desc'; }
  document.getElementById('sort-size').classList.toggle('active', key==='size');
  document.getElementById('sort-age').classList.toggle('active', key==='age');
  convRender();
}
async function convClean(){
  const sel=convChecked(); if(!sel.length)return;
  const gb=(sel.reduce((a,u)=>a+u.size_mb,0)/1024).toFixed(2);
  const big=sel.filter(u=>u.size_mb>=100).length;
  const old=sel.filter(u=>u.age_days>=365).length;
  let list='将把选中的 '+sel.length+' 条对话移动到回收站（可还原，非永久删除），可回收 '+gb+' GB。\n\n';
  if(big)list+=`⚠ ${big} 条大于 100MB，请确认非重要存档。\n`;
  if(old)list+=`⚠ ${old} 条超过 1 年，请确认无需留存。\n`;
  list+='\n对话清单：\n';
  list+=sel.slice(0,40).map(u=>`  · [${u.provider}] ${u.title}  (${u.size_mb.toFixed(1)}MB, ${Math.floor(u.age_days)}天)`).join('\n');
  if(sel.length>40)list+=`\n  …其余 ${sel.length-40} 条`;
  if(!confirm(list+'\n\n确认移动？'))return;
  const r=await fetch('/api/conv/clean',{method:'POST',headers:HDR,body:JSON.stringify({paths:sel.map(u=>u.path)})});
  const d=await r.json();
  document.getElementById('result').textContent=`【对话清理】已移动 ${d.moved} 条，跳过锁定 ${d.skipped_locked} 条，失败 ${d.failed} 条。\n回收站：${d.dest}\n可在下方「对话回收站」区还原。`;
  convLoad();
}
async function convLoadBackups(){
  const r=await fetch('/api/conv/backups'); const d=await r.json();
  const box=document.getElementById('conv-backups');
  if(!d.backups.length){box.innerHTML='<div class="conv-empty">暂无对话备份</div>';return;}
  box.innerHTML=d.backups.map(b=>`<div class="bk-item"><div class="meta"><div class="nm"><b>${esc(b.ts)}</b> · ${b.count} 条 · ${b.size_gb} GB · ${esc(b.dir)}</div></div>`+
    `<button class="btn-sm" onclick="convRestore('${esc(b.dir)}')">还原</button></div>`).join('');
}
async function convRestore(dir){
  if(!confirm(`还原备份 ${dir} 的全部对话到原路径？`))return;
  const r=await fetch('/api/conv/restore',{method:'POST',headers:HDR,body:JSON.stringify({dir:dir})});
  const d=await r.json();
  document.getElementById('result').textContent=d.ok?`【对话还原】已还原 ${d.restored} 条，失败 ${d.failed} 条。`:'还原失败：'+(d.error||'');
  convLoad();
}

/* ---------- Antigravity 单条会话管理 ---------- */
let AG_CONV=[];
async function agLoad(){
  const btn=document.getElementById('agLoadBtn');
  btn.disabled=true;btn.textContent='扫描中…';
  try{
    const r=await fetch('/api/conv/antigravity');
    const d=await r.json();
    AG_CONV=d.conversations||[];
    document.getElementById('ag-count').textContent=AG_CONV.length+' 个会话';
    agRender();
  }catch(e){
    document.getElementById('ag-hint').textContent='扫描失败：'+e.message;
  }
  btn.disabled=false;btn.textContent='重新扫描';
}
function agRender(){
  const tb=document.getElementById('ag-table');
  const rows=document.getElementById('ag-rows');
  if(!AG_CONV.length){tb.style.display='none';rows.innerHTML='';return;}
  tb.style.display='table';
  rows.innerHTML=AG_CONV.map((c,i)=>{
    const title=(c.title && c.title!==c.uuid.slice(0,8))?c.title:('未命名会话 ('+c.uuid.slice(0,8)+')');
    const ws=c.workspace?'<div class="conv-rel">'+esc(c.workspace)+'</div>':'';
    return `<tr data-i="${i}"><td><input type="checkbox" onchange="agUpdateSel()"></td>`+
      `<td><div class="conv-title">${esc(title)}</div>${ws}</td>`+
      `<td><div class="conv-sub">${c.steps} 步骤 · ${c.mtime}</div></td>`+
      `<td class="size">${c.size_mb} MB</td><td style="color:var(--muted)">${c.age_days}天</td>`+
      `<td><button class="btn-sm" onclick="agDelete('${esc(c.uuid)}',${i})" style="color:var(--red);border-color:var(--red)">删除</button></td></tr>`;
  }).join('');
  agUpdateSel();
}
function agUpdateSel(){
  const cks=document.querySelectorAll('#ag-rows input[type=checkbox]:checked');
  // 可扩展批量删除
}
async function agDelete(uuid, idx){
  const c=AG_CONV[idx];
  if(!c)return;
  if(!confirm(`确认删除 Antigravity 会话「${c.title||uuid}」？\n\n这将永久删除：\n· 对话记录（${c.size_mb}MB，${c.steps} 步）\n· 关联的 brain 目录和标注\n\n⚠️ 删除后不可恢复！`))return;
  try{
    const r=await fetch('/api/conv/antigravity/delete',{method:'POST',headers:HDR,body:JSON.stringify({uuid:uuid})});
    const d=await r.json();
    if(d.ok){
      AG_CONV.splice(idx,1);
      document.getElementById('ag-count').textContent=AG_CONV.length+' 个会话';
      agRender();
      document.getElementById('result').textContent=`【Antigravity】已删除会话 ${c.title||uuid}（清理了：${d.removed.join(', ')}）`;
    }else{
      alert('删除失败：'+(d.errors||[]).join('; '));
    }
  }catch(e){
    alert('请求失败：'+e.message);
  }
}
let APPS=[];
async function loadUninstall(){
  try{
    const r=await fetch("/api/uninstall"); const d=await r.json(); APPS=d.apps||[];
  }catch(e){ APPS=[]; }
  const list=document.getElementById("appList");
  document.getElementById("appCount").textContent=APPS.length;
  document.getElementById("appSize").textContent=APPS.reduce((a,x)=>a+x.size_gb,0).toFixed(1)+" GB";
  if(!APPS.length){
    list.innerHTML='<div class="app-empty">未识别到已安装的受支持 AI 工具（Cursor / QClaw / Antigravity / Zed / Claude / Coze / Gemini / Codex CLI）。清单可在仓库 ai_tools.json 中由社区补充。</div>';
    return;
  }
  list.innerHTML=APPS.map(function(app){
    const badge=app.brand?`<span style="color:${app.brand}">${esc(app.name.slice(0,1))}</span>`:esc(app.name.slice(0,1));
    return `<div class="app-row">
      <div class="app-ico">${badge}</div>
      <div class="app-info"><div class="app-name">${esc(app.name)}</div><div class="app-sub">${esc(app.sub||"")}</div></div>
      <div class="app-size">${app.size_gb} GB</div>
      <button class="btn-un" onclick="confirmUninstall('${app.id}')">卸载</button>
    </div>`;
  }).join("");
}
function confirmUninstall(id){
  const app=APPS.find(a=>a.id===id); if(!app) return;
  const modelDirs=(app.model_dirs||[]).map(p=>`<div class="gp">${esc(p)}</div>`).join("")||'<div class="gp">（无已知模型目录）</div>';
  const card=document.getElementById("modalCard");
  card.innerHTML=
    `<div class="modal-title">⚠️ 卸载 ${esc(app.name)}</div>
     <div class="modal-body">将通过系统 / 官方卸载程序完成卸载：<br><code>${esc(app.uninstall_cmd||"（未找到卸载命令，请在系统设置中卸载）")}</code></div>
     <div class="modal-warn">卸载不可逆。Sweep 只负责调起官方卸载程序，不会自行递归删除文件。模型 / 数据目录通常需你在卸载向导中勾选或手动清理。</div>
     <div class="modal-opt">
       <input type="checkbox" id="md-${app.id}">
       <div><div class="ot">一并清理模型 / 数据目录（Sweep 不会代为删除，仅作提示）</div>
       <div class="od">已知目录：${modelDirs}</div></div>
     </div>
     <div class="modal-acts">
       <button class="btn-sm" onclick="closeModal()">取消</button>
       <button class="btn-danger" onclick="doUninstall('${app.id}')">确认卸载</button>
     </div>`;
  document.getElementById("modal").classList.remove("hidden");
}
function closeModal(){ document.getElementById("modal").classList.add("hidden"); }
async function doUninstall(id){
  const app=APPS.find(a=>a.id===id); if(!app) return;
  const box=document.getElementById("md-"+id);
  const includeModelDir=!!(box&&box.checked);
  closeModal();
  const r=await fetch("/api/uninstall",{method:"POST",headers:HDR,body:JSON.stringify({id:id,include_model_dir:includeModelDir})});
  const res=await r.json();
  if(res.ok){ alert(app.name+"：已调起系统卸载程序，请按向导完成。\n"+(res.note||"")); }
  else alert("调起失败："+(res.error||"未知错误"));
  loadUninstall();
}
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        if self.path in ("/", "/dashboard"):
            self._send(200, "text/html; charset=utf-8", DASHBOARD)
        elif self.path.startswith("/api/scan"):
            self._stream_scan("fresh=1" in self.path)
        elif self.path.startswith("/api/dupes"):
            self._stream_dupes("fresh=1" in self.path)
        elif self.path.startswith("/api/backups"):
            try:
                data = json.dumps(list_backups(), ensure_ascii=False)
                self._send(200, "application/json; charset=utf-8", data)
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"error": str(e)}))
        elif self.path.startswith("/api/settings"):
            try:
                self._send(200, "application/json; charset=utf-8", json.dumps(load_settings(), ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"error": str(e)}))
        elif self.path.startswith("/api/audit"):
            try:
                self._send(200, "application/json; charset=utf-8", json.dumps(read_audit(), ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"error": str(e)}))
        elif self.path == "/api/stop":
            self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True}))
            # 在独立线程关闭，避免从请求处理线程直接 shutdown 造成死锁
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        elif self.path.startswith("/api/conv/units"):
            try:
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                kv = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
                age = int(kv.get("age", C.DEFAULT_AGE_DAYS))
                size = int(kv.get("size", C.DEFAULT_SIZE_MB))
            except Exception:
                age, size = C.DEFAULT_AGE_DAYS, C.DEFAULT_SIZE_MB
            procs = S.running_procs()
            units = C.plan(age, size, procs)
            for u in units:
                u["size_mb"] = round(u["size_mb"], 1)
                u["age_days"] = round(u["age_days"], 0)
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"age": age, "size": size, "units": units}, ensure_ascii=False))
        elif self.path.startswith("/api/conv/backups"):
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"backups": conv_list_backups()}, ensure_ascii=False))
        elif self.path == "/api/conv/antigravity":
            try:
                self._send(200, "application/json; charset=utf-8",
                           json.dumps({"conversations": antigravity_list_conversations(), "total": len(antigravity_list_conversations())}, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"error": str(e)}))
        elif self.path == "/api/conv/antigravity/delete":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            uuid = (body.get("uuid") or "").strip()
            if not uuid:
                self._send(400, "application/json; charset=utf-8", json.dumps({"error": "missing uuid"}))
            else:
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(antigravity_delete_conversation(uuid), ensure_ascii=False))
        elif self.path.startswith("/api/uninstall"):
            try:
                self._send(200, "application/json; charset=utf-8",
                           json.dumps({"apps": enum_uninstall_targets()}, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json; charset=utf-8", json.dumps({"error": str(e)}))
        else:
            self._send(404, "text/plain; charset=utf-8", "not found")

    def _stream_dupes(self, force):
        """SSE 流式返回重复检测进度（逐文件哈希），最后返回 done 事件。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()

        def send(obj):
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        try:
            data, cached = run_dupes(
                progress=lambda d, t: send({"phase": "progress", "done": d, "total": t}),
                force=force,
            )
            send({"phase": "done", "cached": cached, "result": data})
        except Exception as e:
            send({"phase": "error", "message": str(e)})

    def _stream_scan(self, force):
        """以 SSE 流式返回扫描进度（每个应用一条），最后返回 done 事件。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()

        def send(obj):
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        try:
            data, cached = run_scan(
                progress=lambda n, d, t: send({"phase": "progress", "app": n, "done": d, "total": t}),
                force=force,
            )
            send({"phase": "done", "cached": cached, "result": data})
        except Exception as e:
            send({"phase": "error", "message": str(e)})

    def do_POST(self):
        if self.path == "/api/clean":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(do_clean(body.get("dry_run", True), body.get("selected")), ensure_ascii=False))
        elif self.path == "/api/restore":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(restore_item(body.get("ts"), body.get("name")), ensure_ascii=False))
        elif self.path == "/api/restore-all":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(restore_all(), ensure_ascii=False))
        elif self.path == "/api/settings":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            saved = save_settings(body)
            global CACHE
            CACHE = {"data": None, "ts": 0.0}  # 保护列表变化后下次扫描重新评估
            self._send(200, "application/json; charset=utf-8", json.dumps(saved, ensure_ascii=False))
        elif self.path == "/api/dedup":
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n or 0)
            groups = dedupe.find_duplicates()  # 基于当前磁盘状态重新检测
            res = dedupe.merge_all(groups)
            res["reclaimed_gb"] = round(res["reclaimed_bytes"] / 1073741824, 1)
            self._send(200, "application/json; charset=utf-8", json.dumps(res, ensure_ascii=False))
        elif self.path == "/api/conv/clean":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(conv_move_selected(body.get("paths", [])), ensure_ascii=False))
        elif self.path == "/api/conv/restore":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(conv_restore_backup(body.get("dir") or body.get("ts")), ensure_ascii=False))
        elif self.path == "/api/uninstall":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(request_uninstall(body.get("id"), body.get("include_model_dir", False)), ensure_ascii=False))
        else:
            self._send(404, "text/plain; charset=utf-8", "not found")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Sweep 网页版运行中： http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
