# -*- coding: utf-8 -*-
"""验证顶栏 LLM 标签能打开设置弹窗"""
import json, time, urllib.request, websocket

PORT = 9356
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(op)
req = urllib.request.Request(
    f"http://127.0.0.1:{PORT}/json/new?http://127.0.0.1:5173/dashboard", method="PUT")
tab = json.loads(urllib.request.urlopen(req).read())
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=90)
seq = [0]; errs = []


def send(m, p=None):
    seq[0] += 1
    mid = seq[0]
    ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get('method') == 'Runtime.exceptionThrown':
            d = r['params']['exceptionDetails']
            errs.append((d.get('text', ''), (d.get('exception') or {}).get('description', '')[:160]))
        if r.get("id") == mid:
            return r.get("result", {})


def ev(e):
    return send("Runtime.evaluate", {"expression": e, "returnByValue": True}) \
        .get("result", {}).get("value")


send("Page.enable"); send("Runtime.enable")
send("Emulation.setDeviceMetricsOverride",
     {"width": 1500, "height": 950, "deviceScaleFactor": 1, "mobile": False})
for _ in range(30):
    time.sleep(1.5)
    if ev("(document.body.innerText||'').includes('LLM')"):
        break
time.sleep(3)

# 必须限定在顶栏 .sentinel-header 内 —— 否则会匹配到采集动态里
# 论文标题里的「LLM ...」字样（实测踩过：匹配到 y=3036 的论文条目）
r = ev("""(()=>{
 const hdr=document.querySelector('.sentinel-header-right');
 if(!hdr) return {found:false, why:'no-header'};
 const e=[...hdr.querySelectorAll('button,span')].find(x=>{
   const t=(x.innerText||'').trim();
   return t.startsWith('LLM ') && t.length<40;});
 if(!e) return {found:false, why:'no-llm-tag'};
 const rect=e.getBoundingClientRect();
 return {found:true, text:e.innerText.trim(), title:e.getAttribute('title'),
   y:Math.round(rect.y), inView: rect.y>=0 && rect.y<=window.innerHeight,
   x:Math.round(rect.x+rect.width/2), cy:Math.round(rect.y+rect.height/2)};})()""")
print("LLM 标签:", r)
if r.get("found"):
    send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": r["x"], "y": r["cy"],
                                      "button": "left", "clickCount": 1})
    send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": r["x"], "y": r["cy"],
                                      "button": "left", "clickCount": 1})
    time.sleep(3)
    d = ev("""(()=>{const m=document.querySelector('.ant-modal-content');
     if(!m) return {shown:false};
     return {shown:true, title:m.querySelector('.ant-modal-title')?.innerText,
       inputs:[...m.querySelectorAll('input')].map(i=>i.type+'|'+(i.placeholder||'').slice(0,24)),
       tags:[...m.querySelectorAll('.ant-tag')].map(t=>t.innerText.trim()).slice(0,8),
       buttons:[...m.querySelectorAll('.ant-modal-footer button')].map(b=>b.innerText.trim()),
       body:m.innerText.replace(/\\s+/g,' ').slice(0,300)};})()""")
    print("\n弹窗显示:", d.get("shown"), "|", d.get("title"))
    print("  输入框:", d.get("inputs"))
    print("  预设:", d.get("tags"))
    print("  按钮:", d.get("buttons"))
    print("  内容:", (d.get("body") or "")[:240])
print("\n异常数:", len(errs))
for t, dd in errs[:2]:
    print("  ", t, dd[:140])
ws.close()
