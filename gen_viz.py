#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Sweep 扫描概览页的多形态空间可视化 SVG（真实本机数据）。
输出：donut.svg(环形占比) / treemap.svg(块图) / sunburst.svg(旭日图) / monograms.svg(兜底字母徽标，单文件多图标)"""
import math, json

# 真实本机 AI 资产（来自 sweep 盘点 v3，单位 GB）
TOOLS = [
    ("Gemini",     6.2, "#E5C07B"),
    ("Antigravity",3.7, "#E06C9F"),
    ("Codex",      3.3, "#56B6C2"),
    ("QClaw",      2.9, "#4C8DFF"),
    ("Kimi",       1.5, "#7EE787"),
    ("Coze",       0.6, "#C678DD"),
    ("OpenAI",     0.6, "#A3BE8C"),
    ("Claude",     0.6, "#E5A962"),
    ("InsightFace",0.6, "#88C0D0"),
    ("Cursor",     0.3, "#ABB2BF"),
    ("pip",        0.3, "#EBCB8B"),
    ("Trae",       0.1, "#81A1C1"),
    ("NVIDIA",     0.1, "#B48EAD"),
    ("npm",        0.1, "#D08770"),
    ("Grok/HF",    0.2, "#5E81AC"),
]
TOTAL = sum(t[1] for t in TOOLS)  # ~20.4 GB

def rgba(hexcol, a=1.0):
    hexcol = hexcol.lstrip("#")
    r, g, b = int(hexcol[0:2],16), int(hexcol[2:4],16), int(hexcol[4:6],16)
    return f"rgba({r},{g},{b},{a})"

# ---------- 1) Donut 环形 ----------
def donut(cx, cy, R, r, items, total):
    parts = []
    ang = -math.pi/2  # 从顶部开始
    for name, val, col in items:
        frac = val/total
        a0, a1 = ang, ang + frac*2*math.pi
        large = 1 if (a1-a0) > math.pi else 0
        x0,y0 = cx+R*math.cos(a0), cy+R*math.sin(a0)
        x1,y1 = cx+R*math.cos(a1), cy+R*math.sin(a1)
        xi1,yi1 = cx+r*math.cos(a1), cy+r*math.sin(a1)
        xi0,yi0 = cx+r*math.cos(a0), cy+r*math.sin(a0)
        path = (f"M{x0:.2f} {y0:.2f} A{R} {R} 0 {large} 1 {x1:.2f} {y1:.2f} "
                f"L{xi1:.2f} {yi1:.2f} A{r} {r} 0 {large} 0 {xi0:.2f} {yi0:.2f} Z")
        parts.append(f'<path d="{path}" fill="{col}" stroke="#0E1116" stroke-width="2"/>')
        # 标签（仅大段）
        if frac > 0.06:
            mid = (a0+a1)/2
            lx, ly = cx+(R+r)/2*math.cos(mid), cy+(R+r)/2*math.sin(mid)
            pct = f"{frac*100:.0f}%"
            parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#0E1116" font-size="11" font-weight="700" text-anchor="middle" dominant-baseline="middle">{pct}</text>')
        ang = a1
    center = (f'<text x="{cx}" y="{cy-6}" fill="#E6E9EF" font-size="22" font-weight="700" text-anchor="middle">{total:.1f} GB</text>'
              f'<text x="{cx}" y="{cy+16}" fill="#9AA4B2" font-size="12" text-anchor="middle">总 AI 资产占用</text>')
    return "".join(parts)+center

donut_svg = (f'<svg width="320" height="320" viewBox="0 0 320 320" xmlns="http://www.w3.org/2000/svg">'
             + donut(160,160,140,82,TOOLS,TOTAL)
             + '</svg>')

# ---------- 2) Treemap 块图 (squarified) ----------
def squarify(items, x, y, w, h):
    """极简 squarify：按面积比例铺矩形"""
    total = sum(v for _,v,_ in items)
    rects = []
    # 归一化面积为像素
    scale = (w*h)/total
    areas = [(n, v*scale, c) for n,v,c in items]
    # 简单二分铺放：交替横向/纵向切
    cur_x, cur_y, cur_w, cur_h = x, y, w, h
    horiz = w >= h
    i = 0
    while i < len(areas):
        # 每行(列)放若干，使长宽比尽量接近
        row = [areas[i]]; rowsum = areas[i][1]; i+=1
        while i < len(areas):
            # 试加一项，判断长宽比是否更差
            cand = areas[i]; newsum = rowsum + cand[1]
            if horiz:
                rw = cur_w; rh = newsum/cur_w
                # 当前行高度
                best = max(rh, cur_h*0.4)
            else:
                rh = cur_h; rw = newsum/cur_h
            row.append(cand); rowsum = newsum; i+=1
        # 实际铺设该行
        if horiz:
            rh = rowsum/cur_w
            ry = cur_y
            cx_offset = cur_x
            for n, ar, c in row:
                rw = ar/rh
                rects.append((n, c, cx_offset, ry, rw, rh))
                cx_offset += rw
            cur_y += rh; cur_h -= rh
        else:
            rw = rowsum/cur_h
            rx = cur_x
            for n, ar, c in row:
                rh = ar/rw
                rects.append((n, c, rx, cur_y, rw, rh))
                cur_y += rh
            cur_x += rw; cur_w -= rw
        horiz = not horiz
        if cur_w<=0 or cur_h<=0: break
    return rects

def treemap(W, H, items):
    rects = squarify(items, 0, 0, W, H)
    out=[]
    for n, c, x, y, w, h in rects:
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{c}" rx="2" stroke="#0E1116" stroke-width="1.5"/>')
        if w>54 and h>26:
            cx, cy = x+w/2, y+h/2
            out.append(f'<text x="{cx:.1f}" y="{cy-4:.1f}" fill="#0E1116" font-size="12" font-weight="700" text-anchor="middle">{n}</text>')
            # 估算该块 GB
    return "".join(out)

tm_items = [(n, v, c) for n,v,c in TOOLS]
treemap_svg = (f'<svg width="640" height="320" viewBox="0 0 640 320" xmlns="http://www.w3.org/2000/svg">'
               + treemap(640,320,tm_items) + '</svg>')

# ---------- 3) Sunburst 旭日图 ----------
def sunburst(cx, cy, r_inner, r_outer, items, total):
    parts=[]
    ang = -math.pi/2
    for name, val, col in items:
        frac = val/total
        a0,a1 = ang, ang+frac*2*math.pi
        large = 1 if (a1-a0)>math.pi else 0
        x0,y0 = cx+r_inner*math.cos(a0), cy+r_inner*math.sin(a0)
        x1,y1 = cx+r_inner*math.cos(a1), cy+r_inner*math.sin(a1)
        x2,y2 = cx+r_outer*math.cos(a1), cy+r_outer*math.sin(a1)
        x3,y3 = cx+r_outer*math.cos(a0), cy+r_outer*math.sin(a0)
        path=(f"M{x0:.2f} {y0:.2f} A{r_inner} {r_inner} 0 {large} 1 {x1:.2f} {y1:.2f} "
              f"L{x2:.2f} {y2:.2f} A{r_outer} {r_outer} 0 {large} 0 {x3:.2f} {y3:.2f} Z")
        parts.append(f'<path d="{path}" fill="{col}" stroke="#0E1116" stroke-width="1.5" opacity="0.92"/>')
        ang=a1
    # 中心留白圆
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_inner-6}" fill="#0E1116"/>')
    parts.append(f'<text x="{cx}" y="{cy-6}" fill="#E6E9EF" font-size="18" font-weight="700" text-anchor="middle">{total:.1f} GB</text>')
    parts.append(f'<text x="{cx}" y="{cy+14}" fill="#9AA4B2" font-size="11" text-anchor="middle">按工具分布</text>')
    return "".join(parts)

sun_svg = (f'<svg width="320" height="320" viewBox="0 0 320 320" xmlns="http://www.w3.org/2000/svg">'
           + sunburst(160,160,52,148,TOOLS,TOTAL) + '</svg>')

# ---------- 4) 字母徽标兜底 (Codex/Gemini CLI/Trae/Kimi 等无 exe 的) ----------
MONOS = [
    ("Codex",   "#56B6C2", "C"),
    ("Gemini",  "#E5C07B", "G"),
    ("Trae",    "#81A1C1", "T"),
    ("Kimi",    "#7EE787", "K"),
    ("Grok",    "#5E81AC", "X"),
]
def mono(cx,cy,R,col,letter):
    return (f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="{col}"/>'
            f'<text x="{cx}" y="{cy+1}" fill="#0E1116" font-size="{int(R*1.1)}" font-weight="800" text-anchor="middle" dominant-baseline="middle">{letter}</text>')
monos_svg = '<svg width="200" height="48" viewBox="0 0 200 48" xmlns="http://www.w3.org/2000/svg">'
for i,(n,col,l) in enumerate(MONOS):
    monos_svg += mono(24+i*44, 24, 18, col, l)
    monos_svg += f'<text x="{24+i*44}" y="44" fill="#9AA4B2" font-size="9" text-anchor="middle">{n}</text>'
monos_svg += '</svg>'

for fn, svg in [("donut.svg",donut_svg),("treemap.svg",treemap_svg),("sunburst.svg",sun_svg),("monograms.svg",monos_svg)]:
    with open(fn,"w",encoding="utf-8") as f:
        f.write(svg)
print("written: donut.svg, treemap.svg, sunburst.svg, monograms.svg")
print("TOTAL GB =", round(TOTAL,1))

# 同时输出 donut 各段百分比供图例使用
print("\n--- 环形分段(%) ---")
for n,v,c in TOOLS:
    print(f"  {n:12} {v:>4}GB  {v/TOTAL*100:5.1f}%")
