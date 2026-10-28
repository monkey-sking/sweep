import conv_clean as C
import sweep_scan as S
from collections import defaultdict

procs = S.running_procs()
ai = [p for p in procs if any(k in p for k in ("codex", "qclaw", "antigravity", "cursor", "claude"))]
print("当前运行的 AI 相关进程:", ai or "（无）")

units = C.plan(90, 50)
agg = defaultdict(lambda: {"total": 0, "locked": 0, "cand": 0})
for u in units:
    a = agg[u["store"]]
    a["total"] += 1
    if u["locked"]:
        a["locked"] += 1
    if u["candidate"]:
        a["cand"] += 1

print(f"\n{'存储':46s}{'总数':>5s}{'被占用':>7s}{'可删':>6s}")
for k, v in agg.items():
    print(f"{k:46s}{v['total']:>5d}{v['locked']:>7d}{v['cand']:>6d}")
print(f"\n总锁定(真被占用)文件数: {sum(1 for u in units if u['locked'])}  (之前整块锁时代是 18)")

# 历史旧对话能否删：更松阈值下，来自 codex/qclaw 存储的候选
c = [u for u in C.plan(60, 30) if u["candidate"]]
from_codex_qclaw = [u for u in c if ("Codex" in u["store"] or "QClaw" in u["store"])]
print(f"age60/size30 可删候选: {len(c)} 条，其中来自 Codex/QClaw 存储(之前整块锁): {len(from_codex_qclaw)} 条")
for u in from_codex_qclaw[:6]:
    print(f"    [可删] {u['age_days']:.0f}天 {u['size_mb']:.1f}MB  {u['store']} / {u['rel'][:36]}")
