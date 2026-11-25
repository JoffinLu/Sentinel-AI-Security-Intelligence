"""
混合检索引擎

赛题要求pgvector 语义检索 + 全文检索，RRF 融合排序。
单靠任一通道都有明显短板：

  关键词检索（PostgreSQL ILIKE / trigram）
    优点：精确、可解释、对 CVE 编号与专有名词命中好
    缺点：同义表达覆盖为零——用户问"提示词注入"搜不到标题写
          "LLM Prompt Injection" 的漏洞；数据主体是英文，
          中文查询几乎无法命中

  语义检索（pgvector + bge-small-zh）
    优点：跨语言、跨表述，中文问句能命中英文漏洞
    缺点：对精确标识符（CVE-XXXX-YYYY、组件名）反而不如关键词

RRF（Reciprocal Rank Fusion）融合公式：

    score(d) = Σ 1 / (k + rank_i(d))

其中 rank_i(d) 是文档 d 在第 i 个检索通道中的排名（从 1 开始），
k 为平滑常数（取 60，是信息检索领域的经验值，用于抑制头部排名的影响）。

RRF 只用排名不用分数，天然规避了「余弦相似度 0-1」与
「BM25 无上界」这两类分数不可比的问题——这是选它的主要原因。

时间感知加权：
    近期披露的漏洞对情报问答更有价值，故对时间较新的结果施加
    轻微加权（1.0 ~ 1.15），避免压过语义相关性主判断。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from ..后端服务.logger import get_logger
from ..知识库.database import get_session
from ..知识库.models import Paper, Policy, Vulnerability
from .embedding import get_embedding_service

logger = get_logger("retrieval")

# RRF 平滑常数（信息检索领域经验值）
RRF_K = 60

# 单通道召回数
CHANNEL_TOP_K = 30

# 最终返回条数
FINAL_TOP_K = 8


# CVE / GHSA 编号提取（用于精确匹配）
_ID_PATTERN = re.compile(r"\b(?:CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})\b", re.I)


def _extract_cve_ids(text: str) -> list[str]:
    """从文本中提取漏洞编号（统一大写）"""
    return [m.group(0).upper() for m in _ID_PATTERN.finditer(text or "")]


@dataclass(slots=True)
class SearchHit:
    """单条检索结果"""

    cve_id: str
    title: str
    description: str
    score: float  # 融合后的最终得分
    rank_vector: int | None = None  # 在向量通道的排名
    rank_keyword: int | None = None  # 在关键词通道的排名
    similarity: float | None = None  # 向量相似度（仅参考）
    source: str = ""
    severity: str | None = None
    cvss_score: float | None = None
    ai_category: str | None = None
    ai_relevance: float = 0.0
    published_at: str | None = None
    source_url: str | None = None
    latency_seconds: int | None = None
    # 溯源信息——支撑前端「点击查看来源」
    origin_source: str | None = None
    ingest_at: str | None = None
    # 匹配依据，用于向用户解释「为什么召回这条」
    match_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cve_id": self.cve_id,
            "title": self.title,
            "description": self.description[:400],
            "score": round(self.score, 4),
            "rank_vector": self.rank_vector,
            "rank_keyword": self.rank_keyword,
            "similarity": round(self.similarity, 3) if self.similarity is not None else None,
            "source": self.source,
            "severity": self.severity,
            "cvss_score": self.cvss_score,
            "ai_category": self.ai_category,
            "ai_relevance": round(self.ai_relevance, 3),
            "published_at": self.published_at,
            "url": self.source_url,
            "latency_seconds": self.latency_seconds,
            "origin_source": self.origin_source,
            "ingest_at": self.ingest_at,
            "match_reason": self.match_reason,
        }


@dataclass(slots=True)
class RetrievalResult:
    """检索结果汇总"""

    query: str
    hits: list[SearchHit] = field(default_factory=list)
    elapsed_ms: int = 0
    vector_used: bool = False
    keyword_used: bool = True
    total_candidates: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "hits": [h.to_dict() for h in self.hits],
            "elapsed_ms": self.elapsed_ms,
            "vector_used": self.vector_used,
            "keyword_used": self.keyword_used,
            "total_candidates": self.total_candidates,
        }


class HybridRetriever:
    """
    混合检索器

    双通道并行 + RRF 融合：
      ① 向量通道：pgvector 余弦相似度（跨语言、跨表述）
      ② 关键词通道：PostgreSQL 模糊匹配（精确标识符命中好）

    任一通道不可用时自动降级为单通道，不影响整体可用性。
    """

    def __init__(self) -> None:
        self.embedding = get_embedding_service()

    # ---------- 关键词通道 ----------

    async def _keyword_search(
        self, query: str, top_k: int = CHANNEL_TOP_K
    ) -> list[tuple[str, int]]:
        """
        关键词检索

        三种匹配策略，优先级从高到低：

        ① 精确 ID 匹配——查询含 CVE/GHSA 编号时走这条。
           编号是唯一标识，用模糊匹配既慢又会漏——
           实测 `CVE-2026-12372` 用 ILIKE 查不到（标题里编号被截断），
           但等值查询立即命中。
        ② 整串模糊匹配——多词查询用整串保证短语完整性
        ③ 分词 OR 匹配——单句查询拆词后用 OR 扩大召回

        返回 [(cve_id, rank), ...]
        """
        q = (query or "").strip()
        if not q:
            return []

        async with get_session() as session:
            # ---------- 策略 ①：精确 ID ----------
            ids = _extract_cve_ids(q)
            if ids:
                stmt = (
                    select(Vulnerability.cve_id)
                    .where(Vulnerability.cve_id.in_(ids))
                    .order_by(Vulnerability.ai_relevance_score.desc())
                    .limit(top_k)
                )
                rows = (await session.execute(stmt)).scalars().all()
                if rows:
                    return [(cve_id, idx + 1) for idx, cve_id in enumerate(rows)]

            # ---------- 策略 ②③：模糊匹配 ----------
            terms = [t for t in q.replace(",", " ").split() if len(t) > 1]
            conditions = []

            if len(terms) > 1:
                # 多词：整串（短语）+ 分词 OR 并用，兼顾精度与召回
                conditions.append(
                    or_(
                        Vulnerability.title.ilike(f"%{q}%"),
                        Vulnerability.description.ilike(f"%{q}%"),
                    )
                )
                for term in terms[:5]:
                    pattern = f"%{term}%"
                    conditions.append(
                        or_(
                            Vulnerability.title.ilike(pattern),
                            Vulnerability.description.ilike(pattern),
                        )
                    )
            else:
                pattern = f"%{q}%"
                conditions.append(
                    or_(
                        Vulnerability.title.ilike(pattern),
                        Vulnerability.description.ilike(pattern),
                        Vulnerability.cve_id.ilike(pattern),
                    )
                )

            stmt = (
                select(Vulnerability.cve_id)
                .where(or_(*conditions))
                .order_by(Vulnerability.ai_relevance_score.desc())
                .limit(top_k)
            )
            rows = (await session.execute(stmt)).scalars().all()

        return [(cve_id, idx + 1) for idx, cve_id in enumerate(rows)]

    # ---------- 向量通道 ----------

    async def _vector_search(
        self, query: str, top_k: int = CHANNEL_TOP_K
    ) -> list[tuple[str, int, float]]:
        """
        向量检索

        用 pgvector 的余弦距离运算符 `<=>`。
        返回 [(cve_id, rank, similarity), ...]
        """
        vec = self.embedding.encode_query(query)
        if vec is None:
            return []

        async with get_session() as session:
            distance_expr = Vulnerability.embedding.cosine_distance(vec)
            stmt = (
                select(
                    Vulnerability.cve_id,
                    distance_expr.label("dist"),
                )
                .where(Vulnerability.embedding.isnot(None))
                .order_by(distance_expr)
                .limit(top_k)
            )
            rows = (await session.execute(stmt)).all()

        # 距离转相似度：cosine_dist 越小越相似，相似度 = 1 - dist
        return [
            (cve_id, idx + 1, max(0.0, 1.0 - float(dist)))
            for idx, (cve_id, dist) in enumerate(rows)
        ]

    # ---------- 融合 ----------

    @staticmethod
    def rrf_fuse(
        vector_hits: list[tuple[str, int, float]],
        keyword_hits: list[tuple[str, int]],
        recency_boost: dict[str, float] | None = None,
    ) -> list[tuple[str, float, int | None, int | None, float | None]]:
        """
        RRF 融合排序

        Args:
            vector_hits: [(cve_id, rank, similarity), ...]
            keyword_hits: [(cve_id, rank), ...]
            recency_boost: {cve_id: 权重倍数}

        Returns:
            按融合得分降序排列的
            [(cve_id, score, vector_rank, keyword_rank, similarity), ...]
        """
        scores: dict[str, float] = {}
        v_rank: dict[str, int] = {}
        k_rank: dict[str, int] = {}
        sims: dict[str, float] = {}

        for cve_id, rank, sim in vector_hits:
            scores[cve_id] = scores.get(cve_id, 0.0) + 1.0 / (RRF_K + rank)
            v_rank[cve_id] = rank
            sims[cve_id] = sim

        for cve_id, rank in keyword_hits:
            scores[cve_id] = scores.get(cve_id, 0.0) + 1.0 / (RRF_K + rank)
            k_rank[cve_id] = rank

        # 时间加权（轻微，避免压过语义相关性）
        if recency_boost:
            for cve_id, weight in recency_boost.items():
                if cve_id in scores:
                    scores[cve_id] *= weight

        fused = [
            (cve_id, score, v_rank.get(cve_id), k_rank.get(cve_id), sims.get(cve_id))
            for cve_id, score in scores.items()
        ]
        fused.sort(key=lambda x: x[1], reverse=True)
        return fused

    # ---------- 主入口 ----------

    async def search(
        self,
        query: str,
        top_k: int = FINAL_TOP_K,
        ai_only: bool = False,
        use_vector: bool = True,
        use_keyword: bool = True,
    ) -> RetrievalResult:
        """
        混合检索主入口

        Args:
            query: 查询串（支持中英文）
            top_k: 返回条数
            ai_only: 仅检索 AI 安全相关
            use_vector: 启用向量通道
            use_keyword: 启用关键词通道
        """
        start = time.perf_counter()
        result = RetrievalResult(query=query)

        # ---------- 短路：查询含漏洞编号时直接精确匹配 ----------
        # 编号是唯一标识，此时语义检索只会引入噪声——
        # 实测「CVE-2026-12372」走向量通道会被当成普通文本，
        # 召回一堆编号相近但完全无关的漏洞。
        ids = _extract_cve_ids(query)
        if ids:
            async with get_session() as session:
                stmt = (
                    select(Vulnerability)
                    .where(Vulnerability.cve_id.in_(ids))
                    .limit(top_k)
                )
                rows = (await session.execute(stmt)).scalars().all()

            if rows:
                for idx, v in enumerate(rows):
                    result.hits.append(
                        SearchHit(
                            cve_id=v.cve_id,
                            title=v.title or "",
                            description=v.description or "",
                            # 精确命中给满分，避免被语义结果压过
                            score=1.0 - idx * 0.01,
                            rank_keyword=idx + 1,
                            source=v.source_name or "",
                            severity=v.severity,
                            cvss_score=v.cvss_score,
                            ai_category=v.ai_category,
                            ai_relevance=v.ai_relevance_score or 0.0,
                            published_at=v.published_at.isoformat() if v.published_at else None,
                            source_url=v.source_url,
                            latency_seconds=v.collect_latency_seconds,
                            origin_source=v.origin_source,
                            ingest_at=v.ingest_at.isoformat() if v.ingest_at else None,
                            match_reason="编号精确匹配",
                        )
                    )
                result.total_candidates = len(rows)
                result.elapsed_ms = int((time.perf_counter() - start) * 1000)
                logger.info(
                    "exact_id_shortcut", query=query, matched=len(rows)
                )
                return result

        # ---------- 常规：双通道并行 ----------
        vector_hits: list[tuple[str, int, float]] = []
        keyword_hits: list[tuple[str, int]] = []

        if use_vector and self.embedding.available:
            try:
                vector_hits = await self._vector_search(query)
                result.vector_used = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("vector_channel_failed", error=str(exc)[:150])

        if use_keyword:
            try:
                keyword_hits = await self._keyword_search(query)
                result.keyword_used = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("keyword_channel_failed", error=str(exc)[:150])

        result.total_candidates = len({h[0] for h in vector_hits} | {h[0] for h in keyword_hits})

        if not vector_hits and not keyword_hits:
            result.elapsed_ms = int((time.perf_counter() - start) * 1000)
            return result

        # 时间权重：7 天内 1.15，30 天内 1.08，90 天内 1.03，更早 1.0
        recency = await self._recency_boost({h[0] for h in vector_hits} | {h[0] for h in keyword_hits})

        fused = self.rrf_fuse(vector_hits, keyword_hits, recency)

        # 取 top_k 并加载详情
        top_ids = [f[0] for f in fused[:top_k]]
        details = await self._load_details(top_ids, ai_only)

        for cve_id, score, v_rank, k_rank, sim in fused[:top_k]:
            d = details.get(cve_id)
            if d is None:
                continue
            hit = SearchHit(
                cve_id=cve_id,
                title=d.get("title") or "",
                description=d.get("description") or "",
                score=score,
                rank_vector=v_rank,
                rank_keyword=k_rank,
                similarity=sim,
                source=d.get("source_name") or "",
                severity=d.get("severity"),
                cvss_score=d.get("cvss_score"),
                ai_category=d.get("ai_category"),
                ai_relevance=d.get("ai_relevance") or 0.0,
                published_at=d.get("published_at"),
                source_url=d.get("source_url"),
                latency_seconds=d.get("collect_latency_seconds"),
                origin_source=d.get("origin_source"),
                ingest_at=d.get("ingest_at"),
                match_reason=self._explain(v_rank, k_rank, sim),
            )
            result.hits.append(hit)

        result.elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "hybrid_search_done",
            query_len=len(query),
            vector_hits=len(vector_hits),
            keyword_hits=len(keyword_hits),
            returned=len(result.hits),
            elapsed_ms=result.elapsed_ms,
        )
        return result

    async def _recency_boost(self, cve_ids: set[str]) -> dict[str, float]:
        """计算时间权重"""
        if not cve_ids:
            return {}
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(Vulnerability.cve_id, Vulnerability.published_at).where(
                        Vulnerability.cve_id.in_(cve_ids)
                    )
                )
            ).all()

        now = datetime.now(UTC)
        boost: dict[str, float] = {}
        for cve_id, published in rows:
            if published is None:
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            days = (now - published).days
            if days <= 7:
                boost[cve_id] = 1.15
            elif days <= 30:
                boost[cve_id] = 1.08
            elif days <= 90:
                boost[cve_id] = 1.03
        return boost

    async def _load_details(
        self, cve_ids: list[str], ai_only: bool
    ) -> dict[str, dict[str, Any]]:
        """加载命中条目的完整信息（用于生成答案与溯源）"""
        if not cve_ids:
            return {}
        async with get_session() as session:
            stmt = select(Vulnerability).where(Vulnerability.cve_id.in_(cve_ids))
            if ai_only:
                stmt = stmt.where(Vulnerability.ai_relevance_score >= 0.5)
            rows = (await session.execute(stmt)).scalars().all()

        return {
            v.cve_id: {
                "title": v.title,
                "description": v.description,
                "source_name": v.source_name,
                "severity": v.severity,
                "cvss_score": v.cvss_score,
                "ai_category": v.ai_category,
                "ai_relevance": v.ai_relevance_score,
                "published_at": v.published_at.isoformat() if v.published_at else None,
                "source_url": v.source_url,
                "collect_latency_seconds": v.collect_latency_seconds,
                "origin_source": v.origin_source,
                "ingest_at": v.ingest_at.isoformat() if v.ingest_at else None,
            }
            for v in rows
        }

    @staticmethod
    def _explain(
        v_rank: int | None, k_rank: int | None, sim: float | None
    ) -> str:
        """生成匹配依据说明——让用户理解为什么召回这条"""
        reasons: list[str] = []
        if v_rank is not None:
            reasons.append(f"语义相似（第{v_rank}位，相似度 {sim:.2f}）" if sim else f"语义相似（第{v_rank}位）")
        if k_rank is not None:
            reasons.append(f"关键词命中（第{k_rank}位）")
        return " + ".join(reasons) if reasons else "召回"

    # ---------- 论文检索 ----------

    async def search_papers(
        self, query: str, top_k: int = 5
    ) -> list[dict[str, Any]]:
        """检索学术论文（支撑问答的理论支撑与延伸阅读）"""
        vec = self.embedding.encode_query(query)
        hits: list[tuple[str, float]] = []

        if vec is not None:
            try:
                async with get_session() as session:
                    dist = Paper.embedding.cosine_distance(vec)
                    rows = (
                        await session.execute(
                            select(Paper.arxiv_id, Paper.title, dist.label("d"))
                            .where(Paper.embedding.isnot(None))
                            .order_by(dist)
                            .limit(top_k)
                        )
                    ).all()
                    hits = [(r[0], max(0.0, 1.0 - float(r[2]))) for r in rows]
            except Exception as exc:  # noqa: BLE001
                logger.warning("paper_vector_search_failed", error=str(exc)[:150])

        if not hits:
            # 降级为关键词
            try:
                async with get_session() as session:
                    pattern = f"%{query}%"
                    rows = (
                        await session.execute(
                            select(Paper.arxiv_id)
                            .where(
                                or_(
                                    Paper.title.ilike(pattern),
                                    Paper.abstract.ilike(pattern),
                                )
                            )
                            .limit(top_k)
                        )
                    ).scalars().all()
                    hits = [(r, 0.0) for r in rows]
            except Exception as exc:  # noqa: BLE001
                logger.warning("paper_keyword_search_failed", error=str(exc)[:150])

        if not hits:
            return []

        ids = [h[0] for h in hits]
        sim_map = dict(hits)

        async with get_session() as session:
            rows = (
                await session.execute(select(Paper).where(Paper.arxiv_id.in_(ids)))
            ).scalars().all()
            details = {p.arxiv_id: p for p in rows}

        out = []
        for aid in ids:
            p = details.get(aid)
            if p is None:
                continue
            out.append(
                {
                    "arxiv_id": p.arxiv_id,
                    "title": p.title,
                    "authors": (p.authors or [])[:3],
                    "ai_topics": p.ai_topics or [],
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                    "pdf_url": p.pdf_url,
                    "similarity": round(sim_map.get(aid, 0.0), 3),
                }
            )
        return out

    async def search_policies(
        self, query: str, top_k: int = 5
    ) -> list[dict[str, Any]]:
        """
        检索政策法规（赛题赛题三类知识底座之一）

        独立于主检索通道：政策类问题（「欧盟 AI 法案有哪些要求」
        「NIST 对生成式 AI 有什么指引」）如果走漏洞主检索，
        语义完全对不上——漏洞描述里不会提法规名称。
        """
        vec = self.embedding.encode_query(query)
        hits: list[tuple[str, float]] = []

        if vec is not None:
            try:
                async with get_session() as session:
                    dist = Policy.embedding.cosine_distance(vec)
                    rows = (
                        await session.execute(
                            select(Policy.doc_key, Policy.title, dist.label("d"))
                            .where(Policy.embedding.isnot(None))
                            .order_by(dist)
                            .limit(top_k)
                        )
                    ).all()
                    hits = [(r[0], max(0.0, 1.0 - float(r[2]))) for r in rows]
            except Exception as exc:  # noqa: BLE001
                logger.warning("policy_vector_search_failed", error=str(exc)[:150])

        if not hits:
            # 向量不可用或全不命中时降级为关键词，
            # 政策标题里通常含法规名与机构名，关键词匹配相当有效
            try:
                async with get_session() as session:
                    pattern = f"%{query}%"
                    rows = (
                        await session.execute(
                            select(Policy.doc_key)
                            .where(
                                or_(
                                    Policy.title.ilike(pattern),
                                    Policy.summary.ilike(pattern),
                                )
                            )
                            .limit(top_k)
                        )
                    ).scalars().all()
                    hits = [(r, 0.0) for r in rows]
            except Exception as exc:  # noqa: BLE001
                logger.warning("policy_keyword_search_failed", error=str(exc)[:150])

        if not hits:
            return []

        ids = [h[0] for h in hits]
        sim_map = dict(hits)

        async with get_session() as session:
            rows = (
                await session.execute(select(Policy).where(Policy.doc_key.in_(ids)))
            ).scalars().all()
            details = {p.doc_key: p for p in rows}

        out: list[dict[str, Any]] = []
        for key in ids:
            p = details.get(key)
            if p is None:
                continue
            out.append(
                {
                    "doc_key": p.doc_key,
                    "title": p.title,
                    "summary": (p.summary or "")[:400],
                    "policy_kind": p.policy_kind,
                    "org": p.org,
                    "region": p.region,
                    "doc_numbers": p.doc_numbers or [],
                    "source_url": p.source_url,
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                    "similarity": round(sim_map.get(key, 0.0), 3),
                }
            )
        return out


# 全局单例
_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    """获取检索器单例"""
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever
