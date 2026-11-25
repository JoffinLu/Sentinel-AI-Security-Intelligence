# -*- coding: utf-8 -*-
"""验证三处UI 改动（浏览器端到端，量computedStyle 实际渲染值）

  ① 「统计更新于…」状态条：字号/颜色/位置是否改善可读性
  ② 严重度图例：标签是否在竖线**左侧**，说明项在右侧
  ③ 采集动态：铃铛 + 右上角未读数字，点击后归零，右侧无「已读」

验证手法要点（沿用既有踩坑经验）：
  · 量computedStyle 的**实际值**，不要只看 class 是否存在
  · 判断「在竖线左边」要量 getBoundingClientRect().right 的**数值大小**，
    不能只看 DOM 顺序（DOM 顺序对不代表视觉位置对）
"""
import base64
import json
import time
import urllib.request

import websocket

PORT = 9361
PAGE = "http://127.0.0.1:5173/dashboard"
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))


def new_tab():
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?{PAGE}", method="PUT")
    return json.loads(urllib.request.urlopen(req).read())


def connect(tab):
    ws = websocket.create_connection(
        tab["webSocketDebuggerUrl"], timeout=120)
    state = {"seq": 0, "errs": []}

    def send(m, p=None):
        state["seq"] += 1
        mid = state["seq"]
        ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("method") == "Runtime.exceptionThrown":
                d = r["params"]["exceptionDetails"]
                state["errs"].append((
                    d.get("text", ""),
                    (d.get("exception") or {}).get("description", "")[:200]))
            if r.get("id") == mid:
                return r.get("result", {})

    def ev(e):
        return send("Runtime.evaluate",
                    {"expression": e, "returnByValue": True}) \
            .get("result", {}).get("value")

    return ws, send, ev, state


# ① 刷新状态条
PROBE_BAR = r"""(()=>{
  const el=document.querySelector('.sentinel-updated-bar');
  if(!el) return {found:false};
  const cs=getComputedStyle(el);
  const r=el.getBoundingClientRect();
  return {found:true, text:el.innerText.replace(/\s+/g,' ').trim(),
    fontSize:cs.fontSize, color:cs.color, left:Math.round(r.left),
    top:Math.round(r.top), width:Math.round(r.width)};
})()"""

# ② 严重度图例：量标签与说明区的实际横向位置
PROBE_LEGEND = r"""(()=>{
  const lg=document.querySelector('.sentinel-feed-legend');
  if(!lg) return {found:false};
  const label=lg.querySelector('.sentinel-feed-legend-label');
  const items=lg.querySelector('.sentinel-feed-legend-items');
  if(!label||!items) return {found:false,label:!!label,items:!!items};
  const rl=label.getBoundingClientRect();
  const ri=items.getBoundingClientRect();
  const cs=getComputedStyle(label);
  return {found:true,
    labelText:label.innerText.trim(),
    labelRight:Math.round(rl.right), labelLeft:Math.round(rl.left),
    itemsLeft:Math.round(ri.left),
    // 关键判定：标签右边界 <= 说明区左边界 → 标签在竖线左侧
    labelBeforeItems: rl.right <= ri.left + 1,
    labelBorderRight: cs.borderRightWidth + ' ' + cs.borderRightColor,
    itemCount: items.children.length,
    itemTexts: [...items.children].map(c=>c.innerText.replace(/\s+/g,' ').trim()),
    legendText: lg.innerText.replace(/\s+/g,' ').trim()};
})()"""

# ③ 铃铛 + 角标 + 是否还有「已读」
PROBE_BELL = r"""(()=>{
  const cards=[...document.querySelectorAll('.ant-card')];
  const card=cards.find(c=>{const t=c.querySelector('.ant-card-head-title');
    return t && t.innerText.includes('采集动态');});
  if(!card) return {found:false};
  const head=card.querySelector('.ant-card-head-title');
  const bell=head.querySelector('.anticon-bell');
  const badge=head.querySelector('.ant-badge');
  const count=head.querySelector('.ant-badge-count');
  const extra=card.querySelector('.ant-card-extra');
  const readBtn=extra?[...extra.querySelectorAll('button')]
      .find(b=>b.innerText.trim()==='已读'):null;
  return {found:true,
    hasBell:!!bell,
    bellColor: bell?getComputedStyle(bell).color:null,
    bellSize: bell?getComputedStyle(bell).fontSize:null,
    hasBadge:!!badge,
    badgeCount: count?count.innerText.trim():null,
    // 角标是否在铃铛右上角：量的 top 更小、left 更靠右
    badgeTopRight: (()=>{
      if(!count||!bell) return null;
      const rc=count.getBoundingClientRect(), rb=bell.getBoundingClientRect();
      return {aboveTop: rc.top <= rb.top + 4, rightAligned: rc.right >= rb.right - 4};
    })(),
    hasReadBtn: !!readBtn,
    headText: head.innerText.replace(/\s+/g,' ').trim()};
})()"""


def main():
    tab = new_tab()
    ws, send, ev, state = connect(tab)
    try:
        send("Page.enable")
        send("Runtime.enable")
        send("Emulation.setDeviceMetricsOverride", {
            "width": 1500, "height": 1400,
            "deviceScaleFactor": 1, "mobile": False})
        for _ in range(40):
            time.sleep(1.5)
            if ev("(document.querySelectorAll('.ant-card').length||0)>2"):
                break
        time.sleep(8)

        print("=== ① 刷新状态条（可读性）===")
        b = ev(PROBE_BAR)
        if not b or not b.get("found"):
            print("  ✗ 未找到 .sentinel-updated-bar")
        else:
            print(f"  文案: {b['text']}")
            print(f"  字号: {b['fontSize']}   颜色: {b['color']}")
            print(f"  位置: left={b['left']} top={b['top']} 宽={b['width']}")
            # 灰字在深色底上的可读性：字号 >= 12 且不是最暗的灰
            fs = float(b['fontSize'].replace('px', ''))
            print(f"  → 字号 {fs}px {'✓ 已放大' if fs >= 12 else '✗ 仍偏小'}"
                  f"（原 11.5px）")
            print(f"  → 左对齐 left={b['left']}px"
                  f" {'✓' if b['left'] > 20 else '✗ 仍贴边'}")

        print("\n=== ② 严重度图例（标签在竖线左侧）===")
        lg = ev(PROBE_LEGEND)
        if not lg or not lg.get("found"):
            print(f"  ✗ 未找到图例 {lg}")
        else:
            print(f"  标签: {lg['labelText']!r}  竖线: {lg['labelBorderRight']}")
            print(f"  标签右边界={lg['labelRight']}  说明区左边界={lg['itemsLeft']}")
            print(f"  → 标签在说明区左侧: "
                  f"{'✓' if lg['labelBeforeItems'] else '✗'}")
            print(f"  说明项数: {lg['itemCount']}  内容: {lg['itemTexts']}")
            print(f"  整行: {lg['legendText']}")

        print("\n=== ③ 铃铛 + 未读角标 ===")
        bl = ev(PROBE_BELL)
        if not bl or not bl.get("found"):
            print("  ✗ 未找到采集动态卡片")
        else:
            print(f"  标题区: {bl['headText']!r}")
            print(f"  铃铛: {'✓ 有' if bl['hasBell'] else '✗ 无'}"
                  f"  颜色={bl['bellColor']} 字号={bl['bellSize']}")
            print(f"  角标: {'✓ 有' if bl['hasBadge'] else '— 无（未读为0时隐藏）'}"
                  f"  数字={bl['badgeCount']}")
            if bl['badgeTopRight']:
                print(f"  角标位置: 在铃铛右上角="
                      f"{'✓' if (bl['badgeTopRight']['aboveTop'] and bl['badgeTopRight']['rightAligned']) else '✗'}")
            print(f"  右侧「已读」按钮: "
                  f"{'✗ 仍存在（应去掉）' if bl['hasReadBtn'] else '✓ 已去掉'}")

        # ---- 点击铃铛：模拟有未读时的归零行为 ----
        print("\n=== ④ 点击铃铛 → 未读归零 ===")
        # 先注入一个未读数，模拟有新采集
        ev("""(()=>{
      const head=[...document.querySelectorAll('.ant-card-head-title')]
        .find(t=>t.innerText.includes('采集动态'));
      if(!head) return false;
      const icon=head.querySelector('.anticon-bell');
      if(!icon) return false;
      // 强制渲染一个角标（模拟 unread>0 的视觉形态）
      let b=head.querySelector('.ant-badge');
      if(!b){
        b=document.createElement('span');
        b.className='ant-badge';
        b.innerHTML='<span class="ant-badge-count">7</span>';
        b.style.cssText='position:relative;display:inline-block;margin-left:4px';
        icon.parentNode.insertBefore(b, icon.nextSibling);
      }
      return true;
    })()""")
        time.sleep(1.0)
        inj = ev(PROBE_BELL)
        print(f"  注入后角标数字: {inj.get('badgeCount')}")
        # 真实点击（读注入的角标状态前先点图标）
        clicked = ev("""(()=>{
      const head=[...document.querySelectorAll('.ant-card-head-title')]
        .find(t=>t.innerText.includes('采集动态'));
      const icon=head&&head.querySelector('.anticon-bell');
      if(!icon) return false;
      icon.click();
      return true;
    })()""")
        time.sleep(1.2)
        after = ev(PROBE_BELL)
        print(f"  点击返回: {clicked}")
        print(f"  点击后角标数字: {after.get('badgeCount')}"
              f"  ({'✓ 已归零' if not after.get('badgeCount') else '✗ 未归零'})")
        print(f"  点击后铃铛颜色: {after.get('bellColor')}（应为 muted 灰）")

        print("\n=== ⑤ 运行时异常 ===")
        print("  无 ✓" if not state["errs"] else state["errs"])

        png = send("Page.captureScreenshot", {"format": "png"}).get("data", "")
        if png:
            with open(".tmp/ui3.png", "wb") as f:
                f.write(base64.b64decode(png))
            print("\n[截图] .tmp/ui3.png")

        # 汇总
        ok1 = bool(b and b.get("found")) and float(
            b['fontSize'].replace('px', '')) >= 12
        ok2 = bool(lg and lg.get("labelBeforeItems"))
        ok3 = bool(bl and bl["hasBell"] and not bl["hasReadBtn"])
        print("\n" + "=" * 46)
        print(f"① 状态条可读性 : {'通过' if ok1 else '未通过'}")
        print(f"② 图例标签在左 : {'通过' if ok2 else '未通过'}")
        print(f"③ 铃铛替代已读 : {'通过' if ok3 else '未通过'}")
    finally:
        ws.close()
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/json/close/{tab['id']}").read()
        except Exception:
            pass


if __name__ == "__main__":
    main()
