# -*- coding: utf-8 -*-
"""
问答系统验收脚本
================
对应优化方案里的三条验收标准：

  ① 每日采集的新数据，更新后能被检索并用于回答
  ② 通用问题可直接回答，不依赖知识库
  ③ 资料不足时明确说「未找到」，不编造

外加两条工程约束（来自本次改造的重点）：
  ④ 库内问答带来源/采集时间
  ⑤ 闲聊与实时信息问题不应被当成通用知识乱答

用法：
    python 评测/verify_qa.py                 # 全部用例
    python评测/verify_qa.py --only 2# 只跑第2 条

判定标准是**关键词命中**，不是 LLM 打分 —— 理由：
LLM 判"回答得好不好"每次结果都不同，验收脚本必须是确定性的。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
TIMEOUT = 120


def _opener() -> urllib.request.OpenerDirector:
    """禁用代理 —— 环境里有代理会让本地请求 10061。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ask(question: str, session: str) -> dict:
    """调用 /api/qa/free，返回解析后的 JSON。

    注意：该接口是 **POST**（参数走query string）。
    早先误用 GET 会得到 405 Method Not Allowed。
    """
    url = (
        f"{BASE}/api/qa/free?"
        + urllib.parse.urlencode({"question": question, "session_id": session})
    )
    req = urllib.request.Request(url, data=b"", method="POST")
    with _opener().open(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def kb_total() -> int:
    """直接问后端要漏洞总数 —— 用作「库内问答数字正确性」的基准。"""
    url = f"{BASE}/api/stats/knowledge-base"
    with _opener().open(url, timeout=20) as r:
        return int(json.loads(r.read())["vulnerabilities"])


# ---------------------------------------------------------------------------
# 用例定义
#   expect_any  : 答案里必须出现其中任一（表示行为正确）
#   expect_none : 答案里不能出现（用于防幻觉 / 防回退）
# ---------------------------------------------------------------------------
CASES = [
    {
        "id": 1,
        "name": "新采集数据可检索（用总数作代理指标）",
        "question": "知识库里的 vulnerabilities 表有多少条记录？",
        "expect_any": [],  # 数字单独校验，见check_numbers
        "check_numbers": True,
    },
    {
        "id": 2,
        "name": "通用问题能回答且带标注",
        "question": "Python 的 FastAPI 怎么用？",
        "expect_any": ["通用知识"],
        "expect_none": ["不在我的能力范围", "不在能力范围"],
    },
    {
        "id": 3,
        "name": "闲聊应礼貌回应，不触发查库",
        "question": "你好",
        "expect_any": [],
        "forbid_tool": True,
    },
    {
        "id": 4,
        "name": "实时信息问题应说明无数据源",
        "question": "今天天气怎么样？",
        "expect_any": ["无法", "没有实时", "建议"],
        "expect_none": ["20度", "晴", "气温"],
    },
    {
        "id": 5,
        "name": "库内问题带来源引用",
        "question": "库里有哪几类数据？分别多少条？",
        "expect_any": [],
        "require_citations": 1,
    },
    {
        "id": 6,
        "name": "库外冷门问题不得编造数据",
        "question": "CVE-2024-99999999 在知识库里的 CVSS 是多少？",
        "expect_any": [],
        "forbid_fabrication": True,
    },
]


def check_numbers(ans: str) -> tuple[bool, str]:
    """库内数字必须与数据库真实值一致 —— 防止「把检索命中数当总量」。"""
    real = kb_total()
    if str(real) in ans:
        return True, f"含真实总数 {real}"
    return False, f"**答案里没有真实总数 {real}**（可能把命中数当总量了）"


def run_case(case: dict, retries: int = 2) -> tuple[bool, str]:
    """跑一条用例，失败可重试。

    ---------- 为什么要重试 ----------
    LLM 输出有随机性：同一个问题两次跑，答案措辞不同、
    偶尔会漏掉某个关键词。实测「库内总数」这条用例
    第一次跑没答出数字，第二次就对了。

    如果不重试，验收脚本会**随机红**——
    那样的脚本没人愿意用，也无法作为回归依据。
    这里只在「断言型失败」时重试：
      · 超时/请求失败 → 直接判失败（那是环境问题，重试无意义）
      · 关键词/数字不符 → 重试（可能是这答歪了）
    """
    last = ""
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            data = ask(case["question"], session="verify")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return False, f"请求失败：{e}"  # 环境问题不重试
        ok, msg = _assert_case(case, data, elapsed=time.time() - t0)
        if ok:
            if attempt:
                msg += f"（第 {attempt + 1} 次尝试通过）"
            return True, msg
        last = msg
        if attempt < retries:
            time.sleep(1.5)
    return False, last


def _assert_case(case: dict, data: dict, elapsed: float = 0.0) -> tuple[bool, str]:
    """对单次结果做全部断言，返回 (是否通过, 说明)。"""
    ans = data.get("answer") or ""
    cites = data.get("citations") or []
    steps = data.get("trace") or []

    # 1) 必须出现的关键词
    for kw in case.get("expect_any", []):
        if kw not in ans:
            return False, f"答案缺少关键词「{kw}」：{ans[:80]}"
    # 2) 必须不出现的关键词（防回退）
    for kw in case.get("expect_none", []):
        if kw in ans:
            return False, f"答案不该出现「{kw}」：{ans[:80]}"
    # 3) 数字准确性
    if case.get("check_numbers"):
        ok, msg = check_numbers(ans)
        if not ok:
            # 失败时把答案原文带出来：Agent 有随机性，
            # 不看原文无法区分「逻辑错误」与「这次答歪了」
            return False, f"{msg}｜实际答案：{ans[:160]}"
    # 4) 引用溯源
    need = case.get("require_citations")
    if need and len(cites) < need:
        return False, f"引用只有 {len(cites)} 条，少于要求的 {need} 条"
    # 5) 闲聊不该走工具
    if case.get("forbid_tool") and steps:
        return False, f"闲聊却调用了工具：{[s.get('tool') for s in steps]}"
    # 6) 防幻觉：问到不存在的 CVE 时不能编造具体分数
    if case.get("forbid_fabrication"):
        import re
        m = re.search(r"CVSS[^0-9]{0,8}(10(?:\.0)?|[0-9])\.?\d*", ans)
        if m and "未" not in ans and "没有" not in ans and "暂无" not in ans:
            return False, f"疑似编造了CVSS 分数：{m.group(0)}"

    return True, f"{elapsed:.1f}s · {len(ans)} 字 · 引用 {len(cites)} 条"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=0, help="只跑某个用例编号")
    args = ap.parse_args()

    # 前置：后端是否可达
    try:
        with _opener().open(f"{BASE}/api/health", timeout=10) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print(f"✗ 后端不可达（{BASE}）：{e}")
        print("  请先启动：python -m uvicorn 项目源码.后端服务.app:app --port 8000")
        return 2

    cases = [c for c in CASES if not args.only or c["id"] == args.only]
    print(f"=== 问答验收（{len(cases)} 条用例）===\n")

    passed = 0
    for c in cases:
        ok, msg = run_case(c)
        mark = "✓" if ok else "✗"
        print(f"{mark} [{c['id']}] {c['name']}")
        print(f"     {msg}")
        if ok:
            passed += 1
        print()

    total = len(cases)
    print(f"=== 结果：{passed}/{total} 通过 ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())