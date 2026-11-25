"""问答评测（限流安全版）：复用官方评测逻辑，仅替换执行节奏。

为什么需要：免费网关约 10 次/分钟即触发 429（冷却 41s+），官方并发跑法
会让绝大多数题目落入降级模板，测不出 LLM 真实答案质量。

改动仅两处（均不影响官方判定逻辑与单题计时）：
1. 题间节流：每题完成后在 run_case 计时之外 sleep PACE 秒
2. 熔断等待：题目开始前若 LLM 处于冷却期，等冷却结束再执行（而非降级）

用法：
  $env:PYTHONPATH="."
  ./.venv/Scripts/python.exe "归档/技术报告/评测脚本/问答评测_限流安全.py"
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
EVAL_SCRIPT = HERE / "问答评测.py"
PACE_SECONDS = 9.0
REPORT = "归档/技术报告/数据/问答评测报告_全LLM.md"


def load_eval_module():
    spec = importlib.util.spec_from_file_location("qa_eval_official", EVAL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qa_eval_official"] = mod
    spec.loader.exec_module(mod)
    return mod


async def paced_run_all(self, cases, concurrency: int = 1):
    """顺序执行 + 题间节流 + 熔断等待。计时逻辑复用官方 run_case。"""
    mod = sys.modules["qa_eval_official"]
    results: list = []
    total = len(cases)
    t_start = time.perf_counter()

    for i, case in enumerate(cases, 1):
        llm = getattr(self.agent, "llm", None)
        if llm is not None:
            loop = asyncio.get_running_loop()
            wait_rounds = 0
            while llm.is_rate_limited():
                remain = llm._rate_limited_until - loop.time()
                print(f"    [cooldown] LLM 冷却中，等待 {remain:.0f}s ...")
                await asyncio.sleep(min(remain, 60) + 1.0)
                wait_rounds += 1
                if wait_rounds > 30:
                    break

        r = await self.run_case(case, "eval")
        results.append(r)

        done = i
        passed = sum(1 for x in results if x.passed)
        degraded = sum(1 for x in results if x.degraded)
        if i % 10 == 0 or i == total:
            eta = (time.perf_counter() - t_start) / i * (total - i)
            print(
                f"  进度 {i}/{total}  通过 {passed}  降级 {degraded}  "
                f"ETA {eta/60:.0f}min",
                flush=True,
            )
        mod.logger.info(
            "eval_progress", done=done, passed=passed, total=total,
        )
        if i < total:
            await asyncio.sleep(PACE_SECONDS)

    return results


def main() -> None:
    mod = load_eval_module()
    mod.QAEvaluator.run_all = paced_run_all
    sys.argv = [
        str(EVAL_SCRIPT),
        "--concurrency", "1",
        "--output", REPORT,
    ]
    asyncio.run(mod.main())


if __name__ == "__main__":
    main()
