"""
数据质量修复：严重度归一化

背景
----
`ingest_pipeline` 已在入口统一严重度写法，但历史数据里仍留有脏值：
`Critical` 39 条与 `CRITICAL` 607 条并存，`High` 73 与 `HIGH` 2273 并存。
后果是前端筛选漏数据、统计图表被拆成两半、阈值判断失效。

本脚本做一次性清洗，把存量数据对齐到新口径。
新增采集的数据由管道自动保证，无需再跑。
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from 项目源码.知识库.database import close_database, get_session, init_database  # noqa: E402
from 项目源码.知识库.models import Vulnerability  # noqa: E402
from 项目源码.数据采集.ingest_pipeline import _normalize_severity  # noqa: E402


async def main() -> None:
    await init_database()

    async with get_session() as session:
        rows = (await session.execute(select(Vulnerability))).scalars().all()

    before = Counter(r.severity for r in rows if r.severity)
    print(f"清洗前（{len(rows)} 条漏洞的严重度分布）：")
    for k, v in before.most_common():
        flag = "  ← 需归一" if k != k.upper() or k == "MODERATE" else ""
        print(f"    {k:<14} {v:>5}{flag}")

    changed = 0
    async with get_session() as session:
        for r in (await session.execute(select(Vulnerability))).scalars().all():
            new = _normalize_severity(r.severity)
            if new != r.severity:
                r.severity = new
                changed += 1
        await session.commit()

    async with get_session() as session:
        rows2 = (await session.execute(select(Vulnerability))).scalars().all()
    after = Counter(r.severity for r in rows2 if r.severity)

    print(f"\n已修正 {changed} 条")
    print("\n清洗后：")
    for k, v in after.most_common():
        print(f"    {k:<14} {v:>5}")

    print("\n" + "=" * 54)
    ok = not any(k != k.upper() for k in after)
    print("归一化结果：" + ("通过，无残留非大写档位" if ok else "仍有脏值"))
    print("=" * 54)

    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
