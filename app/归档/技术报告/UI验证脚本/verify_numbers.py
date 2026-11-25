# -*- coding: utf-8 -*-
"""验证看板四处改动：口径标注 / 组序号 / 刷新标识 / 时效性降级"""
import json, time, urllib.request, websocket

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(op)
P = 9349
req = urllib.request.Request(
    f"http://127.0.0.1:{P}/json/new?http://127.0.0.1:5173/dashboard", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
seq = [0]
errs = []


def send(m, p=None):
    seq[0] += 1
    mid = seq[0]
    ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get('method') == 'Runtime.exceptionThrown':
            errs.append(str(r['params']['exceptionDetails'].get('text', '')))
        if r.get("id") == mid:
            return r.get("result", {})


def ev(e):
    v = send("Runtime.evaluate", {"expression": e, "returnByValue": True}) \
        .get("result", {}).get("value")
    return v


send("Page.enable"); send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1500, "height": 1050, "deviceScaleFactor": 1, "mobile": False})
for i in range(40):
    time.sleep(1.5)
    if ev("document.querySelectorAll('.ant-card').length") > 2:
        break
time.sleep(7)

d = json.loads(ev(r"""
(() => {
  const txt = document.body.innerText;
  const near = (t, n) => { const i = txt.indexOf(t);
    return i < 0 ? null : txt.slice(i, i + n).replace(/\s+/g, ' ').trim(); };
  const m = (txt.match(/统计更新于[^\n]{0,32}/) || [null])[0];
  return JSON.stringify({
    total: near('情报总量', 60),
    source: near('数据源', 40),
    delay: near('采集延迟 P95', 50),
    timecard: near('监测时效性', 170),
    updated: m,
    groupIdx: document.querySelectorAll('.sentinel-feed-idx').length,
    feedRows: (document.querySelector('.ant-card-head-title')?.parentElement?.parentElement
      ?.querySelectorAll('tbody tr').length) || 0,
  });
})()
"""))

print("=== A 情报总量口径 ===")
print("  ", d.get("total"))
print("\n=== 数据源（未改） ===")
print("  ", d.get("source"))
print("\n=== D 采集延迟 P95 ===")
print("  ", d.get("delay"))
print("\n=== D 时效性卡片（样本不足应降级） ===")
print("  ", (d.get("timecard") or "")[:165])
print("\n=== C 刷新标识 ===")
print("  ", d.get("updated") or "✗ 未找到")
print("\n=== B 组序号 ===")
print(f"   残留 {d.get('groupIdx')} 个", "✓ 已去掉" if d.get("groupIdx") == 0 else "✗ 仍在")
if errs:
    print("\n运行时错误:", errs[:2])
ws.close()