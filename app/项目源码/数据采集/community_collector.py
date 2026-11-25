"""
安全社区 RSS 采集器

覆盖 FreeBuf、先知等中文安全社区，以及 AWS、Cloudflare、Google 等厂商博客，
补充厂商公告与漏洞库之外的视角。RSS 走标准 Atom/RDF 协议。
"""

from __future__ import annotations

import asyncio
from typing import Any

import feedparser
from bs4 import BeautifulSoup

from .base_collector import BaseCollector, RawItem

# ========== 安全社区 RSS ==========

RSS_FEEDS = [
    ("freebuf", "FreeBuf", "https://www.freebuf.com/feed", "社区"),
    ("xianzhi", "阿里云先知", "https://xz.aliyun.com/feed", "社区"),
    ("github_ai_security", "GitHub Trending - Security", "https://github.com/security.atom", "社区"),
    ("krebs", "Krebs on Security", "https://krebsonsecurity.com/feed/", "社区"),
    ("schneier", "Schneier on Security", "https://www.schneier.com/feed/atom/", "社区"),
    ("google_security_blog", "Google Security Blog", "https://googleprojectzero.blogspot.com/feeds/posts/default", "厂商"),
    ("cloudflare", "Cloudflare Blog", "https://blog.cloudflare.com/rss/", "厂商"),
    ("aws_security", "AWS Security Blog", "https://aws.amazon.com/blogs/security/feed/", "厂商"),
]

# AI 安全主题词——用于过滤 RSS 中的无关内容
AI_RSS_KEYWORDS = [
    "ai", "llm", "gpt", "machine learning", "deep learning", "neural",
    "prompt", "model", "agent", "rag", "transformer", "diffusion",
    "人工智能", "大模型", "提示词", "模型", "智能体", "机器学习", "深度学习",
]


class SecurityRSSCollector(BaseCollector):
    """
    安全社区与厂商博客 RSS 采集器

    覆盖 FreeBuf、先知等中文社区与 AWS、Cloudflare 等厂商博客，
    支撑赛题要求的"安全社区"与"厂商公告"两类数据源。
    """

    code = "rss"
    name = "安全社区与厂商博客"
    category = "社区"
    homepage = "https://www.freebuf.com/"
    default_interval = 1800  # 30 分钟（RSS 静态文件最友好，社区博客分钟级更新）
    # RSS 是静态文件、无 API 配额限制，是唯一能接近「实时」的源；
    # 上限 2 小时是为了别比同页其他源慢太多而失去意义。
    min_interval = 600
    max_interval = 7200
    interval_basis = "RSS 静态文件无配额限制；社区博客分钟级更新，10 分钟即可捕捉"
    ai_native = True

    async def fetch(self, cursor: str | None) -> list[Any]:
        """并发拉取所有 RSS 源"""
        client = await self.get_client()
        feeds = []

        async def _fetch_one(code: str, name: str, url: str, cat: str) -> dict[str, Any]:
            try:
                resp = await client.get(url, timeout=30.0)
                resp.raise_for_status()
                return {"code": code, "name": name, "category": cat, "content": resp.text}
            except Exception as exc:  # noqa: BLE001
                self.log.debug("rss_failed", feed=code, error=str(exc)[:100])
                return {"code": code, "name": name, "category": cat, "error": str(exc)[:200]}

        results = await asyncio.gather(
            *[_fetch_one(c, n, u, cat) for c, n, u, cat in RSS_FEEDS],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, dict):
                feeds.append(r)
        return feeds

    def normalize(self, raw: dict[str, Any]) -> list[RawItem]:
        """解析 RSS/Atom 内容，仅保留 AI 安全相关条目"""
        if raw.get("error") or not raw.get("content"):
            return []

        feed_code = raw.get("code", "rss")
        feed_name = raw.get("name", "RSS")
        parsed = feedparser.parse(raw["content"])
        items: list[RawItem] = []

        for entry in parsed.entries:
            title = entry.get("title", "").strip()
            if not title:
                continue

            summary = entry.get("summary", "") or ""
            # 清洗 HTML
            summary_clean = BeautifulSoup(summary, "lxml").get_text(" ").strip()

            full_text = f"{title} {summary_clean}".lower()
            # 只保留含 AI 相关词的条目，避免噪声淹没情报
            if not any(kw in full_text for kw in AI_RSS_KEYWORDS):
                continue

            link = entry.get("link", "")
            published = entry.get("published") or entry.get("updated")
            items.append(
                RawItem(
                    dedup_key=f"rss:{feed_code}:{_link_hash(link or title)}",
                    origin_source=feed_code,
                    external_id=link or title,
                    title=title,
                    description=summary_clean,
                    source_name=feed_name,
                    source_url=link,
                    published_at=self._parse_rfc822(published),
                    extra={
                        "is_news": True,
                        "feed_code": feed_code,
                        "feed_name": feed_name,
                        "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                    },
                )
            )
        return items

    @staticmethod
    def _parse_rfc822(value: str | None):
        """解析 RFC822 时间（feedparser 的标准格式）"""
        if not value:
            return None
        try:
            from datetime import UTC
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(value)
        except Exception:  # noqa: BLE001
            return None
        if dt is not None and dt.tzinfo is None:
            # RFC2822 的 -0000（未知偏移）会解析出 naive datetime，
            # 与 _parse_iso 同一陷阱：asyncpg 存 timestamptz 时按本机
            # 本地时区(+08)换算，published_at 会早 8 小时。按 UTC 兜底。
            dt = dt.replace(tzinfo=UTC)
        return dt


def _link_hash(value: str) -> str:
    """链接去重哈希"""
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
