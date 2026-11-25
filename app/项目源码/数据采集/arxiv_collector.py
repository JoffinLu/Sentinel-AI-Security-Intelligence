"""
arXiv 学术论文数据源

赛题明确要求"同步采集最新的 AI 安全学术论文"。
arXiv 的 cs.CR（密码学与安全）与 cs.LG（机器学习）是最相关的两个分类。

采用 Atom 协议解析，按提交时间倒序增量拉取。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

from .base_collector import BaseCollector, RawItem

API_BASE = "http://export.arxiv.org/api/query"

# AI 安全主题词——用于论文主题标注
AI_SECURITY_TOPICS = {
    "提示词注入": ["prompt injection", "prompt attack"],
    "越狱攻击": ["jailbreak", "jailbreaking"],
    "对抗样本": ["adversarial example", "adversarial attack", "adversarial robustness"],
    "模型后门": ["backdoor attack", "trojan", "neural backdoor"],
    "数据投毒": ["data poisoning", "poisoning attack", "backdoor"],
    "模型窃取": ["model stealing", "model extraction", "extraction attack"],
    "成员推断": ["membership inference"],
    "隐私泄露": ["privacy leak", "differential privacy", "federated learning privacy"],
    "联邦学习安全": ["federated learning", "secure aggregation"],
    "RAG 安全": ["retrieval augmented generation", "rag", "knowledge poisoning"],
    "智能体安全": ["agent security", "llm agent", "autonomous agent", "tool use"],
    "供应链安全": ["supply chain", "model supply chain", "trojan model"],
    "可解释性": ["interpretability", "explainability", "transparency"],
    "对齐与越狱防御": ["alignment", "guardrail", "safety alignment", "refusal"],
}


class ArxivCollector(BaseCollector):
    """arXiv 论文数据源"""

    code = "arxiv"
    name = "arXiv 学术论文"
    category = "学术"
    homepage = "https://arxiv.org/"
    default_interval = 43200  # 12 小时
    # arXiv API 要求请求间隔 ≥3 秒；学术论文的产出与评审是天级节奏，
    # 不存在「必须小时内看到」的场景，6 小时足够。
    min_interval = 21600
    max_interval = 86400
    interval_basis = "arXiv API 请求间隔 ≥3 秒；学术产出为天级节奏，无小时级时效要求"
    ai_native = True

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        拉取 AI 安全相关论文

        使用多个检索式覆盖不同主题，提高召回率。
        """
        client = await self.get_client()

        queries = [
            'cat:cs.CR AND (abs:"prompt injection" OR abs:"jailbreak")',
            'cat:cs.CR AND abs:"large language model" AND abs:"attack"',
            'cat:cs.CR AND abs:"adversarial" AND abs:"language model"',
            'cat:cs.LG AND abs:"poisoning" AND abs:"learning"',
        ]

        entries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for q in queries:
            params = {
                "search_query": q,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": 60,
            }
            try:
                resp = await client.get(API_BASE, params=params)
                resp.raise_for_status()
                parsed = self._parse_atom(resp.text)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("arxiv_query_failed", query=q, error=str(exc)[:200])
                continue

            for entry in parsed:
                aid = entry.get("id", "")
                if aid and aid not in seen_ids:
                    seen_ids.add(aid)
                    entries.append(entry)

            self.log.info("arxiv_query_done", query=q, found=len(parsed))
            await asyncio.sleep(3)  # arXiv 要求请求间隔不少于 3 秒

        # 按发布时间游标过滤
        if cursor:
            entries = [e for e in entries if (e.get("_published_dt") or datetime.min.replace(tzinfo=UTC)) > self._parse_iso(cursor)]

        return entries

    def _parse_atom(self, xml_text: str) -> list[dict[str, Any]]:
        """解析 arXiv Atom 响应"""
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        root = ElementTree.fromstring(xml_text)
        entries: list[dict[str, Any]] = []

        for entry in root.findall("atom:entry", ns):
            raw_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
            arxiv_id = raw_id.rsplit("/", 1)[-1] if raw_id else ""
            if not arxiv_id:
                continue

            title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
            summary = " ".join(
                (entry.findtext("atom:summary", default="", namespaces=ns) or "").split()
            )

            authors = [
                a.findtext("atom:name", default="", namespaces=ns)
                for a in entry.findall("atom:author", ns)
            ]

            categories = [
                c.get("term")
                for c in entry.findall("atom:category", ns)
                if c.get("term")
            ]

            published = entry.findtext("atom:published", default="", namespaces=ns)
            updated = entry.findtext("atom:updated", default="", namespaces=ns)
            pdf_url = ""
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                    pdf_url = link.get("href", "")
                    break

            # 主题标注
            topics = self._detect_topics(f"{title} {summary}")

            entries.append(
                {
                    "id": arxiv_id,
                    "title": title,
                    "summary": summary,
                    "authors": [a for a in authors if a],
                    "categories": categories,
                    "published": published,
                    "updated": updated,
                    "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
                    "topics": topics,
                    "_published_dt": self._parse_iso(published),
                }
            )
        return entries

    def _detect_topics(self, text: str) -> list[str]:
        """基于关键词的论文主题标注"""
        lower = text.lower()
        hits: list[str] = []
        for topic, keywords in AI_SECURITY_TOPICS.items():
            if any(kw in lower for kw in keywords):
                hits.append(topic)
        return hits

    def normalize(self, raw: Any) -> list[RawItem]:
        """
        论文归一化

        论文不是漏洞，dedup_key 使用 arxiv_id，
        后续由入库层分流到 papers 表。
        """
        arxiv_id = raw.get("id", "")
        if not arxiv_id:
            return []

        title = raw.get("title", "")
        summary = raw.get("summary", "")
        topics = raw.get("topics", [])

        return [
            RawItem(
                dedup_key=f"arxiv:{arxiv_id}",
                origin_source=self.code,
                external_id=arxiv_id,
                title=title,
                description=summary,
                source_name="arXiv",
                source_url=f"https://arxiv.org/abs/{arxiv_id}",
                published_at=raw.get("_published_dt") or self._parse_iso(raw.get("published")),
                modified_at=self._parse_iso(raw.get("updated")),
                references=[raw["pdf_url"]] if raw.get("pdf_url") else [],
                extra={
                    "is_paper": True,
                    "authors": raw.get("authors", []),
                    "categories": raw.get("categories", []),
                    "ai_topics": topics,
                    "pdf_url": raw.get("pdf_url"),
                },
            )
        ]
