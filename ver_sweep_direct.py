import sweep_scan as S
from collections import Counter

s = S.scan()
tiers = Counter(i.get("tier") for i in s["items"])
gb = {t: round(sum(i["size_bytes"] for i in s["items"] if i.get("tier") == t) / 1024**3, 2)
      for t in tiers}
print("扫描条目总数:", len(s["items"]))
print("三档条目数:", dict(tiers))
print("三档体积(GB):")
for t in ("green", "amber", "red"):
    if t in gb:
        print(f"  {t}: {gb[t]} GB  ({tiers[t]} 项)")
# 对话历史类条目
conv = [i for i in s["items"] if "对话" in i.get("name", "") or "会话" in i.get("name", "") or "历史" in i.get("name", "")]
print(f"\n对话/会话/历史类条目: {len(conv)} 项")
for i in conv[:8]:
    print(f"  [{i.get('tier'):>5s}] {round(i['size_bytes']/1024/1024,1)}MB  {i['name']}")
