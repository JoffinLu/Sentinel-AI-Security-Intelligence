# -*- coding: utf-8 -*-
"""
用 CDP 实地探查情报页的 DOM，找出真正产生横向滚动条的元素。

背景：之前用 `.ant-table-body::-webkit-scrollbar:horizontal` 隐藏滚动条
无效——说明猜错了实际承载滚动的元素。这次直接量：
对页面上每个元素，比较 clientWidth 与 scrollWidth，
找出横向溢出的真实节点及其类名。
"""
import json
import time
import urllib.request

# 本地 CDP 请求必须绕开代理，否则被内部代理劫持返回 502
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)

import websocket  # type: ignore

CDP = "http://127.0.0.1:9333"
URL = "http://127.0.0.1:5173/intel"

# 1) 新建标签页
req = urllib.request.Request(f"{CDP}/json/new?{URL}", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws_url = tab["webSocketDebuggerUrl"]

ws = websocket.create_connection(ws_url, timeout=40)
_seq = [0]


def send(method, params=None, wait=True):
    _seq[0] += 1
    mid = _seq[0]
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    if not wait:
        return None
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == mid:
            return msg.get("result", {})


# 2) 开启并等待页面加载
send("Page.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
# SPA 首屏要拉多个接口，给足时间
for _ in range(20):
    time.sleep(1.5)
    st = send("Runtime.evaluate", {"expression": "document.readyState + '|' + document.querySelectorAll('.ant-table').length + '|' + location.pathname", "returnByValue": True})
    info = st.get("result", {}).get("value", "")
    if "complete" in info and not info.startswith("complete|0|"):
        print(f"[等待] {info}")
        break
else:
    print("[警告] 页面未渲染出表格，仍继续探查")

PROBE = r"""
(() => {
  // 找出所有横向溢出（scrollWidth > clientWidth）的元素
  const out = [];
  document.querySelectorAll('*').forEach((el) => {
    const dx = el.scrollWidth - el.clientWidth;
    if (dx > 1) {
      const cs = getComputedStyle(el);
      out.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 110),
        clientW: el.clientWidth,
        scrollW: el.scrollWidth,
        overflowX: cs.overflowX,
        // 关键：它是否真的画了可拖动的横向滚动条
        scrollable: dx > 1,
      });
    }
  });
  return out.slice(0, 30);
})()
"""

res = send("Runtime.evaluate", {"expression": PROBE, "returnByValue": True})
rows = res.get("result", {}).get("value", [])
print(f"横向溢出元素共 {len(rows)} 个：\n")
for r in rows:
    print(f"  {r['tag']}.{r['cls']}")
    print(f"     client={r['clientW']} scroll={r['scrollW']} "
          f"溢出={r['scrollW'] - r['clientW']}px overflowX={r['overflowX']}")

# 3) 特别检查表格相关节点
PROBE2 = r"""
(() => {
  const t = document.querySelector('.intel-vuln-table');
  if (!t) return {found: false};
  const info = {found: true, children: []};
  const walk = (el, depth) => {
    if (depth > 4) return;
    for (const c of el.children) {
      const cs = getComputedStyle(c);
      const dx = c.scrollWidth - c.clientWidth;
      if (cs.overflowX === 'auto' || cs.overflowX === 'scroll' || dx > 1) {
        info.children.push({
          depth,
          cls: (c.className || '').toString().slice(0, 100),
          overflowX: cs.overflowX,
          clientW: c.clientWidth,
          scrollW: c.scrollWidth,
          overflow: dx,
        });
      }
      walk(c, depth + 1);
    }
  };
  walk(t, 0);
  return info;
})()
"""
res2 = send("Runtime.evaluate", {"expression": PROBE2, "returnByValue": True})
info = res2.get("result", {}).get("value", {})
print("\n=== 表格内部可横滚节点 ===")
if not info.get("found"):
    print("  未找到 .intel-vuln-table")
else:
    for c in info["children"]:
        print(f"  [{c['depth']}] {c['cls']}")
        print(f"       overflowX={c['overflowX']} client={c['clientW']} "
              f"scroll={c['scrollW']} 溢出={c['overflow']}px")

ws.close()
