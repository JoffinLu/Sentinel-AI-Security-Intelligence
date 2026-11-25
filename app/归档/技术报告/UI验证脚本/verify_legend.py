# -*- coding: utf-8 -*-
"""验证采集动态卡片的严重度图例是否正确显示"""
import json, time, urllib.request, websocket

PORT = 9361
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(op)
req = urllib.request.Request(
    f"http://127.0.0.1:{PORT}/json/new?http://127.0.0.1:5173/dashboard", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=120)
seq = [0]; errs = []


def send(m, p=None):
    seq[0] += 1
    mid = seq[0]
    ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get('method') == 'Runtime.exceptionThrown':
            d = r['params']['exceptionDetails']
            errs.append((d.get('text', ''), (d.get('exception') or {}).get('description', '')[:140]))
        if r.get("id") == mid:
            return r.get("result", {})


def ev(e):
    return send("Runtime.evaluate", {"expression": e, "returnByValue": True}) \
        .get("result", {}).get("value")


send("Page.enable"); send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1500, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
for _ in range(30):
    time.sleep(1.5)
    if ev("(document.querySelectorAll('.ant-card').length||0)>2"):
        break
time.sleep(6)

d = ev(r"""(()=>{
  const cards=[...document.querySelectorAll('.ant-card')];
  const card=cards.find(c=>{const t=c.querySelector('.ant-card-head-title');
    return t && t.innerText.includes('采集动态');});
  if(!card) return {found:false};
  const legend=card.querySelector('.sentinel-feed-legend');
  const compact=card.querySelector('.sentinel-feed-sev-legend');
  const items=legend ? [...legend.querySelectorAll('.sentinel-feed-legend-item')] : [];
  return {found:true,
    titleText:card.querySelector('.ant-card-head-title').innerText.replace(/\s+/g,' ').trim(),
    hasLegend:!!legend,
    hasCompact:!!compact,
    items:items.map(i=>({
      text:i.innerText.replace(/\s+/g,' ').trim(),
      color:i.querySelector('.sentinel-feed-sev-dot')?
        getComputedStyle(i.querySelector('.sentinel-feed-sev-dot')).backgroundColor:null,
      w:i.getBoundingClientRect().width>0
    })),
    legendText: legend? legend.innerText.replace(/\s+/g,' ').trim():null,
    rows:card.querySelectorAll('tbody tr').length};
})()""")

print("=== 严重度图例验证 ===")
print(f"卡片存在: {d.get('found')}")
print(f"标题: {d.get('titleText')}")
print(f"常驻图例: {'✓ 有' if d.get('hasLegend') else '✗ 无'}"
      f" | 标题紧凑图例: {'✓ 有' if d.get('hasCompact') else '✗ 无'}")
print(f"\n图例内容: {d.get('legendText')}")
print("\n各图例项（含实际渲染色值）:")
for it in d.get('items', []):
    print(f"  · {it['text']:8} 色值={it['color']} {'✓可见' if it['w'] else '✗不可见'}")
print(f"\n表格行数: {d.get('rows')}")
print(f"运行时异常: {len(errs)}")
for t, dd in errs[:2]:
    print("  ", t, dd[:120])
ws.close()
