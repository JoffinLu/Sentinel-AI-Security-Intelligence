"""
维度三：POC 关联

赛题要求富化"POC 验证代码"。产出内容：
- 匹配的 POC 仓库 / Exploit-DB 条目
- 可用性评估（是否可直接利用、是否需要环境准备）
- 关联依据（关键词重合度），便于人工核验

匹配策略：关键词 Jaccard 相似度 + 星标数加权。
纯规则实现，不依赖 LLM，保证响应速度与稳定性。
"""

from __future__ import annotations

import math
from typing import Any

from .base_enricher import (
    BaseEnricher,
    VulnContext,
    keyword_overlap,
    normalize_keyword,
)

# POC 检索词（从漏洞描述提取）
_POC_TERMS = [
    "exploit", "poc", "proof", "concept", "payload", "shellcode",
    "metasploit", "nuclei", "burp", "reproducer", "repro",
]


def _extract_poc_terms(text: str) -> set[str]:
    """检查描述中是否提及 POC 相关概念"""
    lower = text.lower()
    return {t for t in _POC_TERMS if t in lower}


class POCEnricher(BaseEnricher):
    """维度三：POC 关联富化"""

    dimension = "poc"
    requires_llm = False

    async def enrich(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        # 从漏洞文本构造检索关键词
        vuln_keywords = normalize_keyword(f"{ctx.title} {ctx.description}")
        mentioned_poc = _extract_poc_terms(f"{ctx.title} {ctx.description}")

        candidates = ctx.all_pocs or []
        scored: list[tuple[float, dict[str, Any]]] = []

        for poc in candidates:
            poc_text = normalize_keyword(
                f"{poc.get('repo_name', '')} {poc.get('title', '')} {poc.get('description', '')}"
            )
            overlap = keyword_overlap(vuln_keywords, poc_text)
            if overlap <= 0:
                continue
            # 星标数作为质量加权（log 抑制，避免头部仓库垄断）
            stars = poc.get("stars", 0) or 0

            star_bonus = min(math.log10(stars + 1) * 0.08, 0.25) if stars > 0 else 0
            scored.append((overlap + star_bonus, poc))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 取相似度最高的 3 条作为关联
        top_scored = scored[:3]
        matches = [
            {
                "name": poc.get("repo_name") or poc.get("name"),
                "url": poc.get("source_url") or poc.get("url"),
                "stars": poc.get("stars"),
                "language": poc.get("language"),
                "description": (poc.get("description") or "")[:300],
                "availability": poc.get("availability"),
                "relevance": round(score, 3),
            }
            for score, poc in top_scored
        ]

        # 描述中明确提及 POC 时，置信度上调（有直接证据）
        confidence = 0.75 if matches else 0.6
        if mentioned_poc:
            confidence = 0.85

        # 完全没有 POC 库数据时，诚实降级
        if not candidates:
            return (
                {
                    "pocs": [],
                    "poc_count": 0,
                    "exploitability": "未知",
                    "note": "POC 库为空，无法完成关联（可能上游采集未执行）",
                    "mentioned_in_description": bool(mentioned_poc),
                },
                0.4,
            )

        # 可用性评估
        high_quality = [m for m in matches if (m["stars"] or 0) >= 10]
        if high_quality:
            exploitability = "已有可用POC"
        elif matches:
            exploitability = "存在POC但成熟度低"
        else:
            exploitability = "暂未发现公开POC"

        return (
            {
                "pocs": matches,
                "poc_count": len(matches),
                "exploitability": exploitability,
                "mentioned_in_description": bool(mentioned_poc),
                "matched_terms": sorted(mentioned_poc)[:5] if mentioned_poc else [],
                "note": "基于关键词重合度匹配，结果需人工核验" if matches else None,
            },
            confidence,
        )
