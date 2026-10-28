#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正确的 squarified treemap（2D 方块）。标准 Bruls et al. 算法。"""
TOOLS = [
    ("Gemini",6.2,"#E5C07B"),("Antigravity",3.7,"#E06C9F"),("Codex",3.3,"#56B6C2"),
    ("QClaw",2.9,"#4C8DFF"),("Kimi",1.5,"#7EE787"),("Coze",0.6,"#C678DD"),
    ("OpenAI",0.6,"#A3BE8C"),("Claude",0.6,"#E5A962"),("InsightFace",0.6,"#88C0D0"),
    ("Cursor",0.3,"#ABB2BF"),("pip",0.3,"#EBCB8B"),("Trae",0.1,"#81A1C1"),
    ("NVIDIA",0.1,"#B48EAD"),("npm",0.1,"#D08770"),("Grok/HF",0.2,"#5E81AC"),
]
TOTAL_GB = 21.1

def normalize(values, total_area):
    s = sum(values)
    return [v/s*total_area for v in values]

def worst(row, length):
    s = sum(row)
    return max(s / min(row), max(row) / s)

def _squarify(children, x, y, w, h):
    if not children: return []
    if len(children) == 1:
        return [(x, y, w, h)]
    row = [children[0]]; i = 1
    while i < len(children):
        c = children[i]
        if w >= h:
            if worst(row, h) >= worst(row+[c], h):
                row.append(c); i += 1
            else: break
        else:
            if worst(row, w) >= worst(row+[c], w):
                row.append(c); i += 1
            else: break
    s = sum(row)
    rects = []
    if w >= h:
        rw = s/h; yy = y
        for area in row:
            rh = area/h; rects.append((x, yy, rw, rh)); yy += rh
        rects += _squarify(children[i:], x+rw, y, w-rw, h)
    else:
        rh = s/w; xx = x
        for area in row:
            rw = area/w; rects.append((xx, y, rw, rh)); xx += rw
        rects += _squarify(children[i:], x, y+rh, w, h-rh)
    return rects

W, H = 640, 320
areas = normalize([v for _,v,_ in TOOLS], W*H)
areas = sorted(areas, reverse=True)
rects = _squarify(areas, 0, 0, W, H)
# 配对回工具名/颜色（按面积降序对应）
order = sorted(TOOLS, key=lambda t:-t[1])
out = []
for (rx, ry, rw, rh), (n, v, c) in zip(rects, order):
    out.append(f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" fill="{c}" rx="2" stroke="#0E1116" stroke-width="1.5"/>')
    if rw>52 and rh>22:
        out.append(f'<text x="{rx+rw/2:.1f}" y="{ry+rh/2-2:.1f}" fill="#0E1116" font-size="11" font-weight="700" text-anchor="middle">{n}</text>')
        out.append(f'<text x="{rx+rw/2:.1f}" y="{ry+rh/2+12:.1f}" fill="#0E1116" font-size="10" text-anchor="middle">{v}GB</text>')
svg = f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{"".join(out)}</svg>'
open("treemap.svg","w",encoding="utf-8").write(svg)
print("blocks:", len(rects), "| distinct y:", len(set(round(r[1],0) for r in rects)), "distinct x:", len(set(round(r[0],0) for r in rects)))
