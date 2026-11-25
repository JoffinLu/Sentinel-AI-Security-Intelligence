# -*- coding: utf-8 -*-
"""浏览器端到端验证：Agent 流式逐步增长 + Markdown 渲染

对应上次遗留的两项（后端 SSE 已确认 21 个 delta，但页面上没观察到增长）：

  ① 流式：答案文本长度随时间**多次增长**（不是一次性出现）
  ② Markdown：`##` / `**` / 表格标记被渲染成**真实 DOM 元素**，
     页面上不残留可见的原始标记

验证要点（踩过的坑）：
  · **不能用 reload 模拟「时间推移」** —— reload 会重置 React 状态，
    之前 verify_feed_badge 就因此误判过一次。要就地连续采样。
  · 量「文本长度」要量innerText 的实际渲染结果，
    不要只看 state —— 用户看到的是 DOM。
  · Markdown 是否渲染：查真实元素（table/strong/h2）的数量，
    而不是查有没有某个 class。
  · `**` 加粗渲染成 <strong> 后innerText 里就**没有星号**了，
    所以 rawMarks 只查行首 `##`（标题标记）—— 加粗靠 strongs 判定。
"""
import base64
import json
import time
import urllib.request

import websocket

PORT = 9361
PAGE = "http://127.0.0.1:5173/qa"
OUT = ".tmp/qa_stream.png"
QUESTION = (
    "请详细介绍知识库的情况：有哪些数据表、各表有多少条数据、"
    "最近一次采集是什么时候、数据来源有哪些"
)

urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))


def new_tab():
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?{PAGE}", method="PUT")
    return json.loads(urllib.request.urlopen(req).read())


def close_tab(tab_id):
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/json/close/{tab_id}").read()
    except Exception:
        pass


def connect(tab):
    ws = websocket.create_connection(
        tab["webSocketDebuggerUrl"], timeout=300)
    state = {"seq": 0, "errs": []}

    def send(m, p=None):
        state["seq"] += 1
        mid = state["seq"]
        ws.send(json.dumps(
            {"id": mid, "method": m, "params": p or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("method") == "Runtime.exceptionThrown":
                d = r["params"]["exceptionDetails"]
                state["errs"].append((
                    d.get("text", ""),
                    (d.get("exception") or {}).get("description", "")[:200],
                ))
            if r.get("id") == mid:
                return r.get("result", {})

    def ev(e):
        return send("Runtime.evaluate",
                    {"expression": e, "returnByValue": True}) \
            .get("result", {}).get("value")

    return ws, send, ev, state


# 读**最后一个**助手气泡的渲染文本 + Markdown DOM 结构
# ⚠️ 必须按「最后一个 .sentinel-qa-md」取，且验证前要确保会话是空的——
# 页面会加载历史会话，若抓到**历史那条**气泡，
# 它在提问前就已经渲染完成 → 采样全程长度不变 → 误判「没流式」。
# （实测就踩过：答案内容与当次请求完全对不上，才意识到抓错了气泡。）
PROBE = r"""(()=>{
  const md=[...document.querySelectorAll('.sentinel-qa-md')];
  if(!md.length) return {found:false, count:0};
  const last=md[md.length-1];
  return {
    found:true,
    count: md.length,
    textLen: last.innerText.replace(/\s+/g,' ').trim().length,
    text: last.innerText.replace(/\s+/g,' ').trim().slice(0,400),
    tables: last.querySelectorAll('table').length,
    strongs: last.querySelectorAll('strong').length,
    h2: last.querySelectorAll('h2').length,
    h3: last.querySelectorAll('h3').length,
    lis: last.querySelectorAll('li').length,
    tableWrap: last.querySelectorAll('.sentinel-qa-md-tablewrap').length,
    rawMarks: (last.innerText.match(/^\s*#{1,6}\s/gm)||[]).length,
    spinning: document.querySelectorAll('.ant-spin').length,
  };
})()"""


def main():
    tab = new_tab()
    ws, send, ev, state = connect(tab)
    try:
        send("Page.enable")
        send("Runtime.enable")
        send("Emulation.setDeviceMetricsOverride", {
            "width": 1500, "height": 1200,
            "deviceScaleFactor": 1, "mobile": False})

        for _ in range(40):
            time.sleep(1.5)
            if ev("!!document.querySelector('textarea')"):
                break
        print("[准备] 页面已挂载")
        time.sleep(3)

        # ---------- 清空历史会话 ----------
        # 页面会加载历史会话，历史气泡本来就已渲染完成。
        # 不清空就会抓到历史那条 → 采样全程长度不变 → 误判「没流式」。
        ev("""(()=>{
          const btn=[...document.querySelectorAll('button')]
            .find(b=>b.innerText.trim()==='清空');
          if(btn){btn.click(); return true;}
          return false;
        })()""")
        time.sleep(1.5)
        # 可能有「确认」弹窗
        ev("""(()=>{
          const ok=[...document.querySelectorAll('.ant-btn-primary')]
            .find(b=>/确定|确认|清空/.test(b.innerText));
          if(ok) ok.click();
          return true;
        })()""")
        time.sleep(1.5)
        pre = ev(PROBE) or {}
        print(f"[清空] 现有 Markdown 气泡数 = {pre.get('count')}"
              f"  长度 = {pre.get('textLen')}")
        if pre.get("count", 0) > 0:
            print("  ⚠️ 仍有历史气泡，采样起点不为空，结果可能失真")
        else:
            print("  ✓ 起点为空，可准确观察本次增长")

        # ---------- 输入并发送 ----------
        print(f"[发送] {QUESTION}")
        ev("""(()=>{
          const ta=document.querySelector('textarea');
          const setter=Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype,'value').set;
          setter.call(ta, %s);
          ta.dispatchEvent(new Event('input',{bubbles:true}));
          return true;
        })()""" % json.dumps(QUESTION))
        time.sleep(0.6)
        clicked = ev("""(()=>{
          const btn=[...document.querySelectorAll('button')]
            .find(b=>b.innerText.trim()==='发送');
          if(btn){btn.click(); return true;}
          return false;
        })()""")
        print(f"[发送] 按钮点击={clicked}")

        # ---------- ① 流式：连续采样文本长度 ----------
        # ⚠️ 采样间隔必须 **远小于**流式持续时间。
        # 实测后端 127 个 delta 分布在 1.65 秒内 ——
        # 用 1 秒间隔采样只会拿到 2 个点，误判成「没流式」。
        # 这是第二次被采样频率坑到（第一次是 1 秒粒度看不出 3.7s 的过程）。
        print("\n=== ① 流式：文本长度随时间增长（每 200ms 采样）===")
        print("   t(s)   长度   增量表/粗预览")
        samples = []
        t0 = time.time()
        prev = 0
        grew = 0
        shrank = 0
        stable = 0
        while time.time() - t0 < 150:
            time.sleep(0.2)
            d = ev(PROBE)
            el = time.time() - t0
            if not d or not d.get("found"):
                print(f"  {el:5.1f}   (尚未出现答案气泡)")
                continue
            cur = d["textLen"]
            delta = cur - prev
            if delta > 0:
                grew += 1
                stable = 0
            elif delta < 0:
                # 负增长 = reset 撤回中间轮过渡语，是预期行为
                shrank += 1
                stable = 0
            else:
                stable += 1
            samples.append((round(el, 1), cur, delta))
            mark = "" if delta >= 0 else "  ←reset 撤回"
            print(f"  {el:5.1f}  {cur:5d}  {delta:+5d}   "
                  f"{d['tables']}/{d['strongs']}   {d['text'][:30]}{mark}")
            prev = cur
            # 有内容后连续 12 次无变化（2.4s）→ 判定流式结束
            if cur > 0 and stable >= 12:
                print("  → 内容稳定，判定流式结束")
                break

        final = ev(PROBE) or {}
        distinct = len({s[1] for s in samples})
        final_len = samples[-1][1] if samples else 0
        print(f"\n  采样 {len(samples)} 次 · 长度取值 {distinct} 种 · "
              f"增长 {grew} 次 · 撤回 {shrank} 次 · 最终长度 {final_len}")

        print("\n=== ② Markdown 渲染结构 ===")
        for k in ("tables", "strongs", "h2", "h3", "lis", "tableWrap"):
            print(f"  {k:11s}: {final.get(k)}")
        print(f"  行首 ## 残留  : {final.get('rawMarks')}")
        print(f"  旋转图标      : {final.get('spinning')}")
        print("\n  --- 页面渲染文本（前 400 字）---")
        print("  " + (final.get("text") or "")[:400])

        print("\n=== ③ 运行时异常 ===")
        if state["errs"]:
            for t, d in state["errs"]:
                print(f"  [异常] {t} {d}")
        else:
            print("  无 ✓")

        png = send("Page.captureScreenshot", {"format": "png"}).get("data", "")
        if png:
            with open(OUT, "wb") as f:
                f.write(base64.b64decode(png))
            print(f"\n[截图] {OUT}")

        # 流式判定：长度取值足够多样 = 文本是逐步长出来的，
        # 而不是一次性出现。
        # 阈值按实测校准：200ms 采样下，一段 204 字、生成耗时约 1.3 秒的
        # 答案能取到 7 种长度（14→57→83→117→143→191→204）。
        # 低于 4 种基本就是"一次性出现"（对照：修复前是 1 种）。
        ok_stream = distinct >= 4 and grew >= 3
        # Markdown 判定：真实 DOM 元素存在，且无可见的原始标记。
        # 表格这次可能没生成（模型选择用列表回答），
        # 所以只要 strong/li 等任一结构化元素存在即可。
        struct = ((final.get("tables") or 0) + (final.get("strongs") or 0)
                  + (final.get("lis") or 0))
        ok_md = struct > 0 and (final.get("rawMarks") or 0) == 0
        print("\n" + "=" * 48)
        print(f"流式（逐步增长）: {'通过' if ok_stream else '未通过'}"
              f"  长度种类={distinct} 增长={grew} 撤回={shrank}")
        print(f"Markdown 渲染 : {'通过' if ok_md else '未通过'}"
              f"  表格={final.get('tables')} 加粗={final.get('strongs')}"
              f" 列表={final.get('lis')} 原始标记={final.get('rawMarks')}")
        print(f"运行时异常    : {len(state['errs'])}")
    finally:
        ws.close()
        close_tab(tab["id"])


if __name__ == "__main__":
    main()
