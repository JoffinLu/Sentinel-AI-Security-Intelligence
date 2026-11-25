# -*- coding: utf-8 -*-
"""
验证横向滚动条「常驻显示」是否生效。

判据（不靠肉眼）：
  1) `overflow-x` 计算值必须是 `scroll`（而非 auto）——auto 会被系统自动隐藏
  2) `offsetHeight - clientHeight` 的差值 > 0 —— 证明滚动条**真的占位**
  3) 鼠标**移开**（在页面顶部空白处）后再测一次 —— 两次都 >0 才叫常驻

第 3 步是关键：不移开鼠标量一次，只能证明「悬停时在」，
正是用户抱怨的「时隐时现」。
"""
import json
import time
import urllib.request

import websocket  # type: ignore

PORT = 9336
CDP = f"http://127.0.0.1:{PORT}"
URL = "http://127.0.0.1:5173/intel"

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
req = urllib.request.Request(f"{CDP}/json/new?{URL}", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=45)
seq = [0]


def send(method, params=None):
    seq[0] += 1
    mid = seq[0]
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == mid:
            return msg.get("result", {})


def evaluate(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("value")


send("Page.enable")
send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

for _ in range(30):
    time.sleep(1.5)
    if evaluate("document.querySelectorAll('.intel-paper-item').length") > 0:
        print("[就绪] 页面已渲染")
        break

PROBE = r"""
(() => {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    // 表格用横向滚动条(量高度)，卡片用纵向滚动条(量宽度)
    const horiz = sel.includes('intel-vuln-table');
    return horiz
      ? { dir: 'x', of: cs.overflowX, bar: el.offsetHeight - el.clientHeight }
      : { dir: 'y', of: cs.overflowY, bar: el.offsetWidth - el.clientWidth };
  };
  return JSON.stringify({
    tableContent: pick('.intel-vuln-table .ant-table-content'),
    tableBody: pick('.intel-vuln-table .ant-table-body'),
    paper: pick('.intel-paper-list'),
    policy: pick('.intel-policy-list'),
    atlas: pick('.atlas-chain'),
  });
})()
"""


def report(tag):
    d = json.loads(evaluate(PROBE))
    print(f"\n=== {tag} ===")
    for k, v in d.items():
        if not v:
            print(f"  {k:14} (未找到)")
            continue
        ok = v["bar"] > 0
        flag = "✓ 轨道常驻" if ok else "✗ 无占位"
        print(f"  {k:14} overflow-{v['dir']}={v['of']:7} 条占位={v['bar']:3}px  {flag}")
    return d


# ① 鼠标停在页面外（0,0 是页头空白区，不在表格上）
send("Input.dispatchMouseEvent",
     {"type": "mouseMoved", "x": 5, "y": 2})
time.sleep(2)
d1 = report("① 鼠标移开（停在页头空白处）")

# ② 鼠标悬停在表格中部
send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 500, "y": 400})
time.sleep(2)
d2 = report("② 鼠标悬停在表格上")

print("\n=== 结论 ===")
ok1 = d1["tableContent"] and d1["tableContent"]["bar"] > 0
ok2 = d2["tableContent"] and d2["tableContent"]["bar"] > 0
print(f"  移开时可见: {'✓' if ok1 else '✗'}")
print(f"  悬停时可见: {'✓' if ok2 else '✗'}")
print(f"  是否常驻  : {'✓ 是（两次都占位）' if ok1 and ok2 else '✗ 否（仍随悬停变化）'}")
print(f"  overflow-x: {d1['tableContent']['of']} "
      f"({'正确为 scroll' if d1['tableContent']['of']=='scroll' else '仍是 auto'})")
print("  各卡片纵向滚动:")
for k in ('paper','policy','atlas'):
    v=d1[k]
    if v: print(f"    {k:8} overflow-y={v['of']:8} 占位={v['bar']}px")

ws.close()
