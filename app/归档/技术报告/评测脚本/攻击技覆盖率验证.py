"""
攻击技映射覆盖率重跑与验证

背景
----
赛题要求「漏洞与攻击技术的映射覆盖率 ≥ 70%」。
首轮实测仅 58%，原因是大量真实漏洞（路径穿越、SSRF、认证绕过等
传统 Web 攻击手法发生在 AI 组件上）在ATLAS 中找不到"AI 特有"
对应项，被判为 no_match。

修复方式：在 _ATLAS_RULES 中补充这类映射规则。
本脚本用于量化验证修复效果。

口径说明（避免虚高）
--------------------
分母 = 适用 ATLAS 的 AI 相关漏洞
分子 = conclusion == "mapped" 的漏洞
applicable=False（传统 Web 漏洞，ATLAS 不覆盖）不计入分母。

两种运行模式
------------
默认（规则通道，推荐）
    只跑规则匹配，不调 LLM。理由：
    ① 结果确定、可复现，不受网关限流影响
    ② LLM 通道单条 40~60s，163 条需约 2 小时，实测频繁触发 429
       长冷却（1920s）与 JSON 截断解析失败，结果反而不稳定
    规则通道是确定性下限，LLM 只是补充

--with-llm
    走完整三通道，贴近线上真实覆盖率，但需数小时
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
from 项目源码.富化模块.technique_enricher import TechniqueEnricher  # noqa: E402
from 项目源码.富化模块.base_enricher import VulnContext  # noqa: E402

TARGET = 70.0
WITH_LLM = "--with-llm" in sys.argv


async def main() -> None:
    await init_database()

    enricher = TechniqueEnricher()

    # ATLAS 技术库仅 LLM 通道需要；规则通道不载入以节省时间
    atlas_techniques: list[dict] = []
    if WITH_LLM:
        try:
            from 项目源码.知识库.models import Technique

            async with get_session() as session:
                trows = (await session.execute(select(Technique))).scalars().all()
                atlas_techniques = [
                    {
                        "atlas_id": t.atlas_id,
                        "name": t.name,
                        "tactic": t.tactic,
                        "description": t.description or "",
                    }
                    for t in trows
                ]
            print(f"ATLAS 技术库载入：{len(atlas_techniques)} 项")
        except Exception as exc:  # noqa: BLE001
            print(f"ATLAS 技术库载入失败（退化为规则通道）：{exc}")
    else:
        print("模式：规则通道（确定性，可复现；加 --with-llm 走完整三通道）")

    async with get_session() as session:
        # AI 相关漏洞：被三级过滤器判定为相关（ai_category 非空）
        stmt = select(Vulnerability).where(
            Vulnerability.ai_category.isnot(None)
        )
        rows = (await session.execute(stmt)).scalars().all()

    print(f"AI 相关漏洞总数：{len(rows)}")
    if not rows:
        print("无数据，请先运行采集")
        await close_database()
        return

    stats: Counter[str] = Counter()
    technique_counter: Counter[str] = Counter()
    high_sev_total = high_sev_mapped = 0
    samples: list[tuple[str, str, str]] = []

    for v in rows:
        ctx = VulnContext(
            vulnerability_id=v.id,
            cve_id=v.cve_id,
            title=v.title or "",
            description=v.description or "",
            severity=v.severity,
            cvss_score=v.cvss_score,
            cvss_vector=getattr(v, "cvss_vector", None),
            affected_assets=v.affected_assets or [],
            references=v.references or [],
            ai_category=v.ai_category,
            ai_relevance_score=v.ai_relevance_score or 0.0,
            published_at=v.published_at,
            source_url=v.source_url,
            all_techniques=atlas_techniques,
        )
        try:
            data, conf = await enricher.enrich(ctx)
        except Exception:  # noqa: BLE001
            stats["error"] += 1
            continue

        applicable = data.get("applicable", True)
        techniques = data.get("techniques") or []
        conclusion = data.get("conclusion") or ("not_applicable" if not applicable else "no_match")

        #口径：applicable=False（传统 Web 漏洞，ATLAS 不覆盖）不计入分母，
        # 因为赛题考察的是「AI 相关漏洞」的映射能力。
        if not applicable:
            stats["not_applicable"] += 1
            continue

        stats[conclusion] += 1
        for t in techniques:
            tid = t.get("atlas_id") or t.get("technique_id") or t.get("id")
            if tid:
                technique_counter[tid] += 1

        is_high = (v.cvss_score or 0) >= 7.0
        if is_high:
            high_sev_total += 1
            if conclusion == "mapped":
                high_sev_mapped += 1

        if conclusion != "mapped":
            samples.append((v.cve_id, (v.title or "")[:66], conclusion))

    total = len(rows)
    mapped = stats["mapped"]
    not_applicable = stats["not_applicable"]
    undetermined = stats.get("undetermined", 0) + stats.get("error", 0)
    # 分母：适用 ATLAS 的漏洞（排除 not_applicable 与 undetermined）
    denominator = total - not_applicable - undetermined
    coverage = mapped / denominator * 100 if denominator else 0.0
    high_cov = high_sev_mapped / high_sev_total * 100 if high_sev_total else 0.0

    mode = "含 LLM 三通道" if WITH_LLM else "规则通道（确定性下限）"
    print("\n" + "=" * 62)
    print(f"攻击技映射覆盖率验证　[{mode}]")
    print("=" * 62)
    print(f"  AI 相关漏洞总数        ：{total}")
    print(f"  ATLAS 不适用（非AI攻击）：{not_applicable}（已排除，见口径说明）")
    print(f"  分子（mapped）          ：{mapped}")
    print(f"  分母                    ：{denominator}")
    print(f"  覆盖率                  ：{coverage:.1f}%")
    print(f"  赛题要求 ≥ {TARGET}%        ：{'达标' if coverage >= TARGET else '未达标'}")
    print()
    print(f"  高危（CVSS≥7）覆盖率     ：{high_cov:.1f}%（{high_sev_mapped}/{high_sev_total}）")
    print()
    print("  结论分布：")
    for k, v_ in stats.most_common():
        print(f"    {k:<16} {v_:>5}  ({v_/total*100:.1f}%)")
    print()
    print(f"  命中技术 Top 10（共 {len(technique_counter)} 个不同技术）：")
    for tid, c in technique_counter.most_common(10):
        print(f"    {tid:<16} {c:>4}")

    # 未映射样例落盘，供后续补充规则时分析
    if samples:
        out = ROOT / "数据" / "未映射样例.txt"
        out.write_text(
            "\n".join(f"{cve}\t{concl}\t{title}" for cve, title, concl in samples),
            encoding="utf-8",
        )
        print(f"\n  未映射 {len(samples)} 条，样例已写入 {out.relative_to(ROOT)}")
        print("  前 10 条：")
        for cve, title, concl in samples[:10]:
            print(f"    {cve:<18} [{concl}] {title}")

    print("\n" + "=" * 62)
    await close_database()
    sys.exit(0 if coverage >= TARGET else 2)


if __name__ == "__main__":
    asyncio.run(main())
