"""
AI 安全政策法规采集器

赛题明确要求
    「系统应同步采集最新的 AI 安全学术论文、技术标准与政策法规等信息」

论文（arXiv）与技术标准（MITRE ATLAS）已在 P1 覆盖，
本采集器补齐第三类——政策法规。

数据源选择
----------
政策法规没有像 NVD 那样的统一 API，各机构发布格式各异，
故采用「官方 RSS/Atom + 权威机构站点」混合策略：

  国内监管（合规视角）：
    - 国家网信办官网（网络安全审查、算法备案等）
    - 全国标准信息公共服务平台（AI 相关国标）
    - 中国网络安全审查网

  国际标准与法规：
    - NIST（AI 风险管理框架、生成式 AI 指南）
    - ENISA（欧盟 AI 安全指引）
    - OWASP（AI 安全实践，兼具标准属性）

设计要点
--------
政策法规与漏洞情报的性质不同（无 CVE、无 CVSS），
因此：
  ① 统一用 extra.is_policy 标记，与漏洞情报区分
  ② 提取法规编号（如 GB/T、EU AI Act Article）便于溯源
  ③ 分类维度：法律/法规/标准/指南/政策，与赛题四分类不同
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from ..后端服务.logger import get_logger
from .base_collector import BaseCollector, RawItem

logger = get_logger("collector.policy")

# 政策法规数据源
# 说明：各机构的 RSS 稳定性不一，采集器对失败源做静默跳过，
# 保证部分可用即可产出数据。
POLICY_SOURCES = [
    # 说明：政策类 RSS 无统一标准，且机构站点改版频繁。
    # 实测 OWASP/ENISA/网信办的 RSS 均已失效（404），
    # 故改用「权威机构综合源 + 严格关键词过滤」的策略：
    # 源本身可靠，AI 政策相关性由 normalize 的关键词判定保证。
    {
        "code": "nist",
        "name": "NIST（美国国家标准与技术研究院）",
        "url": "https://www.nist.gov/news-events/news/rss.xml",
        "org": "NIST",
        "region": "美国",
        "kind": "标准",
    },
    {
        "code": "darkreading_policy",
        "name": "DarkReading 安全政策观察",
        "url": "https://www.darkreading.com/rss.xml",
        "org": "DarkReading",
        "region": "国际",
        "kind": "政策",
    },
    {
        "code": "schneier",
        "name": "Schneier on Security（安全政策评论）",
        "url": "https://www.schneier.com/feed/atom/",
        "org": "Schneier on Security",
        "region": "国际",
        "kind": "观点",
    },
]

# 美国联邦公报官方 API——本采集器最重要的法规原文源。
#
# 为什么必须有它：原先三个源都是「新闻/观点」类（NIST 新闻动态、
# DarkReading 报道、Schneier 评论），入库 15 条里没有一条是真正的法规条文。
# 后果是问「欧盟 AI 法案有哪些合规要求」时，语义检索命中的是
# "Unsloth Studio Flaw..." 这类新闻，相似度仅 0.42~0.48，答非所问。
#
# 联邦公报提供的是正式立法/规制文件原文（Rule / Proposed Rule /
# Presidential Document），带机构、文件编号、发布日期，
# 权威性与结构化程度都远高于新闻源。
FEDERAL_REGISTER_API = (
    "https://www.federalregister.gov/api/v1/documents.json"
    "?conditions[type][]=RULE"
    "&conditions[type][]=PRORULE"
    "&conditions[type][]=PRESDOCU"
    "&conditions[term]=%22artificial+intelligence%22"
    "&per_page=100"
    "&order=newest"
    "&fields[]=title&fields[]=abstract&fields[]=html_url"
    "&fields[]=publication_date&fields[]=agencies&fields[]=document_number"
    "&fields[]=type&fields[]=action"
)

# 联邦公报本地二次过滤用的 AI 术语表。
#
# 为什么必须本地过滤：API 的 term 是全文检索，噪声很大——实测召回
# 「Veterans' Benefits」「Declaration of Independence」这类文件
# （它们只是在几十页长文里出现过 AI 一词），入库后会把政策库污染成新闻堆。
#
# 为什么用宽泛词表而非只认 "artificial intelligence"：
# 法规标题里的 AI 表述五花八门（AI Diffusion、Algorithmic Transparency、
# Foundation Model 等），只认一个词会漏掉真正相关的法规。
# 实测按标题过滤，100 条召回里约 8 条真正相关——比例低但准确率高。
AI_TERMS_FOR_FILTER = (
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "neural network",
    "foundation model",
    "generative ai",
    "large language model",
    "ai diffusion",
    "ai model",
    "ai system",
    "ai innovation",
    "ai security",
    "ai safety",
    "algorithm transparency",
    "automated decision",
    "automated decision-making",
)

# AI 安全相关的主题词——用于从综合 RSS 中筛出 AI 政策条目
# 政策类关键词：需同时具备「AI 主题」与「政策属性」，
# 否则综合新闻源会混入大量纯技术内容，偏离赛题要求的政策法规定位。
AI_TOPIC_KEYWORDS = [
    "artificial intelligence", " ai ", "a.i.", "machine learning",
    "generative ai", "large language model", "llm", "algorithm",
    "人工智能", "生成式", "大模型", "算法", "深度合成", "智能体",
]

POLICY_ASPECT_KEYWORDS = [
    # 监管与合规
    "regulation", "regulatory", "governance", "compliance", "law",
    "legislation", "act", "policy", "framework", "standard", "guideline",
    "监管", "合规", "立法", "法规", "政策", "治理", "标准", "指引",
    "备案", "审查", "认证",
    # 具体法规名
    "ai act", "executive order", "nist ai", "iso 42001", "iso/iec",
    "eu ai act", "人工智能法", "生成式人工智能", "数据安全法",
    "网络安全法", "个人信息保护法", "算法推荐", "网信",
]

# 法规编号识别
POLICY_ID_PATTERNS = [
    re.compile(r"GB(?:/T)?\s*\d{4,5}(?:[.-]\d{4})?"),  # GB/T 35273-2020
    re.compile(r"EU\s*AI\s*Act", re.I),
    re.compile(r"Article\s*\d+", re.I),
    re.compile(r"ISO[/I]?\s*\d{4,5}(?::\d{4})?"),  # ISO/IEC 42001
    re.compile(r"NIST\s*(?:AI\s*)?\d{3}(?:-\d)?"),  # NIST AI 600-1
    re.compile(r"AIOps|ACSF"),  # 等保/安全框架
]


class PolicyCollector(BaseCollector):
    """
    政策法规采集器

    赛题三类知识底座中的「政策法规」类，与论文、技术标准并列。
    """

    code = "policy"
    name = "AI 安全政策法规"
    category = "政策法规"
    homepage = "https://www.nist.gov/itl/ai-risk-management-framework"
    default_interval = 43200  # 12 小时
    # 政策法规是静态页面、发布节奏以周/月计，不存在小时级时效要求。
    # 6 小时已远够；上限 1 天避免形同虚设。
    min_interval = 21600
    max_interval = 86400
    interval_basis = "政策法规为静态页面，发布节奏以周/月计，无小时级时效要求"
    ai_native = True

    def __init__(self) -> None:
        super().__init__()
        self._sources = POLICY_SOURCES

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        并发拉取各机构的政策动态

        政策 RSS 格式不统一，用 feedparser 统一处理；
        部分源会失败（404/格式变更），静默跳过以保证部分可用。
        """
        import feedparser

        client = await self.get_client()
        results: list[dict[str, Any]] = []

        async def _fetch_one(src: dict[str, str]) -> None:
            try:
                resp = await client.get(src["url"], timeout=30.0)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries:
                    results.append(
                        {
                            "source": src,
                            "title": entry.get("title", ""),
                            "summary": entry.get("summary", ""),
                            "link": entry.get("link", ""),
                            "published": entry.get("published")
                            or entry.get("updated"),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                self.log.debug("policy_source_failed", code=src["code"], error=str(exc)[:100])

        await asyncio.gather(*[_fetch_one(s) for s in self._sources])

        # 联邦公报 API（法规原文，非新闻）
        await self._fetch_federal_register(results)

        self.log.info("policy_fetched", raw=len(results), sources=len(self._sources))
        return results

    async def _fetch_federal_register(self, results: list[dict[str, Any]]) -> None:
        """
        拉取美国联邦公报的 AI 相关正式文件

        与 RSS 源不同，这里返回的是法规原文条目：
        type 区分 Rule / Proposed Rule / Presidential Document，
        document_number 即法规编号，agencies 给出发布机构。
        这些字段让政策条目具备可核验的溯源信息。
        """
        try:
            client = await self.get_client()
            resp = await client.get(FEDERAL_REGISTER_API, timeout=40.0)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            # 单源失败不影响整体，政策库仍可由 RSS 源产出
            self.log.debug("federal_register_failed", error=str(exc)[:150])
            return

        type_label = {
            "RULE": "法规",
            "PRORULE": "法规草案",
            "PRESDOCU": "总统令",
            "NOTICE": "通告",
        }
        for doc in payload.get("results", []):
            title = (doc.get("title") or "").strip()
            if not title:
                continue
            abstract = (doc.get("abstract") or "").strip()
            # API 的 term 匹配是全文检索，噪声不小——实测召回
            # 「Veterans' Benefits」「Declaration of Independence」等
            # 与 AI 毫无关系的文件（它们只是在长文本里出现过 AI 一词）。
            # 故在本地做一次严格过滤。
            # 标题与摘要都参与匹配：法规文件的 abstract 常为空，
            # 只看标题会漏掉那些标题不含 AI 字样但正文是 AI 法规的文件。
            haystack = f"{title} {abstract}".lower()
            if not any(term in haystack for term in AI_TERMS_FOR_FILTER):
                continue
            agencies = doc.get("agencies") or []
            org = agencies[0].get("name") if agencies else "Federal Register"
            results.append(
                {
                    "source": {
                        "code": "federal_register",
                        "name": "美国联邦公报（Federal Register）",
                        "org": org,
                        "region": "美国",
                        "kind": "法规",
                    },
                    "title": title,
                    # 联邦公报的 abstract 字段常为空，用 action 补位，
                    # 保证摘要不为空（否则检索文本只剩标题，语义过弱）
                    "summary": abstract
                    or f"Document type: {doc.get('type', '')}. "
                    f"Action: {doc.get('action') or 'N/A'}. "
                    f"Agency: {org}.",
                    "link": doc.get("html_url"),
                    "published": doc.get("publication_date"),
                    "doc_number": doc.get("document_number"),
                    "doc_type": type_label.get(doc.get("type", ""), "法规"),
                    "is_regulation": True,
                }
            )

        self.log.info(
            "federal_register_fetched",
            count=len(payload.get("results", [])),
            # count 是匹配总数（整数），不是列表
            total_matches=payload.get("count") or 0,
        )

    def normalize(self, raw: Any) -> list[RawItem]:
        """
        政策条目归一化

        双重判定：必须同时具备「AI 主题」与「政策属性」才收录。
        只判 AI 主题会把大量纯技术新闻混进来，
        偏离赛题要求的政策法规定位。

        遇到明确法规编号（GB/T、EU AI Act 等）时直接收录，
        避免因表述差异漏掉重要的正式文件。
        """
        title = (raw.get("title") or "").strip()
        if not title:
            return []

        from bs4 import BeautifulSoup

        summary_raw = raw.get("summary") or ""
        summary = BeautifulSoup(summary_raw, "lxml").get_text(" ").strip()

        full_text = f"{title} {summary}".lower()

        # 提取法规编号——命中说明是正式法规/标准，优先收录
        doc_numbers: list[str] = []
        for pattern in POLICY_ID_PATTERNS:
            for m in pattern.finditer(full_text):
                num = m.group(0).strip()
                if num not in doc_numbers:
                    doc_numbers.append(num)

        has_topic = any(k in full_text for k in AI_TOPIC_KEYWORDS)
        has_aspect = any(k in full_text for k in POLICY_ASPECT_KEYWORDS)

        src = raw.get("source", {})
        is_regulation = bool(raw.get("is_regulation"))

        # 收录条件：
        #   ① 正式法规文件（联邦公报 API 已在服务端按 AI 主题过滤过）→ 直接收录
        #   ② 或带明确法规编号
        #   ③ 或同时具备 AI 主题词与政策属性词
        # 之所以给法规文件开直通道：API 查询已限定 type 为 RULE/PRORULE/PRESDOCU
        # 且关键词为 artificial intelligence，不需要再用标题关键词二次过滤——
        # 法规标题常写成 "Rule on..." 这类不含中文政策词的形式，
        # 若沿用双重判定会把真法规全部误杀。
        if not (is_regulation or doc_numbers or (has_topic and has_aspect)):
            return []

        # 分类：法规文件优先用API 提供的类型标签
        if is_regulation:
            kind = raw.get("doc_type") or "法规"
        else:
            kind = src.get("kind", "政策")
            if "GB" in full_text or "国家标准" in title:
                kind = "标准"
            elif "法" in title or "条例" in title or "办法" in title:
                kind = "法律"
            elif "指引" in title or "指南" in title or "指南" in full_text:
                kind = "指南"

        # 联邦公报的文档编号就是法规编号，直接采信
        if raw.get("doc_number") and raw["doc_number"] not in doc_numbers:
            doc_numbers.insert(0, raw["doc_number"])

        # 稳定去重键。不能直接用内置 hash——CPython 对 str 的 hash
        # 启用了随机化种子（PYTHONHASHSEED），每个进程结果都不同，
        # 后果是同一份政策在两次采集中生成不同 dedup_key，
        # upsert 失效，库里出现大量重复条目（实测15 条里有2 条重复）。
        # 改用 sha256 前 12 位，跨进程稳定。
        stable_id = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]

        return [
            RawItem(
                dedup_key=f"policy:{src.get('code', 'unknown')}:{stable_id}",
                origin_source=src.get("code", "policy"),
                external_id=stable_id,
                title=title,
                description=summary[:1500],
                source_name=src.get("name", "政策源"),
                source_url=raw.get("link"),
                published_at=self._parse_rfc822(raw.get("published")),
                extra={
                    "is_policy": True,
                    "is_news": True,  # 复用入库层的新闻分流路径
                    "org": src.get("org"),
                    "region": src.get("region"),
                    "policy_kind": kind,
                    "doc_numbers": doc_numbers,
                    "ai_keywords": [
                        k.strip()
                        for k in AI_TOPIC_KEYWORDS + POLICY_ASPECT_KEYWORDS
                        if k in full_text
                    ][:5],
                },
            )
        ]

    @staticmethod
    def _parse_rfc822(value: str | None):
        """
        解析时间字符串

        两种格式都要处理：
        ① RSS 的 RFC822（`Mon, 05 Oct 2026 12:00:00 GMT`）
        ② 联邦公报的纯日期（`2026-10-05`）——RFC822 解析会直接失败，
           若不处理会导致全部法规的发布时间为 None，排序与时效性指标失真
        """
        if not value:
            return None
        text = value.strip()
        # 先试ISO 日期格式
        try:
            from datetime import date, datetime, UTC

            if len(text) == 10 and text[4] == "-" and text[7] == "-":
                return datetime.combine(
                    date(int(text[:4]), int(text[5:7]), int(text[8:10])),
                    datetime.min.time(),
                    tzinfo=UTC,
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                # -0000（未知偏移）会解析出 naive datetime——asyncpg 存
                # timestamptz 时按本机本地时区(+08)换算，published_at
                # 会早 8 小时。按 UTC 兜底（与 _parse_iso 同一修复）。
                dt = dt.replace(tzinfo=UTC)
            return dt
        except Exception:  # noqa: BLE001
            return None
