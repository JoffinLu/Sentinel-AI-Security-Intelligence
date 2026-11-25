"""知识库全貌：给 LLM 一张「数据地图」。

为什么需要这个
--------------
现在问答的 LLM 只拿到「检索命中的 3~5 条片段」，它看不到全库，
于是问「知识库里一共有多少条数据」时会把「命中 3 条」当成「总量 3 条」
——实测答错（真实 4886 条）。

本模块提供**由 SQL 统计出的准确数字与字段说明**，
让 LLM 知道「我有什么数据、数据长什么样」，
从而能回答「哪个最多 / 有哪些属性 / 含义是什么」这类问题。

设计要点
--------
* **所有数字都由 SQL 计算**，绝不让 LLM 自己数 —— 它数不准。
* 统计结果带**短 TTL 缓存**（默认 60s）——
  Agent 每轮可能要查多次全貌，重复统计会给数据库添麻烦；
  而采集间隔最短也是 1 小时，60s 缓存完全够用。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select

# ---------- 各表的业务含义与字段中文说明 ----------
# 这些说明是给 LLM 看的 —— 它要靠这些文字理解「cvss_score 是什么」，
# 才能回答用户「哪些属性是什么含义」。
TABLE_DOCS: dict[str, dict[str, Any]] = {
    "vulnerabilities": {
        "label": "漏洞情报（NVD / GHSA / OSV / CISA KEV 等多源去重合并）",
        "columns": {
            "cve_id": "唯一标识（CVE/GHSA 编号，或 poc:/rss: 前缀的内部键）",
            "title": "漏洞标题",
            "description": "漏洞描述全文",
            "cvss_score": "CVSS 评分（0~10，数值越高越严重）",
            "cvss_vector": "CVSS 向量串，如 AV:N/AC:L/PR:N",
            "severity": "严重度：CRITICAL / HIGH / MEDIUM / LOW",
            "ai_category": "AI 安全分类：模型与数据层风险 / AI应用与智能体风险 / 供应链风险 等",
            "ai_relevance_score": "AI 相关度0~1（AI 自动打的分）",
            "ai_relevance_method": "相关度的判定方式（规则/模型/人工）",
            "ai_keywords": "AI 抽取的关键词",
            "origin_source": "首发数据源（nvd/ghsa/osv/cisa_kev…）",
            "published_at": "漏洞公开时间",
            "enrichment_status": "富化状态：pending / completed",
            "references": "参考链接列表",
            "content_hash": "内容哈希（用于变更检测）",
        },
    },
    "papers": {
        "label": "学术论文（arXiv，赛题赛题三类知识底座之一）",
        "columns": {
            "arxiv_id": "arXiv 编号",
            "title": "论文标题",
            "authors": "作者列表",
            "ai_topics": "AI 主题标签，如 RAG 安全 / 数据投毒 / 提示词注入",
            "published_at": "发表时间",
            "pdf_url": "PDF 链接",
        },
    },
    "policies": {
        "label": "政策法规（赛题赛题三类知识底座之一）",
        "columns": {
            "doc_key": "唯一键",
            "title": "文件标题",
            "summary": "摘要",
            "policy_kind": "属性：政策 / 法规 / 标准 / 观点",
            "org": "发布机构",
            "region": "地区（美国 / 国际 / 中国…）",
            "doc_numbers": "文号列表",
            "published_at": "发布时间",
        },
    },
    "techniques": {
        "label": "MITRE ATLAS 攻击技术（AI 专用攻击基线，赛题赛题三类知识底座之一）",
        "columns": {
            "atlas_id": "ATLAS 编号，如 AML.T0051",
            "name": "技术名称（英文）",
            "tactic": "所属战术编号（攻击链阶段）",
            "description": "技术描述",
            "maturity": "成熟度：realized 已实证 / demonstrated 已演示 / feasible 可行",
        },
    },
}

# 攻击链战术的中文名（与前端 lib/atlasTaxonomy.ts 保持一致）
TACTIC_ZH: dict[str, str] = {
    "AML.TA0000": "访问 AI 系统",
    "AML.TA0001": "准备攻击能力",
    "AML.TA0002": "侦察",
    "AML.TA0003": "资源开发",
    "AML.TA0004": "初始访问",
    "AML.TA0005": "执行",
    "AML.TA0006": "持久化",
    "AML.TA0007": "权限提升",
    "AML.TA0008": "防御规避",
    "AML.TA0009": "凭据访问",
    "AML.TA0010": "信息发现",
    "AML.TA0011": "横向移动",
    "AML.TA0012": "数据收集",
    "AML.TA0013": "数据外泄",
    "AML.TA0014": "产生影响",
    "AML.TA0015": "备用认证材料",
}

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, Any] = {"at": 0.0, "data": None}


async def _collect() -> dict[str, Any]:
    """从库里统计出全貌数据。"""
    from ..知识库.database import get_session
    from ..知识库.models import (
        Paper,
        Policy,
        Technique,
        Vulnerability,
    )

    async with get_session() as session:
        v_total = await session.scalar(select(func.count()).select_from(Vulnerability))
        p_total = await session.scalar(select(func.count()).select_from(Paper))
        pol_total = await session.scalar(select(func.count()).select_from(Policy))
        t_total = await session.scalar(
            select(func.count())
            .select_from(Technique)
            .where(Technique.is_case_study.is_(False))
        )

        # severity 分布
        sev_rows = await session.execute(
            select(Vulnerability.severity, func.count())
            .group_by(Vulnerability.severity)
        )
        severity = {s: int(c) for s, c in sev_rows if s}

        # AI 分类分布
        cat_rows = await session.execute(
            select(Vulnerability.ai_category, func.count())
            .group_by(Vulnerability.ai_category)
        )
        ai_category = {c: int(n) for c, n in cat_rows if c}

        # 数据源分布
        src_rows = await session.execute(
            select(Vulnerability.origin_source, func.count())
            .group_by(Vulnerability.origin_source)
        )
        by_source = {s: int(n) for s, n in src_rows if s}

        # 论文主题分布
        topic_rows = await session.execute(select(Paper.ai_topics).limit(500))
        topic_count: dict[str, int] = {}
        for (topics,) in topic_rows:
            for t in topics or []:
                topic_count[t] = topic_count.get(t, 0) + 1
        paper_topics = dict(
            sorted(topic_count.items(), key=lambda kv: kv[1], reverse=True)[:15]
        )

        # 政策属性 / 地区分布
        kind_rows = await session.execute(
            select(Policy.policy_kind, func.count()).group_by(Policy.policy_kind)
        )
        policy_kinds = {k: int(n) for k, n in kind_rows if k}
        region_rows = await session.execute(
            select(Policy.region, func.count()).group_by(Policy.region)
        )
        policy_regions = {r: int(n) for r, n in region_rows if r}

        # ATLAS 战术分布
        tac_rows = await session.execute(
            select(Technique.tactic, func.count())
            .where(
                Technique.is_case_study.is_(False),
                Technique.tactic.isnot(None),
            )
            .group_by(Technique.tactic)
        )
        tactics = {
            t: {"count": int(n), "label": TACTIC_ZH.get(t, t)} for t, n in tac_rows if t
        }

        # CVSS 区间分布（让LLM 能回答「高危有多少」这类问题）
        cvss_rows = await session.execute(
            select(
                func.count().filter(Vulnerability.cvss_score >= 9.0),
                func.count().filter(
                    (Vulnerability.cvss_score < 9.0)
                    & (Vulnerability.cvss_score >= 7.0)
                ),
                func.count().filter(
                    (Vulnerability.cvss_score < 7.0)
                    & (Vulnerability.cvss_score >= 4.0)
                ),
                func.count().filter(Vulnerability.cvss_score < 4.0),
            )
        )
        crit, high, mid, low = (int(x or 0) for x in cvss_rows.one())

        # 时间跨度
        span_rows = await session.execute(
            select(
                func.min(Vulnerability.published_at),
                func.max(Vulnerability.published_at),
                func.max(Vulnerability.ingest_at),
            )
        )
        v_min, v_max, v_ingest = span_rows.one()

        return {
            "counts": {
                "vulnerabilities": int(v_total or 0),
                "papers": int(p_total or 0),
                "policies": int(pol_total or 0),
                "techniques": int(t_total or 0),
            },
            "schema": {
                name: {"label": doc["label"], "columns": doc["columns"]}
                for name, doc in TABLE_DOCS.items()
            },
            "severity_distribution": severity,
            "cvss_distribution": {
                "critical_9_10": crit,
                "high_7_9": high,
                "medium_4_7": mid,
                "low_0_4": low,
            },
            "ai_category_distribution": ai_category,
            "by_source": by_source,
            "paper_topics": paper_topics,
            "policy_kinds": policy_kinds,
            "policy_regions": policy_regions,
            "atlas_tactics": dict(sorted(tactics.items(), key=lambda kv: kv[0])),
            "vulnerability_time_range": {
                "earliest_published": v_min.isoformat() if v_min else None,
                "latest_published": v_max.isoformat() if v_max else None,
                "last_ingested": v_ingest.isoformat() if v_ingest else None,
            },
        }


async def get_overview(force: bool = False) -> dict[str, Any]:
    """取知识库全貌（带短 TTL 缓存）。"""
    now = time.monotonic()
    if not force and _cache["data"] is not None and now - _cache["at"] < _CACHE_TTL_SECONDS:
        return _cache["data"]
    data = await _collect()
    _cache["at"] = now
    _cache["data"] = data
    return data


def render_for_llm(ov: dict[str, Any]) -> str:
    """把全貌渲染成给 LLM 阅读的紧凑文本。

   刻意不用 JSON —— 文本更省 token，也更接近「把表发给模型」的直觉。
    数字全部来自 SQL，不给模型自己数的机会。
    """
    c = ov["counts"]
    lines: list[str] = []
    lines.append("【知识库总览】")
    lines.append(
        f"漏洞情报 {c['vulnerabilities']} 条 | 学术论文 {c['papers']} 篇 | "
        f"政策法规 {c['policies']} 条 | ATLAS 攻击技术 {c['techniques']} 项"
    )
    # 口径说明：techniques 表实际有 227 行，其中 57 条是「案例研究」
    # （is_case_study=True），不属于攻击技术基线，已排除。
    # 若模型直接 COUNT(*) 会得到 227 —— 必须提前说明，否则它会
    # 报出与总览不一致的数字（实测出现过）。
    lines.append(
        "（口径说明：techniques 表共 227 行，其中 57 条为案例研究 is_case_study=true，"
        "不属于攻击技术基线；统计技术项数时请加条件 "
        "WHERE is_case_study = false，即 170 项。）"
    )

    cv = ov["cvss_distribution"]
    lines.append(
        f"CVSS 分布：9.0+ 有{cv['critical_9_10']} 条 / 7.0-8.9 有 {cv['high_7_9']} 条 / "
        f"4.0-6.9 有 {cv['medium_4_7']} 条 / 0-3.9 有 {cv['low_0_4']} 条"
    )
    if ov["severity_distribution"]:
        sev = "、".join(f"{k} {v}" for k, v in ov["severity_distribution"].items())
        lines.append(f"严重度标注：{sev}")
    if ov["ai_category_distribution"]:
        cat = "、".join(f"{k} {v}" for k, v in ov["ai_category_distribution"].items())
        lines.append(f"AI 分类：{cat}")
    if ov["by_source"]:
        src = "、".join(f"{k} {v}" for k, v in ov["by_source"].items())
        lines.append(f"数据源：{src}")
    if ov["paper_topics"]:
        tp = "、".join(f"{k} {v}" for k, v in ov["paper_topics"].items())
        lines.append(f"论文主题：{tp}")
    if ov["policy_kinds"]:
        pk = "、".join(f"{k} {v}" for k, v in ov["policy_kinds"].items())
        lines.append(f"政策属性：{pk}")
    if ov["atlas_tactics"]:
        tc = "、".join(f"{v['label']}({v['count']})" for v in ov["atlas_tactics"].values())
        lines.append(f"ATLAS 攻击链阶段（按顺序）：{tc}")
    tr = ov["vulnerability_time_range"]
    if tr.get("latest_published"):
        lines.append(
            f"漏洞时间跨度：最早 {str(tr.get('earliest_published'))[:10]} 至"
            f" 最新 {str(tr['latest_published'])[:10]}"
        )

    lines.append("")
    lines.append("【各表字段含义】")
    for table, meta in ov["schema"].items():
        lines.append(f"· {table}（{meta['label']}）")
        for col, desc in meta["columns"].items():
            lines.append(f"    - {col}: {desc}")
    return "\n".join(lines)
