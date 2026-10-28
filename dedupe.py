#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sweep 跨工具重复检测引擎。

问题：同一模型/大文件常被多个 AI 工具各自存一份
（Ollama / HuggingFace / LM Studio / 各工具的 models 目录…），
浪费大量磁盘。

方案：按内容 sha256 找完全相同（不同 inode）的文件 → 多份副本。
合并：保留一份为基准，其余同盘副本替换为硬链接（os.link），
共享数据块，工具侧路径依旧可用，空间即时回收 (n-1)*size。
硬链接不删除数据，天然可撤销（删任一链接不影响其它）。
"""
import os
import hashlib

CHUNK = 1 << 20  # 1MB 读取块


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(CHUNK), b""):
            h.update(blk)
    return h.hexdigest()


def default_roots():
    """返回待查重的根目录：模型仓库 + 各 AI 工具下的模型类子目录。"""
    HOME = os.path.expanduser("~")
    LOCAL = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
    roots = [
        os.path.join(HOME, ".ollama", "models"),
        os.path.join(HOME, ".cache", "huggingface"),
        os.path.join(HOME, ".lmstudio", "models"),
        os.path.join(HOME, "ComfyUI", "models"),
        os.path.join(LOCAL, "ComfyUI", "models"),
    ]
    # 各 AI 应用数据根下，名为 models/weights/checkpoints/embeddings 的子目录
    app_roots = [
        os.path.join(HOME, ".claude"),
        os.path.join(HOME, ".cursor"),
        os.path.join(HOME, ".codex"),
        os.path.join(HOME, ".gemini"),
        os.path.join(HOME, ".kimi-code"),
        os.path.join(HOME, ".trae-cn"),
        os.path.join(HOME, ".coze"),
        os.path.join(HOME, ".qclaw"),
        os.path.join(HOME, ".antigravity"),
        os.path.join(LOCAL, "Zed"),
        os.path.join(LOCAL, "STranslate"),
    ]
    model_names = {"models", "weights", "checkpoints", "embeddings", "bin"}
    for r in app_roots:
        if not os.path.isdir(r):
            continue
        try:
            for e in os.listdir(r):
                if e.lower() in model_names:
                    p = os.path.join(r, e)
                    if os.path.isdir(p):
                        roots.append(p)
        except OSError:
            pass
    # 去重 + 仅保留存在的
    seen = set()
    out = []
    for r in roots:
        if r in seen:
            continue
        seen.add(r)
        if os.path.exists(r):
            out.append(r)
    return out


def find_duplicates(roots=None, min_size=1024 * 1024, progress=None):
    """
    返回重复组列表：[{hash, size, paths:[...]}, ...]
    仅包含「真实重复」：相同内容且为不同 inode（即实际占了多份空间）。
    已硬链接（同 inode）的组不算可回收。
    progress(done, total) 在每哈希一个候选文件时回调。
    """
    if roots is None:
        roots = default_roots()
    # 按体积预分组（同体积才可能是重复）
    size_map = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    fp = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    if st.st_size < min_size:
                        continue
                    size_map.setdefault(st.st_size, []).append(fp)
        except OSError:
            continue

    candidates = [p for ps in size_map.values() if len(ps) > 1 for p in ps]
    total = len(candidates)
    done = 0

    # hash -> {paths:[], inodes:set()}
    by_hash = {}
    for fp in candidates:
        try:
            h = _file_hash(fp)
            ino = os.stat(fp).st_ino
        except OSError:
            done += 1
            if progress:
                try:
                    progress(done, total)
                except Exception:
                    pass
            continue
        entry = by_hash.setdefault(h, {"paths": [], "inodes": set()})
        entry["paths"].append(fp)
        entry["inodes"].add(ino)
        done += 1
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass

    groups = []
    for h, entry in by_hash.items():
        if len(entry["paths"]) > 1 and len(entry["inodes"]) > 1:
            size = os.path.getsize(entry["paths"][0]) if entry["paths"] else 0
            groups.append({"hash": h, "size": size, "paths": entry["paths"]})
    groups.sort(key=lambda g: -g["size"])
    return groups


def merge_group(g):
    """合并一组真实重复：基准保留，其余同盘副本替换为硬链接。"""
    paths = g["paths"]
    size = g["size"]
    if len(paths) < 2:
        return {"status": "skipped"}
    canonical = paths[0]
    try:
        cdev = os.stat(canonical).st_dev
    except OSError as e:
        return {"status": "error", "msg": str(e)}
    reclaimed = 0
    same = 0
    cross = 0
    for p in paths[1:]:
        try:
            pdev = os.stat(p).st_dev
        except OSError:
            continue
        if pdev != cdev:
            cross += 1
            continue
        try:
            os.remove(p)
            os.link(canonical, p)
            reclaimed += size
            same += 1
        except OSError as e:
            return {"status": "error", "msg": str(e)}
    if same > 0:
        return {
            "status": "merged" if cross == 0 else "partial",
            "bytes": reclaimed,
            "same": same,
            "cross": cross,
        }
    return {"status": "skipped", "cross": cross}


def merge_all(groups):
    reclaimed = 0
    merged = 0
    partial = 0
    skipped = 0
    errors = []
    for g in groups:
        r = merge_group(g)
        if r["status"] == "merged":
            reclaimed += r["bytes"]
            merged += 1
        elif r["status"] == "partial":
            reclaimed += r["bytes"]
            partial += 1
        elif r["status"] == "skipped":
            skipped += 1
        else:
            errors.append({"hash": g["hash"], "msg": r.get("msg")})
    return {
        "reclaimed_bytes": reclaimed,
        "merged": merged,
        "partial": partial,
        "skipped": skipped,
        "errors": errors,
    }
