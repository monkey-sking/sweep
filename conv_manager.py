#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sweep · 对话清理管理器（Web 版）

把各家 AI 工具「每一条对话 = 一个文件」摊开列出，支持：
  - 逐条勾选
  - 一键全选 / 全不选 / 反选
  - 按「旧」全选（年龄命中策略）
  - 按「大」全选（大小命中策略）
  - 批量清理选中项（移动到回收站，可撤销、可还原，绝不永久擦除）
  - 运行中应用关联的存储自动锁定，不可勾选/不可清理

回收站独立目录：%USERPROFILE%\.sweep_backup\conversations\conv_<时间戳>\
复用 conv_clean 的枚举与命中判定逻辑。
"""
import os
import time
import json
import shutil
import datetime
import socket
import webbrowser
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sweep_scan as S
import conv_clean as C

HOME = S.HOME
BACKUP_ROOT = S.BACKUP_ROOT
CONV_BACKUP = os.path.join(BACKUP_ROOT, "conversations")
os.makedirs(CONV_BACKUP, exist_ok=True)
AUDIT = os.path.join(CONV_BACKUP, "audit.log")

DEFAULT_AGE = C.DEFAULT_AGE_DAYS
DEFAULT_SIZE = C.DEFAULT_SIZE_MB


def write_audit(action, detail):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = json.dumps({"ts": ts, "action": action, "detail": detail},
                      ensure_ascii=False)
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def move_selected(paths):
    """将用户勾选的对话文件移动到回收站。paths 必须在枚举白名单内、且未锁定。"""
    procs = S.running_procs()
    units = {u["path"]: u for u in C.enum_units(procs)}
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = os.path.join(CONV_BACKUP, f"conv_{ts}")
    manifest = []
    moved = failed = skipped = 0
    for p in paths:
        u = units.get(os.path.normpath(p))
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
            shutil.move(u["path"], dest)
            moved += 1
            manifest.append({"store": u["store"], "rel": u["rel"],
                             "src": u["path"], "dest": dest,
                             "size_bytes": u["size_bytes"]})
        except Exception:
            failed += 1
    if moved:
        with open(os.path.join(dest_root, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"type": "conv_cleanup", "moved_at": ts, "items": manifest},
                      f, ensure_ascii=False, indent=2)
    write_audit("clean", f"moved={moved} failed={failed} skipped_locked={skipped} dest={dest_root}")
    return {"moved": moved, "failed": failed, "skipped_locked": skipped, "dest": dest_root}


def list_backups():
    out = []
    for name in sorted(os.listdir(CONV_BACKUP), reverse=True):
        root = os.path.join(CONV_BACKUP, name)
        mpath = os.path.join(root, "manifest.json")
        if not os.path.isfile(mpath):
            continue
        try:
            m = json.load(open(mpath, encoding="utf-8"))
        except Exception:
            continue
        items = m.get("items", [])
        total_b = sum(i.get("size_bytes", 0) for i in items)
        out.append({"ts": m.get("moved_at", name), "dir": name,
                    "count": len(items), "size_gb": round(total_b / 1024 / 1024 / 1024, 2)})
    return out


def restore_backup(name):
    root = os.path.join(CONV_BACKUP, name)
    mpath = os.path.join(root, "manifest.json")
    if not os.path.isfile(mpath):
        return {"ok": False, "error": "manifest 不存在"}
    m = json.load(open(mpath, encoding="utf-8"))
    restored = failed = 0
    for it in m.get("items", []):
        dest = it["dest"]
        src = it["src"]
        try:
            d = os.path.dirname(src)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            if os.path.exists(dest):
                shutil.move(dest, src)
                restored += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    write_audit("restore", f"ts={name} restored={restored} failed={failed}")
    return {"ok": True, "restored": restored, "failed": failed}


def build_units(age, size):
    procs = S.running_procs()
    units = C.plan(age, size, procs)
    for u in units:
        u["size_mb"] = round(u["size_mb"], 1)
        u["age_days"] = round(u["age_days"], 0)
        u["name"] = u["rel"]
        u["store"] = u["store"]
        u["path"] = u["path"]
    return units


# ---------------------------------------------------------------- HTTP API
class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/conversations"):
            try:
                q = self.path.split("?", 1)[1]
                kv = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
                age = int(kv.get("age", DEFAULT_AGE))
                size = int(kv.get("size", DEFAULT_SIZE))
            except Exception:
                age, size = DEFAULT_AGE, DEFAULT_SIZE
            self._send(200, {"age": age, "size": size, "units": build_units(age, size)})
        elif self.path == "/api/backups":
            self._send(200, {"backups": list_backups()})
        elif self.path == "/" or self.path == "/index.html":
            html = PAGE
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        if self.path == "/api/clean":
            paths = body.get("paths", [])
            self._send(200, move_selected(paths))
        elif self.path == "/api/restore":
            name = body.get("ts") or body.get("dir")
            self._send(200, restore_backup(name))
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sweep · 对话清理管理器</title>
<style>
  :root{ --bg:#0c0e13; --card:#131720; --card2:#171c27; --line:rgba(255,255,255,.07);
         --txt:#e8ebf2; --mut:#9aa3b5; --dim:#646c7e; --brand1:#38bdf8; --brand2:#818cf8;
         --green:#34d399; --amber:#fbbf24; --red:#f87171; }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,'Segoe UI',system-ui,'Microsoft YaHei',sans-serif;
       background:var(--bg);color:var(--txt);font-size:14px;min-height:100vh}
  .wrap{max-width:1180px;margin:0 auto;padding:28px 24px 60px}
  .topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:22px}
  .brand{display:flex;align-items:center;gap:11px}
  .logo{width:38px;height:38px;border-radius:11px;
        background:linear-gradient(135deg,var(--brand1),var(--brand2));
        display:flex;align-items:center;justify-content:center;font-weight:800;color:#06121f;font-size:18px}
  .brand h1{font-size:18px;font-weight:700}
  .brand span{font-size:11px;color:var(--dim)}
  .controls{display:flex;align-items:flex-end;gap:14px;background:var(--card);
            border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:18px;flex-wrap:wrap}
  .field{display:flex;flex-direction:column;gap:5px}
  .field label{font-size:11px;color:var(--mut)}
  .field input{width:92px;background:var(--card2);border:1px solid var(--line);
        color:var(--txt);border-radius:8px;padding:7px 9px;font-size:13px}
  .btn{cursor:pointer;border:1px solid var(--line);background:var(--card2);color:var(--txt);
       border-radius:9px;padding:8px 13px;font-size:13px;font-weight:500;transition:.15s}
  .btn:hover{border-color:var(--brand1)}
  .btn.primary{background:linear-gradient(135deg,var(--brand1),var(--brand2));color:#06121f;
       border:none;font-weight:700}
  .btn:disabled{opacity:.4;cursor:not-allowed}
  .stats{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}
  .stat{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);
        border-radius:13px;padding:13px 15px}
  .stat .n{font-size:23px;font-weight:750}
  .stat .l{font-size:11px;color:var(--mut);margin-top:2px}
  .toolbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;
        background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:11px 13px;margin-bottom:12px;position:sticky;top:0;z-index:5}
  .toolbar .sp{flex:1}
  .toolbar .sel{font-size:12px;color:var(--mut)}
  .toolbar .sel b{color:var(--brand1)}
  table{width:100%;border-collapse:collapse;background:var(--card);
        border:1px solid var(--line);border-radius:13px;overflow:hidden}
  thead th{font-size:11px;color:var(--dim);font-weight:600;text-align:left;
        padding:11px 13px;border-bottom:1px solid var(--line);background:var(--card2)}
  tbody td{padding:11px 13px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:middle}
  tbody tr:hover{background:var(--card2)}
  tbody tr.sel{background:rgba(56,189,248,.06)}
  .pill{display:inline-flex;align-items:center;gap:6px;border-radius:20px;
        padding:3px 10px;font-size:11px;font-weight:600}
  .pill.g{background:rgba(52,211,153,.13);color:var(--green)}
  .pill.r{background:rgba(248,113,113,.13);color:var(--red)}
  .pill.a{background:rgba(154,163,181,.13);color:var(--mut)}
  .dot{width:7px;height:7px;border-radius:50%}
  .path{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--mut)}
  .size{font-weight:600;color:var(--txt)}
  .age{color:var(--mut)}
  input[type=checkbox]{width:16px;height:16px;accent-color:var(--brand1);cursor:pointer}
  .empty{text-align:center;color:var(--dim);padding:40px}
  .toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);
        background:var(--card2);border:1px solid var(--line);border-radius:10px;
        padding:12px 18px;font-size:13px;box-shadow:0 10px 30px rgba(0,0,0,.4);
        opacity:0;transition:.25s;pointer-events:none}
  .toast.show{opacity:1}
  .sec-title{font-size:14px;font-weight:650;margin:30px 0 12px}
  .backup{display:flex;align-items:center;justify-content:space-between;
        background:var(--card);border:1px solid var(--line);border-radius:11px;
        padding:12px 15px;margin-bottom:9px}
  .backup .meta{font-size:12px;color:var(--mut)}
  .backup .meta b{color:var(--txt)}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">
      <div class="logo">S</div>
      <div><h1>对话清理管理器</h1><span>Sweep · 逐条列出 AI 工具聊天记录</span></div>
    </div>
    <button class="btn" onclick="loadList()">重新扫描</button>
  </div>

  <div class="controls">
    <div class="field"><label>年龄阈值（天）</label><input id="age" type="number" value="90"></div>
    <div class="field"><label>大小阈值（MB）</label><input id="size" type="number" value="50"></div>
    <button class="btn primary" onclick="loadList()">应用并扫描</button>
    <span style="font-size:11px;color:var(--dim);margin-left:auto">
      命中规则：年龄 ≥ 阈值 <b>或</b> 大小 ≥ 阈值 → 标记为可清理</span>
  </div>

  <div class="stats">
    <div class="stat"><div class="n" id="s-total">0</div><div class="l">对话总数</div></div>
    <div class="stat"><div class="n" id="s-cand" style="color:var(--green)">0</div><div class="l">可清理（命中）</div></div>
    <div class="stat"><div class="n" id="s-lock" style="color:var(--red)">0</div><div class="l">运行中锁定</div></div>
    <div class="stat"><div class="n" id="s-gb" style="color:var(--brand1)">0 GB</div><div class="l">可清理总量</div></div>
  </div>

  <div class="toolbar">
    <button class="btn" onclick="selectAll(true)">全选</button>
    <button class="btn" onclick="selectAll(false)">全不选</button>
    <button class="btn" onclick="invert()">反选</button>
    <button class="btn" onclick="selectBy('old')">选中「旧」</button>
    <button class="btn" onclick="selectBy('big')">选中「大」</button>
    <div class="sp"></div>
    <span class="sel">已选 <b id="sel-count">0</b> 条 · <b id="sel-gb">0 GB</b></span>
    <button class="btn primary" id="cleanBtn" onclick="cleanSelected()" disabled>清理选中</button>
  </div>

  <table>
    <thead><tr>
      <th style="width:38px"></th>
      <th>工具 / 存储</th>
      <th>对话文件</th>
      <th style="width:90px">大小</th>
      <th style="width:80px">年龄</th>
      <th style="width:120px">状态</th>
    </tr></thead>
    <tbody id="rows"><tr><td colspan="6" class="empty">加载中…</td></tr></tbody>
  </table>

  <div class="sec-title">回收站（可还原）</div>
  <div id="backups"><div class="empty">暂无备份</div></div>
</div>

<div class="toast" id="toast"></div>

<script>
let UNITS = [];
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2600);}

async function loadList(){
  const age=+document.getElementById('age').value||90;
  const size=+document.getElementById('size').value||50;
  const r=await fetch(`/api/conversations?age=${age}&size=${size}`);
  const d=await r.json(); UNITS=d.units;
  renderRows();
  loadBackups();
}
function renderRows(){
  const tb=document.getElementById('rows');
  let total=UNITS.length, cand=0, lock=0, gb=0;
  if(!UNITS.length){tb.innerHTML='<tr><td colspan=6 class="empty">未识别到任何对话文件</td></tr>';}
  else{
    tb.innerHTML=UNITS.map((u,i)=>{
      const cand=u.candidate, lock=u.locked;
      if(cand)cand++; if(lock)lock++; if(cand)gb+=u.size_mb;
      let st, cls;
      if(lock){st='<span class="pill r"><span class="dot" style="background:var(--red)"></span>锁定</span>';}
      else if(cand){
        const why=[]; if(u.by_age)why.push('旧'); if(u.by_size)why.push('大');
        st=`<span class="pill g"><span class="dot" style="background:var(--green)"></span>${why.join('·')}可清</span>`;
      } else {st='<span class="pill a"><span class="dot" style="background:var(--dim)"></span>保留</span>';}
      const dis=lock?'disabled':'';
      const chk=`<input type="checkbox" data-i="${i}" ${dis} onchange="updateSel()">`;
      return `<tr data-i="${i}">
        <td>${chk}</td>
        <td>${u.store}</td>
        <td class="path" title="${u.path}">${u.rel}</td>
        <td class="size">${u.size_mb} MB</td>
        <td class="age">${u.age_days} 天</td>
        <td>${st}</td></tr>`;
    }).join('');
  }
  document.getElementById('s-total').textContent=total;
  document.getElementById('s-cand').textContent=cand;
  document.getElementById('s-lock').textContent=lock;
  document.getElementById('s-gb').textContent=(gb/1024).toFixed(2)+' GB';
  updateSel();
}
function checkedUnits(){return UNITS.filter((u,i)=>{
  const c=document.querySelector(`input[data-i="${i}"]`);return c&&c.checked&&!u.locked&&u.candidate;});}
function updateSel(){
  const sel=checkedUnits();
  document.getElementById('sel-count').textContent=sel.length;
  document.getElementById('sel-gb').textContent=(sel.reduce((a,u)=>a+u.size_mb,0)/1024).toFixed(3)+' GB';
  document.getElementById('cleanBtn').disabled=sel.length===0;
}
function selectAll(v){document.querySelectorAll('input[type=checkbox][data-i]').forEach(c=>{if(!c.disabled)c.checked=v;});updateSel();}
function invert(){document.querySelectorAll('input[type=checkbox][data-i]').forEach(c=>{if(!c.disabled)c.checked=!c.checked;});updateSel();}
function selectBy(kind){
  document.querySelectorAll('input[type=checkbox][data-i]').forEach(c=>{
    const i=+c.dataset.i,u=UNITS[i];
    if(!c.disabled&&u.candidate){c.checked=(kind==='old')?u.by_age:u.by_size;}
  });updateSel();
}
async function cleanSelected(){
  const sel=checkedUnits();
  if(!sel.length)return;
  if(!confirm(`将把 ${sel.length} 条对话移动到回收站（可还原，非永久删除）。继续？`))return;
  const r=await fetch('/api/clean',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({paths:sel.map(u=>u.path)})});
  const d=await r.json();
  toast(`已移动 ${d.moved} 条，跳过锁定 ${d.skipped_locked} 条，失败 ${d.failed} 条`);
  loadList();
}
async function loadBackups(){
  const r=await fetch('/api/backups');const d=await r.json();
  const box=document.getElementById('backups');
  if(!d.backups.length){box.innerHTML='<div class="empty">暂无备份</div>';return;}
  box.innerHTML=d.backups.map(b=>`<div class="backup">
    <div class="meta"><b>${b.ts}</b> · ${b.count} 条 · ${b.size_gb} GB · ${b.dir}</div>
    <button class="btn" onclick="restore('${b.dir}')">还原</button></div>`).join('');
}
async function restore(dir){
  if(!confirm(`还原备份 ${dir} 的所有对话到原路径？`))return;
  const r=await fetch('/api/restore',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dir})});
  const d=await r.json();
  toast(d.ok?`已还原 ${d.restored} 条，失败 ${d.failed} 条`:'还原失败：'+(d.error||''));
  loadList();
}
loadList();
</script>
</body>
</html>"""


def port_in_use(p):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(("127.0.0.1", p))
        s.close()
        return True
    except Exception:
        return False


def main():
    port = 8767
    while port_in_use(port):
        port += 1
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}"
    print(f"Sweep 对话清理管理器已启动：{url}")
    threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
