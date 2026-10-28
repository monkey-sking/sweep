import http.client

c = http.client.HTTPConnection("127.0.0.1", 8765, timeout=10)
c.request("GET", "/")
r = c.getresponse()
html = r.read().decode("utf-8", "ignore")
print("首页 HTML 长度:", len(html))
for k in ["扫描概览", "回收站", "保护列表", "审计", "重复检测", "开始扫描",
          "三色战报", "可清理", "建议保留", "不能清理", "对话", "导出", "标签"]:
    print(f"  {'✓' if k in html else '✗'} {k}")
