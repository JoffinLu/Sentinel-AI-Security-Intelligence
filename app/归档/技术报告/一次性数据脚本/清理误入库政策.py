"""
清理误入漏洞表的政策条目

背景
----
`policy_collector` 早期版本同时打 `is_policy` + `is_news` 标记，
而 ingest_pipeline 按 `is_news` 分流，导致政策数据被当作社区资讯
写进 `vulnerabilities` 表。

后果不只是"多了一堆脏数据"，而是双向污染：
  ① 漏洞库掺入非漏洞条目，统计口径失真
  ② 问答检索会召回「Unsloth Studio Flaw」「Carbonato Botnet」这类
     安全新闻来回答漏洞类问题

这些条目的origin_source 属于政策源列表（nist / darkreading_policy /
schneier），可用它精确识别。federal_register 是法规源，
其数据本来就走独立分流，不会误入，故不列入。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from 项目源码.知识库.database import close_database, get_session, init_database  # noqa: E402
from 项目源码.知识库.models import Vulnerability  # noqa: E402
from 项目源码.数据采集.policy_collector import POLICY_SOURCES  # noqa: E402


async def main() -> None:
    await init_database()

    # 只取新闻/观点类源；federal_register 不在其中
    codes = [s["code"] for s in POLICY_SOURCES if s["code"] != "federal_register"]
    print(f"待检查的政策源 origin_source：{codes}")

    async with get_session() as session:
        before = await session.scalar(select(func.count()).select_from(Vulnerability))
        victims = (
            await session.execute(
                select(Vulnerability).where(Vulnerability.origin_source.in_(codes))
            )
        ).scalars().all()

        print(f"\n漏洞表总数：{before}")
        print(f"待清理的政策条目：{len(victims)}")
        for v in victims[:5]:
            print(f"  · [{v.origin_source}] {(v.title or '')[:60]}")
        if len(victims) > 5:
            print(f"  ... 另有 {len(victims) - 5} 条")

        if victims:
            for v in victims:
                await session.delete(v)
            await session.commit()

        after = await session.scalar(select(func.count()).select_from(Vulnerability))
        print(f"\n清理后漏洞表总数：{after}（减少 {before - after}）")

    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
