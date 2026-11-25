# -*- coding: utf-8 -*-
"""
用无头 Chrome + CDP 实地验证：横向滚动条是否已消失。

为什么需要这个：前两轮都「改了 CSS 但没验证」，
第一次选择器写错、第二次容器押错，都是靠猜。
本脚本直接量「每个元素的 clientWidth/scrollWidth」，
用数据回答「还有没有元素在横向溢出」。
"""
import json
import time
import urllib.request

import websocket  # type: ignore

PORT = 9334
CDP = f"http://127.0.0.1:{PORT}"
URL = "http://127.0.0.1:5173/intel"
WIDTH = int(__import__("os").environ.get("VW", "1600"))

# 本地 CDP 必须绕开代理，否则被内部代理劫持返回 502
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
     {"width": WIDTH, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

# 等表格真正渲染出来
ready = False
for _ in range(30):
    time.sleep(1.5)
    st = evaluate("document.readyState + '|' + "
                  "document.querySelectorAll('.ant-table').length")
    if st and "complete" in st and not st.endswith("|0"):
        print(f"[就绪] {st}")
        ready = True
        break
if not ready:
    print("[警告] 表格未渲染，探查结果不可信")

print(f"\n=== 视口宽度 {WIDTH}px 下的横向溢出元素 ===")
rows = evaluate(r"""
(() => {
  const out = [];
  document.querySelectorAll('*').forEach((el) => {
    const dx = el.scrollWidth - el.clientWidth;
    if (dx > 1) {
      const cs = getComputedStyle(el);
      out.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 100),
        clientW: el.clientWidth,
        scrollW: el.scrollWidth,
        overflowX: cs.overflowX
      });
    }
  });
  return out.slice(0, 20);
})()
""")
if not rows:
    print("  无横向溢出元素 ✓")
else:
    for r in rows:
        print(f"  {r['tag']}.{r['cls']}")
        print(f"     client={r['clientW']} scroll={r['scrollW']} "
              f"溢出={r['scrollW'] - r['clientW']}px overflowX={r['overflowX']}")

print("\n=== 表格横向滚动条计算样式（关键：offsetHeight 与 clientHeight）===")
res = evaluate(r"""
(() => {
  const t = document.querySelector('.intel-vuln-table');
  if (!t) return {found: false};
  const out = {found: true, nodes: []};
  ['.ant-table-body', '.ant-table-content', '.ant-table-container']
    .forEach((sel) => {
      const el = t.querySelector(sel);
      if (!el) return;
      const cs = getComputedStyle(el);
      out.nodes.push({
        sel,
        clientH: el.clientHeight,
        offsetH: el.offsetHeight,
        scrollW: el.scrollWidth,
        clientW: el.clientWidth,
        // 有横向滚动条时 offsetHeight 会比 clientHeight 多出滚动条高度
        hBar: el.offsetHeight - el.clientHeight,
        overflowX: cs.overflowX
      });
    });
  return out;
})()
""")
if not res.get("found"):
    print("  未找到 .intel-vuln-table")
else:
    for n in res.get("nodes", []):
        print(f"  {n['sel']}")
        print(f"     clientH={n['clientH']} offsetH={n['offsetH']} "
              f"横向条占高={n['hBar']}px overflowX={n['overflowX']}")
        if n["scrollW"] > n["clientW"]:
            print(f"⚠仍横向溢出 {n['scrollW'] - n['clientW']}px")
        if n["hBar"] <= 0:
            print("✓ 无横向滚动条占位")

ws.close()
