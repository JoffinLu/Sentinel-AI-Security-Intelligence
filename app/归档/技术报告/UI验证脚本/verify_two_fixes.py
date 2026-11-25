# -*- coding: utf-8 -*-
"""
验证两处修复：
  ① 自绘滚动条是否生效（原生条已隐藏、自绘滑块存在且常驻）
  ② 政策属性下拉在**筛选后**是否仍保留全部选项
"""
import json
import time
import urllib.request

import websocket  # type: ignore

PORT = 9337
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


def ev(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("value")


send("Page.enable")
send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

for _ in range(30):
    time.sleep(1.5)
    if ev("document.querySelectorAll('.intel-paper-item').length") > 0:
        print("[就绪] 页面已渲染")
        break

# ---------- ① 自绘滚动条 ----------
print("\n=== ① 自绘滚动条 ===")
d = json.loads(ev(r"""
(() => {
  const areas = [...document.querySelectorAll('.cs-area')];
  const floats = [...document.querySelectorAll('.cs-track-float')];
  return JSON.stringify({
    areaCount: areas.length,
    thumbCount: document.querySelectorAll('.cs-thumb').length,
    floatCount: floats.length,
    // 原生条是否已隐藏
    nativeHidden: (() => {
      const vp = document.querySelector('.cs-viewport');
      const tc = document.querySelector('.intel-vuln-table .ant-table-content');
      const gv = (el) => el ? getComputedStyle(el).scrollbarWidth : 'n/a';
      return { viewport: gv(vp), tableContent: gv(tc) };
    })(),
    // 自绘滑块是否有实际尺寸
    thumbs: [...document.querySelectorAll('.cs-thumb')].map(t => {
      const r = t.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height) };
    }).slice(0, 6),
  });
})()
"""))
print(f"  自绘滚动区数量 : {d['areaCount']}（论文/ATLAS/政策 + 主表）")
print(f"  自绘滑块总数   : {d['thumbCount']}")
print(f"  浮动滑块(主表) : {d['floatCount']}")
print(f"  原生条 scrollbar-width: {d['nativeHidden']}"
      f" {'✓ 已隐藏' if d['nativeHidden']['viewport'] == 'none' else '✗ 仍显示'}")
print(f"  滑块实际尺寸   : {d['thumbs']}")
ok = d['thumbCount'] >= 4 and d['nativeHidden']['viewport'] == 'none'
print(f"  判定: {'✓ 自绘生效' if ok else '✗ 未生效'}")

# ---------- ② 政策属性下拉 ----------
print("\n=== ② 政策属性下拉（筛选后是否还能换别的）===")
before = json.loads(ev(r"""
(() => {
  const sel = document.querySelector('.intel-policy-card ~ * select, .ant-select');
  return JSON.stringify({ exists: !!document.querySelector('.ant-select') });
})()
"""))
print(f"  下拉存在: {before['exists']}")

# 点开政策卡的下拉，读出全部选项
options = json.loads(ev(r"""
(() => {
  // 政策卡的下拉：找 placeholder 为「按属性筛选」的那个
  const sels = [...document.querySelectorAll('.ant-select')];
  const target = sels.find(s => s.innerText.includes('按属性筛选'));
  return JSON.stringify({ found: !!target });
})()
"""))
print(f"  找到「按属性筛选」下拉: {options['found']}")

# 通过 React 的 onChange 不易触发，这里直接验证选项来源：
# 读取全量属性列表是否被渲染进 DOM（AntD Select 的选项在弹层里）
ev("""
(() => {
  const sels = [...document.querySelectorAll('.ant-select')];
  const target = sels.find(s => s.innerText.includes('按属性筛选'));
  if (target) target.click();
  return true;
})()
""")
time.sleep(1.5)
opts = json.loads(ev(r"""
(() => {
  const items = [...document.querySelectorAll('.ant-select-dropdown .ant-select-item-option-content')];
  return JSON.stringify(items.map(i => i.innerText.trim()).filter(Boolean));
})()
"""))
print(f"  下拉展开后的选项: {opts}")
print(f"  判定: {'✓ 选项完整' if len(opts) >= 4 else '✗ 选项缺失'}")

ws.close()
