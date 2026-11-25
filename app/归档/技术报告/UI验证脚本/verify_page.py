# -*- coding: utf-8 -*-
"""
情报页实机体检：确认「能加载」之外，数据是否真的渲染出来了。

背景：500 修复后不能只看 HTTP 200 —— 接口通 ≠ 页面有内容。
本脚本读真实 DOM，确认三张知识底座卡片的条数与徽标状态，
并检查控制台有无报错。
"""
import json
import time
import urllib.request

import websocket  # type: ignore

PORT = 9335
CDP = f"http://127.0.0.1:{PORT}"
URL = "http://127.0.0.1:5173/intel"

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

req = urllib.request.Request(f"{CDP}/json/new?{URL}", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=45)
seq = [0]
console_errors = []


def send(method, params=None):
    seq[0] += 1
    mid = seq[0]
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        # 顺路收集控制台错误
        if msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
            console_errors.append(str(msg["params"]["args"])[:200])
        if msg.get("method") == "Runtime.exceptionThrown":
            console_errors.append(
                str(msg["params"]["exceptionDetails"].get("text", ""))[:200])
        if msg.get("id") == mid:
            return msg.get("result", {})


def evaluate(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("value")


send("Page.enable")
send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

# 等三张卡都渲染出内容
for _ in range(30):
    time.sleep(1.5)
    n = evaluate("document.querySelectorAll('.intel-paper-item').length")
    if n and n > 0:
        print(f"[就绪] 论文条目 {n} 条")
        break
else:
    print("[警告] 论文条目未渲染")

print("\n=== 页面内容实测 ===")
info = evaluate(r"""
(() => {
  const txt = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim().slice(0, 70) : null);
  const cards = [...document.querySelectorAll('.ant-card-head-title')].map(txt);
  return JSON.stringify({
    cards,
    paperItems: document.querySelectorAll('.intel-paper-item').length,
    policyItems: document.querySelectorAll('.intel-policy-card').length,
    atlasGroups: document.querySelectorAll('.atlas-chain-group').length,
    atlasHeads: [...document.querySelectorAll('.atlas-chain-head')].map(
      el => el.innerText.replace(/\s+/g, ' ').trim()),
    freshBadges: [...document.querySelectorAll('.kb-fresh')].map(
      el => el.innerText.replace(/\s+/g, ' ').trim()),
    tableRows: document.querySelectorAll('.intel-vuln-table tbody tr').length,
    errorToasts: document.querySelectorAll('.ant-message-error').length,
  });
})()
""")
d = json.loads(info)
print(f"  卡片: {d['cards']}")
print(f"  主表行数        : {d['tableRows']}")
print(f"  论文条目        : {d['paperItems']}")
print(f"  政策条目        : {d['policyItems']}")
print(f"  ATLAS 分组数: {d['atlasGroups']}")
print(f"  ATLAS 分组标题  : {d['atlasHeads'][:4]}{'...' if len(d['atlasHeads'])>4 else ''}")
print(f"  新鲜度徽标      : {d['freshBadges']}")
print(f"  错误提示 toast  : {d['errorToasts']} {'✓ 无' if d['errorToasts']==0 else '✗ 有'}")

print(f"\n=== 控制台错误 {len(console_errors)} 条 ===")
for e in console_errors[:6]:
    print(f"  {e}")
if not console_errors:
    print("  无 ✓")

ws.close()
