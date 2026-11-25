# -*- coding: utf-8 -*-
"""验证监控看板卡片顺序。"""
import json, time, urllib.request, websocket

PORT = 9350
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(op)
req = urllib.request.Request(
    f"http://127.0.0.1:{PORT}/json/new?http://127.0.0.1:5173/dashboard", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
seq = [0]


def send(m, p=None):
    seq[0] += 1
    mid = seq[0]
    ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == mid:
            return r.get("result", {})


def ev(e):
    return send("Runtime.evaluate", {"expression": e, "returnByValue": True}) \
        .get("result", {}).get("value")


send("Page.enable"); send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1600, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
for _ in range(40):
    time.sleep(1.5)
    if ev("document.querySelectorAll('.ant-card').length") > 2:
        break
time.sleep(7)

d = json.loads(ev(r"""
(() => {
  // 按 top 坐标收集所有卡片标题，排序即视觉顺序
  const items = [];
  document.querySelectorAll('.ant-card-head-title').forEach(t => {
    const card = t.closest('.ant-card');
    if (!card) return;
    const r = card.getBoundingClientRect();
    items.push({ title: t.innerText.trim(), top: Math.round(r.top), left: Math.round(r.left) });
  });
  // 指标卡没有 .ant-card，用 MetricCard 的容器补齐
  document.querySelectorAll('.sentinel-metric, [class*="metric"]').forEach(m => {
    const txt = m.innerText || '';
    if (!txt.includes('情报总量') && !txt.includes('采集延迟')) return;
    const r = m.getBoundingClientRect();
    if (r.width === 0) return;
    items.push({ title: '指标卡:' + txt.slice(0, 14).replace(/\s+/g, ''), top: Math.round(r.top), left: Math.round(r.left) });
  });
  // 同top 的（并排）按 left 排
  items.sort((a, b) => (Math.abs(a.top - b.top) < 40 ? a.left - b.left : a.top - b.top));
  // 去掉重复（指标卡可能被扫两次）
  const seen = new Set();
  const uniq = items.filter(i => { const k = i.title + i.top; if (seen.has(k)) return false; seen.add(k); return true; });
  return JSON.stringify(uniq.map(i => `${i.title} @top=${i.top},left=${i.left}`));
})()
"""))
print("=== 看板从上到下的卡片顺序 ===")
for i, line in enumerate(d, 1):
    print(f"  {i}. {line}")
ws.close()
