# -*- coding: utf-8 -*-
"""验证采集动态卡片的未读角标：首屏无红点 → 新采集后出现 → 点已读清零"""
import json, time, urllib.request, websocket

PORT = 9359
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(op)


def new_tab():
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?http://127.0.0.1:5173/dashboard", method="PUT")
    return json.loads(urllib.request.urlopen(req).read())


def connect(tab):
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=180)
    state = {"seq": 0, "errs": []}

    def send(m, p=None):
        state["seq"] += 1
        mid = state["seq"]
        ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get('method') == 'Runtime.exceptionThrown':
                d = r['params']['exceptionDetails']
                state["errs"].append((d.get('text', ''),
                                      (d.get('exception') or {}).get('description', '')[:140]))
            if r.get("id") == mid:
                return r.get("result", {})

    def ev(e):
        return send("Runtime.evaluate", {"expression": e, "returnByValue": True}) \
            .get("result", {}).get("value")

    return ws, send, ev, state


def probe(ev):
    return ev(r"""(()=>{
  const cards=[...document.querySelectorAll('.ant-card')];
  const card=cards.find(c=>{const t=c.querySelector('.ant-card-head-title');
    return t && t.innerText.includes('采集动态');});
  if(!card) return {found:false};
  const badge=card.querySelector('.ant-badge-count');
  const head=card.querySelector('.ant-card-head-title');
  const extra=card.querySelector('.ant-card-extra');
  const readBtn=extra && [...extra.querySelectorAll('button')]
      .find(b=>b.innerText.trim()==='已读');
  return {found:true,
    headText:head.innerText.replace(/\s+/g,' ').trim(),
    badgeText:badge?badge.innerText.trim():null,
    hasReadBtn:!!readBtn,
    rows:card.querySelectorAll('tbody tr').length};
})()""")


ws, send, ev, state = connect(new_tab())
send("Page.enable"); send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1500, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
for _ in range(30):
    time.sleep(1.5)
    if ev("(document.querySelectorAll('.ant-card').length||0)>2"):
        break
time.sleep(6)

print("=== ① 首屏（历史数据不该算未读）===")
d = probe(ev)
print(f"   找到卡片: {d['found']}")
print(f"   标题: {d['headText']}")
print(f"   角标: {d['badgeText']} {'✓ 首屏无红点' if not d['badgeText'] else '✗ 首屏就有未读'}")
print(f"   表格行数: {d['rows']}")

print("\n=== ② 触发一次采集，等前端轮询自动检测 ===")
# ⚠️ 不要用 location.reload()：那会重置 seenRef/initedRef，
# 第二次加载又被当成「首屏」，角标永远不出现（实测踩过）。
# 正确做法是原地等待 30 秒轮询自然触发。
try:
    op.open(urllib.request.Request("http://127.0.0.1:8000/api/collect/atlas",
                                   data=b"", method="POST"), timeout=150)
except Exception:
    pass  # 采集本身要 30s+，客户端先超时是正常的，后端仍在跑
print("   已触发 atlas 采集，等后端完成 + 轮询到期…")
d2 = None
for _ in range(28):          # 最多 ~84s（采集 30s + 轮询最多 30s）
    time.sleep(3)
    d2 = probe(ev)
    if d2.get("badgeText"):
        break
badge_seen = bool(d2.get("badgeText"))
print(f"   角标: {d2['badgeText']}")
print(f"   已读按钮: {'有' if d2['hasReadBtn'] else '无'}")
badge_seen = bool(d2['badgeText'])

print("\n=== ③ 点「已读」应清零角标但保留列表 ===")
before_rows = d2['rows']
ev("""(()=>{const cards=[...document.querySelectorAll('.ant-card')];
  const card=cards.find(c=>{const t=c.querySelector('.ant-card-head-title');
    return t && t.innerText.includes('采集动态');});
  const b=[...card.querySelectorAll('.ant-card-extra button')]
     .find(x=>x.innerText.trim()==='已读');
  if(b){b.click(); return true;} return false;})()""")
time.sleep(2)
d3 = probe(ev)
print(f"   角标: {d3['badgeText']} {'✓ 已清零/消失' if not d3['badgeText'] else '✗ 仍在'}")
print(f"   列表行数: {before_rows} → {d3['rows']} "
      f"{'✓ 列表保留' if d3['rows']>0 else '✗ 列表被清空'}")
print(f"   已读按钮: {'仍在' if d3['hasReadBtn'] else '已隐藏 ✓'}")

print("\n运行时异常:", len(state['errs']))
for t, d4 in state['errs'][:2]:
    print("  ", t, d4[:120])
ws.close()
