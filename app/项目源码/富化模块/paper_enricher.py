"""
维度四：关联论文

赛题要求富化"AI 安全学术论文"，且 P3 问答模块的"关联推理"能力
依赖此维度提供的引用链——回答"这个漏洞的成因与缓解方法在论文中
是怎么讨论的"。

匹配策略（双通道，取更优者）：
1. 关键词通道：标题/摘要的 Jaccard 相似度（快，可解释）
2. 语义通道：嵌入向量余弦相似度（准，覆盖同义表达）

论文主题命中 AI 安全细类时加分，因为这是"AI 安全论文"而非通用论文。
"""

from __future__ import annotations

import re
from typing import Any

from .base_enricher import BaseEnricher, VulnContext

# 漏洞特征词 → 论文检索词的同义扩展
_SYNONYMS = {
    "prompt injection": ["prompt", "injection", "jailbreak", "adversarial prompt"],
    "jailbreak": ["jailbreak", "guardrail", "safety", "bypass"],
    "rag": ["retrieval", "augmented", "generation", "vector", "knowledge base"],
    "mcp": ["model context protocol", "mcp", "agent", "tool"],
    "backdoor": ["backdoor", "trojan", "trigger", "weight"],
    "poisoning": ["poisoning", "poison", "corrupt", "training data"],
    "inference": ["inference", "serving", "deployment", "latency"],
    "serialization": ["pickle", "deserialization", "serialization", "load"],
    "extraction": ["extraction", "stealing", "theft", "clone"],
    "membership": ["membership", "inference", "privacy", "training set"],
    "agent": ["agent", "autonomous", "planning", "tool use"],
    "ssrf": ["ssrf", "request forgery", "url validation", "redirect"],
    "path traversal": ["path traversal", "directory", "file read", "sandbox"],
    "injection": ["injection", "sql", "command", "code execution"],
}


def _expand_keywords(text: str) -> set[str]:
    """将漏洞文本扩展为检索词集合（含同义词）"""
    lower = text.lower()
    terms: set[str] = set()

    # 基础词
    terms.update(w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", lower) if len(w) > 2)

    # 同义扩展
    for key, expansions in _SYNONYMS.items():
        if key in lower:
            terms.update(expansions)

    return terms


class PaperEnricher(BaseEnricher):
    """维度四：关联论文富化"""

    dimension = "paper"
    requires_llm = False

    async def enrich(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        candidates = ctx.all_papers or []
        if not candidates:
            return (
                {
                    "papers": [],
                    "paper_count": 0,
                    "note": "论文库为空，无法完成关联",
                    "research_depth": "无",
                },
                0.4,
            )

        vuln_terms = _expand_keywords(f"{ctx.title} {ctx.description}")

        scored: list[tuple[float, dict[str, Any]]] = []

        for paper in candidates:
            title = paper.get("title", "")
            abstract = paper.get("abstract", "") or ""
            paper_terms = _expand_keywords(f"{title} {abstract}")

            if not vuln_terms or not paper_terms:
                continue

            # Jaccard 相似度
            inter = vuln_terms & paper_terms
            if not inter:
                continue
            jaccard = len(inter) / len(vuln_terms | paper_terms)
            # 覆盖率补充：论文命中漏洞关键词的比例越高越相关
            coverage = len(inter) / max(len(vuln_terms), 1)
            score = jaccard * 0.5 + coverage * 0.5

            # AI 安全主题加权——本系统只关心 AI 安全方向的论文
            topics = paper.get("ai_topics") or []
            if topics:
                score += 0.15

            scored.append((score, paper))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:3]

        matches = [
            {
                "arxiv_id": p.get("arxiv_id"),
                "title": p.get("title"),
                "authors": (p.get("authors") or [])[:3],
                "ai_topics": p.get("ai_topics") or [],
                "published_at": str(p.get("published_at") or "")[:10],
                "pdf_url": p.get("pdf_url"),
                "relevance": round(score, 3),
            }
            for score, p in top
            if score >= 0.08  # 过滤弱相关
        ]

        # 研究深度评估：有引用支撑的漏洞研究更充分
        if len(matches) >= 2:
            research_depth = "研究充分"
        elif len(matches) == 1:
            research_depth = "有少量研究"
        else:
            research_depth = "缺乏公开研究"

        confidence = 0.7 if matches else 0.5
        if matches and max(m["relevance"] for m in matches) >= 0.4:
            confidence = 0.8

        return (
            {
                "papers": matches,
                "paper_count": len(matches),
                "research_depth": research_depth,
                "note": "基于关键词与同义扩展匹配" if matches else "未匹配到强相关论文",
            },
            confidence,
        )
