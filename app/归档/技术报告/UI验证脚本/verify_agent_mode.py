# -*- coding: utf-8 -*-
"""验证 Agent 模式：任意问题都能答，且数量/时间类问题会主动调工具"""
import json, time, urllib.request, websocket

PORT = 9357
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(op)
req = urllib.request.Request(
    f"http://127.0.0.1:{PORT}/json/new?http://127.0.0.1:5173/qa", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=180)
seq = [0]; errs = []


def send(m, p=None):
    seq[0] += 1
    mid = seq[0]
    ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get('method') == 'Runtime.exceptionThrown':
            d = r['params']['exceptionDetails']
            errs.append((d.get('text', ''), (d.get('exception') or {}).get('description', '')[:150]))
        if r.get("id") == mid:
            return r.get("result", {})


def ev(e):
    return send("Runtime.evaluate", {"expression": e, "returnByValue": True}) \
        .get("result", {}).get("value")


send("Page.enable"); send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1500, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
for _ in range(30):
    time.sleep(1.5)
    if ev("(document.querySelectorAll('textarea').length||0)>0"):
        break
time.sleep(3)


def ask(q, wait=60):
    ev("""(()=>{const ta=document.querySelector('textarea');
     const s=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
     s.call(ta, arguments[0]); ta.dispatchEvent(new Event('input',{bubbles:true}));})()""".replace("arguments[0]", repr(q).replace("'", '"')))
    time.sleep(0.6)
    ev("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('发送'));
     if(b)b.click();})()""")
    for _ in range(wait // 2):
        time.sleep(2)
        n = ev("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('发送'));
         return b && b.disabled;})()""")
        if n is False:
            break
    return ev("""(()=>{const m=document.querySelectorAll('.sentinel-qa-msg-row');
     const last=m[m.length-1];return last?last.innerText.replace(/\\s+/g,' ').trim():'';})()""") or ""


CASES = [
    ("知识库一共有多少条数据？", "应调count_records"),
    ("最近采集到的一条数据是什么时候的？", "应调 get_latest_collect"),
    ("Python 装饰器怎么用？", "不应调工具"),
]
print("=== Agent 模式端到端验证 ===\n")
for q, expect in CASES:
    r = ask(q)
    tools = "有工具调用" if "查询了知识库" in r or "工具调用" in r else "无工具调用"
    ok = len(r) > 20
    print(f"{'✓' if ok else '✗'} 问：{q}")
    print(f"   期望：{expect}")
    print(f"   实测：{tools} · {len(r)} 字")
    print(f"   答  ：{r[:150]}")
    print()

print("运行时异常数:", len(errs))
for t, d in errs[:2]:
    print("  ", t, d[:130])
ws.close()
