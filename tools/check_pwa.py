import json
import re
import sys

html = open("pwa/index.html", encoding="utf-8").read()
js = open("pwa/app.js", encoding="utf-8").read()

html_ids = set(re.findall(r'id="([^"]+)"', html))
js_ids = set(re.findall(r"\$\('([^']+)'\)", js))
missing = sorted(js_ids - html_ids)
print("JS 引用但 HTML 没有 (会崩):", missing if missing else "无 OK")
print("btnPick 已就位:", "btnPick" in html_ids)

# 检查 manifest 里的文件都存在
import os
man = json.load(open("pwa/manifest.webmanifest", encoding="utf-8"))
for icon in man.get("icons", []):
    p = os.path.join("pwa", icon["src"])
    print("icon %-16s %s" % (icon["src"], "存在" if os.path.exists(p) else "缺失!"))

print("\n新增函数:",
      "pickRemembered" in js, "| connect(forceChooser):",
      "async function connect(forceChooser)" in js)
sys.exit(1 if missing else 0)
