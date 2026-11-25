"""
向量化回填脚本

为数据库中尚未生成向量的情报条目计算嵌入。

设计要点：
1. 增量处理——只处理 embedding 为 NULL 的记录，避免重复计算
2. 分批提交——每批一个事务，避免长事务锁表
3. 可断点续跑——中途失败后重跑会跳过已完成的记录
4. 文本拼接策略——把标题、描述、富化要点拼成一段文本，
   让检索能同时命中不同字段的内容

用法：
    ./.venv/Scripts/python.exe -m 问答模块.vectorize --batch 200
    ./.venv/Scripts/python.exe -m 问答模块.vectorize --stats
"""

from __future__ import annotations

import argparse
import asyncio
import time

from sqlalchemy import func, select, update

from ..后端服务.logger import configure_logging, get_logger
from ..知识库.database import close_database, get_session, init_database
from ..知识库.models import Paper, Vulnerability
from .embedding import get_embedding_service

configure_logging()
logger = get_logger("vectorize")


def build_vuln_text(v: Vulnerability) -> str:
    """
    拼接漏洞的检索文本

    把分散在各字段的信息合成一段自然文本，比只搜标题的召回率高得多。
    富化字段（分类、POC、攻击技）一并纳入，让语义检索能命中
    "哪些漏洞有可用 POC" 这类组合查询。
    """
    parts: list[str] = [v.title or ""]

    if v.description:
        parts.append(v.description[:600])

    if v.ai_category:
        parts.append(f"AI安全分类: {v.ai_category}")
    sub = (v.extra or {}).get("ai_subcategory")
    if sub:
        parts.append(f"细类: {sub}")

    if v.severity:
        parts.append(f"严重程度: {v.severity}")

    # 受影响组件——用户常问"transformers 有哪些漏洞"
    for asset in (v.affected_assets or [])[:3]:
        product = asset.get("product")
        if product:
            eco = asset.get("ecosystem")
            parts.append(f"受影响组件: {product}" + (f" ({eco})" if eco else ""))

    # 攻击技术——支撑"针对某技术的漏洞有哪些"
    for tech in (v.technique_refs or [])[:2]:
        name = tech.get("name")
        tid = tech.get("atlas_id")
        if name:
            parts.append(f"攻击技术: {tid} {name}")

    # POC 可用性——支撑"哪些漏洞已有 POC"
    for poc in (v.poc_refs or [])[:2]:
        name = poc.get("name")
        if name:
            parts.append(f"关联POC: {name}")

    # 修复信息——支撑"怎么修"的检索
    fix = v.fix_info or {}
    if fix.get("patch_version"):
        parts.append(f"修复版本: {fix['patch_version']}")

    return "\n".join(p for p in parts if p)[:1500]


def build_paper_text(p: Paper) -> str:
    """拼接论文的检索文本"""
    parts = [p.title or ""]
    if p.abstract:
        parts.append(p.abstract[:800])
    if p.ai_topics:
        parts.append("主题: " + ", ".join(p.ai_topics[:5]))
    if p.categories:
        parts.append("分类: " + ", ".join(p.categories[:3]))
    authors = p.authors or []
    if authors:
        parts.append("作者: " + ", ".join(authors[:3]))
    return "\n".join(x for x in parts if x)[:1500]


def build_policy_text(p) -> str:
    """
    拼接政策法规的检索文本

    政策文本的检索特点：用户往往按「法规名/机构/主题词」提问，
    所以标题、机构、地区、法规编号都要进检索文本——
    只放摘要的话，问「欧盟 AI 法案有哪些要求」会命中不到。
    """
    parts = [p.title or ""]
    if p.summary:
        parts.append(p.summary[:800])
    meta: list[str] = []
    if p.policy_kind:
        meta.append(f"类型: {p.policy_kind}")
    if p.org:
        meta.append(f"发布机构: {p.org}")
    if p.region:
        meta.append(f"适用地区: {p.region}")
    if meta:
        parts.append(" ".join(meta))
    numbers = p.doc_numbers or []
    if numbers:
        parts.append("法规编号: " + ", ".join(numbers[:3]))
    keywords = p.ai_keywords or []
    if keywords:
        parts.append("主题: " + ", ".join(keywords[:5]))
    return "\n".join(x for x in parts if x)[:1500]


async def vectorize_vulns(limit: int | None = None, batch_size: int = 64) -> dict:
    """为漏洞表回填向量"""
    es = get_embedding_service()
    if not es.warmup():
        logger.error("vectorize_aborted", reason="嵌入模型不可用")
        return {"ok": False, "reason": "嵌入模型不可用"}

    start = time.perf_counter()
    processed = 0

    async with get_session() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(Vulnerability)
            .where(Vulnerability.embedding.is_(None))
        )
        logger.info("vectorize_start", table="vulnerabilities", pending=total or 0)

        stmt = (
            select(Vulnerability.id, Vulnerability.title, Vulnerability.description,
                   Vulnerability.ai_category, Vulnerability.severity,
                   Vulnerability.affected_assets, Vulnerability.extra,
                   Vulnerability.technique_refs, Vulnerability.poc_refs,
                   Vulnerability.fix_info)
            .where(Vulnerability.embedding.is_(None))
            .order_by(Vulnerability.id)
        )
        if limit:
            stmt = stmt.limit(limit)

        rows = (await session.execute(stmt)).all()
        logger.info("vectorize_batch_loaded", count=len(rows))

        # 构造检索文本（构造轻量对象避免改动 ORM 实例）
        class _Row:
            def __init__(self, r):
                self.title = r.title
                self.description = r.description
                self.ai_category = r.ai_category
                self.severity = r.severity
                self.affected_assets = r.affected_assets
                self.extra = r.extra
                self.technique_refs = r.technique_refs
                self.poc_refs = r.poc_refs
                self.fix_info = r.fix_info

        texts = [build_vuln_text(_Row(r)) for r in rows]

        # 分批编码并写回
        for start_idx in range(0, len(rows), batch_size):
            batch_rows = rows[start_idx : start_idx + batch_size]
            batch_texts = texts[start_idx : start_idx + batch_size]
            vectors = es.encode_documents(batch_texts)
            if not vectors:
                logger.warning("vectorize_batch_failed", offset=start_idx)
                continue

            for row, vec in zip(batch_rows, vectors, strict=False):
                await session.execute(
                    update(Vulnerability)
                    .where(Vulnerability.id == row.id)
                    .values(embedding=vec)
                )
            await session.commit()
            processed += len(batch_rows)

            if (start_idx // batch_size) % 5 == 0:
                logger.info(
                    "vectorize_progress",
                    done=processed,
                    total=len(rows),
                    elapsed_s=int(time.perf_counter() - start),
                )

    elapsed = time.perf_counter() - start
    return {
        "ok": True,
        "table": "vulnerabilities",
        "processed": processed,
        "elapsed_s": round(elapsed, 1),
        "speed": round(processed / elapsed, 1) if elapsed else 0,
    }


async def vectorize_papers(limit: int | None = None, batch_size: int = 64) -> dict:
    """为论文表回填向量"""
    es = get_embedding_service()
    if not es.warmup():
        return {"ok": False, "reason": "嵌入模型不可用"}

    start = time.perf_counter()
    processed = 0

    async with get_session() as session:
        total = await session.scalar(
            select(func.count()).select_from(Paper).where(Paper.embedding.is_(None))
        )
        logger.info("vectorize_start", table="papers", pending=total or 0)

        stmt = (
            select(Paper.id, Paper.title, Paper.abstract, Paper.ai_topics,
                   Paper.categories, Paper.authors)
            .where(Paper.embedding.is_(None))
            .order_by(Paper.id)
        )
        if limit:
            stmt = stmt.limit(limit)

        rows = (await session.execute(stmt)).all()
        logger.info("vectorize_batch_loaded", table="papers", count=len(rows))

        class _Row:
            def __init__(self, r):
                self.title = r.title
                self.abstract = r.abstract
                self.ai_topics = r.ai_topics
                self.categories = r.categories
                self.authors = r.authors

        texts = [build_paper_text(_Row(r)) for r in rows]

        for start_idx in range(0, len(rows), batch_size):
            batch_rows = rows[start_idx : start_idx + batch_size]
            batch_texts = texts[start_idx : start_idx + batch_size]
            vectors = es.encode_documents(batch_texts)
            if not vectors:
                continue
            for row, vec in zip(batch_rows, vectors, strict=False):
                await session.execute(
                    update(Paper).where(Paper.id == row.id).values(embedding=vec)
                )
            await session.commit()
            processed += len(batch_rows)

    elapsed = time.perf_counter() - start
    return {
        "ok": True,
        "table": "papers",
        "processed": processed,
        "elapsed_s": round(elapsed, 1),
    }


async def vectorize_policies(limit: int | None = None, batch_size: int = 64) -> dict:
    """
    为政策法规表回填向量

    赛题赛题三类知识底座之一。政策数据入库后若不向量化，
    问答层完全检索不到——问「欧盟 AI 法案有哪些合规要求」
    会返回毫不相关的漏洞条目。
    """
    from ..知识库.models import Policy

    es = get_embedding_service()
    if not es.warmup():
        return {"ok": False, "reason": "嵌入模型不可用", "table": "policies"}

    start = time.perf_counter()
    processed = 0

    async with get_session() as session:
        total = await session.scalar(
            select(func.count()).select_from(Policy).where(Policy.embedding.is_(None))
        )
        logger.info("vectorize_start", table="policies", pending=total or 0)

        stmt = (
            select(Policy.id, Policy.title, Policy.summary, Policy.policy_kind,
                   Policy.org, Policy.region, Policy.doc_numbers, Policy.ai_keywords)
            .where(Policy.embedding.is_(None))
            .order_by(Policy.id)
        )
        if limit:
            stmt = stmt.limit(limit)

        rows = (await session.execute(stmt)).all()
        logger.info("vectorize_batch_loaded", table="policies", count=len(rows))
        if not rows:
            return {
                "ok": True,
                "table": "policies",
                "processed": 0,
                "elapsed_s": 0,
            }

        class _Row:
            def __init__(self, r):
                self.title = r.title
                self.summary = r.summary
                self.policy_kind = r.policy_kind
                self.org = r.org
                self.region = r.region
                self.doc_numbers = r.doc_numbers
                self.ai_keywords = r.ai_keywords

        texts = [build_policy_text(_Row(r)) for r in rows]

        for start_idx in range(0, len(rows), batch_size):
            batch_rows = rows[start_idx : start_idx + batch_size]
            batch_texts = texts[start_idx : start_idx + batch_size]
            vectors = es.encode_documents(batch_texts)
            if not vectors:
                continue
            for row, vec in zip(batch_rows, vectors, strict=False):
                await session.execute(
                    update(Policy).where(Policy.id == row.id).values(embedding=vec)
                )
            await session.commit()
            processed += len(batch_rows)

    elapsed = time.perf_counter() - start
    return {
        "ok": True,
        "table": "policies",
        "processed": processed,
        "elapsed_s": round(elapsed, 1),
    }


async def show_stats() -> dict:
    """查看向量化状态"""
    async with get_session() as session:
        v_total = await session.scalar(select(func.count()).select_from(Vulnerability))
        v_done = await session.scalar(
            select(func.count())
            .select_from(Vulnerability)
            .where(Vulnerability.embedding.isnot(None))
        )
        p_total = await session.scalar(select(func.count()).select_from(Paper))
        p_done = await session.scalar(
            select(func.count()).select_from(Paper).where(Paper.embedding.isnot(None))
        )
        from ..知识库.models import Policy

        pol_total = await session.scalar(select(func.count()).select_from(Policy))
        pol_done = await session.scalar(
            select(func.count()).select_from(Policy).where(Policy.embedding.isnot(None))
        )

    return {
        "vulnerabilities": {"total": v_total, "vectorized": v_done,
                            "pending": (v_total or 0) - (v_done or 0)},
        "papers": {"total": p_total, "vectorized": p_done,
                   "pending": (p_total or 0) - (p_done or 0)},
        "policies": {"total": pol_total, "vectorized": pol_done,
                     "pending": (pol_total or 0) - (pol_done or 0)},
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="向量化回填")
    parser.add_argument("--batch", type=int, default=0, help="单次最多处理条数，0 为全部")
    parser.add_argument("--size", type=int, default=64, help="编码批大小")
    parser.add_argument("--stats", action="store_true", help="仅查看状态")
    parser.add_argument("--papers-only", action="store_true", help="只处理论文")
    parser.add_argument("--vulns-only", action="store_true", help="只处理漏洞")
    parser.add_argument(
        "--policies-only", action="store_true", help="只处理政策法规"
    )
    args = parser.parse_args()

    await init_database()
    if args.stats:
        stats = await show_stats()
        print("\n=== 向量化状态 ===")
        for table, info in stats.items():
            print(
                f"  {table:16} {info['vectorized']}/{info['total']} "
                f"({info['vectorized'] / max(info['total'], 1) * 100:.1f}%) "
                f"待处理 {info['pending']}"
            )
        await close_database()
        return

    limit = args.batch if args.batch > 0 else None
    results = []

    if not args.papers_only:
        results.append(await vectorize_vulns(limit=limit, batch_size=args.size))
    if not args.vulns_only:
        results.append(await vectorize_papers(limit=limit, batch_size=args.size))
    if not (args.papers_only or args.vulns_only):
        results.append(await vectorize_policies(limit=limit, batch_size=args.size))

    print("\n=== 向量化完成 ===")
    for r in results:
        if r.get("ok"):
            print(
                f"  {r['table']:16} 处理 {r['processed']} 条 "
                f"耗时 {r['elapsed_s']}s ({r.get('speed', 0)} 条/秒)"
            )
        else:
            print(f"  失败: {r.get('reason')}")

    stats = await show_stats()
    print("\n=== 当前状态 ===")
    for table, info in stats.items():
        pct = info["vectorized"] / max(info["total"], 1) * 100
        print(f"  {table:16} {info['vectorized']}/{info['total']} ({pct:.1f}%)")

    await close_database()
if __name__ == "__main__":
    asyncio.run(main())
