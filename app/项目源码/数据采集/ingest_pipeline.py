"""
采集入库流水线

把「采集 → AI 相关性分类 → 去重 → 入库 → 游标推进 → 日志记录」串成完整链路。
这是 P1 的核心执行单元，被 API 与定时调度共同调用。

关键设计：
- 跨源去重：同一 CVE 被 NVD、GHSA、OSV 重复采集时按 dedup_key 合并，
  并记录全部来源（而非丢弃），保证情报的溯源完整性
- 时效性埋点：记录 published_at → ingest_at 的延迟，供赛题性能指标举证
- 分流：论文与技术库分别入 papers / techniques 表
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..后端服务 import metrics
from ..后端服务.logger import get_logger
from ..知识库.database import get_session
from ..知识库.models import (
    Asset,
    CollectionLog,
    CollectorCursor,
    Paper,
    Policy,
    Source,
    Technique,
    Vulnerability,
)
from .ai_classifier import ClassificationResult, get_classifier
from .base_collector import BaseCollector, CollectorResult, RawItem
from .registry import effective_interval

logger = get_logger("pipeline")

# 字段长度上限，与 models.py 中的定义保持一致。
# 真实数据（CISA KEV 的 product 字段可达数百字符）常超出直觉长度，
# 故在入库前统一截断，避免单条脏数据导致整批事务回滚。
# 采集明细采样上限：每次采集最多记多少个条目 ID。
# 不设限的话单次可到数千条，JSONB 会变得很大且前端渲染也会卡。
_SAMPLE_LIMIT = 30
_RECORD_LIMIT = 10000


FIELD_LIMITS = {
    "title": 1024,
    "source_name": 256,
    "source_url": 1024,
    "cpe": 512,
    "vendor": 256,
    "product": 512,
    "ecosystem": 64,
    "version_start": 128,
    "version_end": 128,
    "version_constraint": 512,
    # 去重键上限，与 vulnerabilities.cve_id 一致
    "dedup_key": 128,
    # ⚠ cvss_vector 曾是唯一漏网的 128 宽列——
    # 它和 cve_id 同为128，但裁剪清单里漏了它。
    # 虽然实测最长仅 67 字符（CVSS 向量串天然短），
    # 但「实测没超」不等于「保证不超」：不同源可能带
    # 自定义前缀/后缀。故与其赌数据，不如纳入同一套裁剪。
    "cvss_vector": 128,
    # content_hash 是 sha256.hexdigest，恒定 64 字符，
    # 与列宽 String(64) 精确相等——**永远不会溢出**，
    # 无需裁剪（加在这里反而是误导）。
}


def _clip(value: Any, limit: int) -> Any:
    """按字段上限截断字符串"""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def _clip_asset(asset: dict[str, Any]) -> dict[str, Any]:
    """截断资产字典中的超长字段"""
    return {k: _clip(v, FIELD_LIMITS[k]) for k, v in asset.items() if k in FIELD_LIMITS}


def _safe_dedup_key(key: str, limit: int) -> str:
    """把去重键压到长度上限内，且**保证不发生碰撞**

    ---------- 为什么不能直接截断 ----------
    `cve_id` 是跨源去重业务主键。直接 `key[:limit]` 会出问题：
    两个不同的长键若前limit 个字符相同（例如同一组织的
    `poc:owner/repo-a-very-long-suffix...` 与
    `poc:owner/repo-a-another-long-suffix...`），
    截断后会变成**同一个字符串** → 两条不同的情报被误判为
    「同一条」而合并，数据静默丢失。

    ---------- 做法 ----------
    保留可读前缀（便于人工辨认来源），尾部附上原键的短哈希
    （保证不同键几乎不可能碰撞）：
        `poc:owner/repo-name…-<8位hash>`

    这与 Git 的「长路径缩写」是同一个思路：
    **可读性 + 唯一性二者兼得**，而不是牺牲其一。
    """
    if len(key) <= limit:
        return key
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    # 前缀留出 12 字符给「…-hash」后缀
    keep = limit - len(digest) - 2
    return f"{key[:keep]}~{digest}"


# 严重度归一化映射。为什么要在这里做：
# 各数据源对严重度的写法不统一——NVD/GHSA 用大写 CRITICAL/HIGH，
# 而部分源的原始字段直接透传（CVSS v3 的MODERATE、CISA 的首字母大写形式），
# 结果库里同时存在 `Critical`39 条与 `CRITICAL`607 条、`High`73 与 `HIGH`2273。
# 后果是：① 前端按严重度筛选会漏数据 ② 统计图表被拆成两半 ③ 阈值判断失效。
# 在管道入口统一，避免每个采集器各写一套。
_SEVERITY_ALIASES = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "MODERATE": "MEDIUM",  # CVSS v3.1 官方档位名是 MODERATE，统一为 MEDIUM
    "LOW": "LOW",
    "NONE": "NONE",
    "INFORMATIONAL": "LOW",
    "INFO": "LOW",
}


def _normalize_severity(value: Any) -> str | None:
    """把各源五花八门的严重度写法归一为 CRITICAL/HIGH/MEDIUM/LOW/NONE"""
    if not isinstance(value, str) or not value.strip():
        return None
    key = value.strip().upper()
    if key in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[key]
    # 未收录的档位（如 CVSS v2 的 MEDIUM 变体）保留原值大写，
    # 不静默丢弃——避免信息损失，统计里能直接看到有哪些意外值
    return key


@dataclass(slots=True)
class IngestReport:
    """入库结果报告"""

    source_code: str
    fetched: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    ai_security: int = 0
    papers: int = 0
    techniques: int = 0
    policies: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    # 本次采集涉及的条目 ID（最多 _SAMPLE_LIMIT 个）
    #
    # 为什么必须记录：new_count 只是汇总数字，无法追溯「采到了什么」。
    # 而业务表的 ingest_at / modified_at 都靠不住——
    #   · rss 这类源 dedup_key 稳定，重复采集走「合并已有条目」，
    #     不产生新行 → ingest_at 不变
    #   · modified_at 取的是源站发布时间，不是入库时间 → 也不变
    # 实测：声称「新增 72 条」的那次采集，按时间窗反查得到 0 行。
    # 唯一可靠的做法是在入库时就把 ID 记下来。
    sampled_ids: list[str] = field(default_factory=list)
    record_ids: list[str] = field(default_factory=list)
    record_ids_complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_code,
            "fetched": self.fetched,
            "new": self.new,
            "updated": self.updated,
            "skipped": self.skipped,
            "ai_security": self.ai_security,
            "papers": self.papers,
            "techniques": self.techniques,
            "policies": self.policies,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


class IngestPipeline:
    """
    采集入库流水线

    用法：
        pipeline = IngestPipeline()
        report = await pipeline.run(collector, cursor)
    """

    def __init__(self) -> None:
        self.log = logger
        self.classifier = get_classifier()
        # 本次采集涉及的条目 ID（采样），由入库方法累积、
        # 结束时写进 collection_logs.metrics。
        # 用实例属性而非模块级字典：并发采集时不会互相污染，
        # 且天然跟随「一次 run 一个实例」的生命周期。
        self._touched_ids: list[str] = []
        self._record_ids: list[str] = []
        self._record_ids_complete = True

    async def run(
        self,
        collector: BaseCollector,
        cursor: str | None = None,
        classify: bool = True,
        trigger: str = "schedule",
    ) -> IngestReport:
        """
        执行一次完整的采集入库

        Args:
            collector: 采集器实例
            cursor: 增量游标
            classify: 是否执行 AI 相关性分类（论文/技术库自动跳过）
            trigger: 本次执行的触发来源，如实写入 collection_logs.trigger
        """
        # ---------- 断档补偿：开机后补采积压数据 ----------
        # 场景：电脑关机 / 断网 / 进程被杀，N 小时没有采集。
        # 开机后若只按原窗口拉取，中间新增的数据会永久丢失。
        # 这里在采集前算出断档时长，交给采集器放大回溯窗口。
        gap_hours = await self._detect_gap(collector)
        if gap_hours:
            # 加2 小时缓冲：调度间隔本身有浮动，
            # 且不同源的「发布时间」与「入库时间」有小时级偏差。
            collector.gap_lookback_hours = int(gap_hours) + 2
            self.log.info(
                "collect_gap_compensate",
                source=collector.code,
                gap_hours=round(gap_hours, 1),
                lookback_hours=collector.gap_lookback_hours,
            )

        result: CollectorResult = await collector.collect(cursor)
        # 采集结束即清空，避免复用实例时影响下一轮
        collector.gap_lookback_hours = None
        # 每次采集开始时重置采样，避免复用实例时把上次的条目带进来。
        self._touched_ids = []
        self._record_ids = []
        self._record_ids_complete = True
        report = IngestReport(
            source_code=collector.code,
            fetched=result.fetched_count,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )

        # 监控埋点：采集成功率与耗时
        metrics.record_collect(
            collector.code, not result.error, result.fetched_count, result.elapsed_ms
        )

        async with get_session() as session:
            # 记录采集日志（无论成败，赛题性能指标需要完整数据）
            log_id = await self._create_log(session, collector, result, trigger)
            try:
                await self._register_source(session, collector)

                if result.error:
                    report.error = result.error
                else:
                    await self._process_items(
                        session, collector, result.items, report, classify
                    )
                    # 推进游标
                    if result.new_cursor:
                        await self._update_cursor(session, collector.code, result.new_cursor)
            except Exception as exc:  # noqa: BLE001
                report.error = str(exc)[:500]
                self.log.error("ingest_failed", source=collector.code, error=report.error)
            finally:
                # 把本次采集涉及的条目采样交给 report，
                # _finish_log 会写进 collection_logs.metrics。
                # 放在 finally 里：即使中途抛错，已入库的条目也已被记录，
                # 那是**真实发生过的采集**，不该丢。
                report.sampled_ids = list(self._touched_ids)
                report.record_ids = list(dict.fromkeys(self._record_ids))
                report.record_ids_complete = self._record_ids_complete
                await self._finish_log(session, log_id, report)

        self.log.info(
            "ingest_complete",
            source=collector.code,
            fetched=report.fetched,
            new=report.new,
            updated=report.updated,
            ai_security=report.ai_security,
        )
        return report

    # ---------- 内部步骤 ----------

    async def _register_source(self, session: AsyncSession, collector: BaseCollector) -> None:
        """登记数据源元信息"""
        existing = await session.scalar(
            select(Source).where(Source.code == collector.code)
        )
        if existing:
            existing.name = collector.name
            existing.category = collector.category
            # 记**有效周期**而非 default_interval：运维在看板调过之后，
            # 这里要反映实际在跑的周期，否则 sources 表显示的值与
            # 「定时调度」卡显示的周期对不上。
            existing.interval_seconds = effective_interval(collector.code)
        else:
            session.add(
                Source(
                    code=collector.code,
                    name=collector.name,
                    category=collector.category,
                    homepage=collector.homepage,
                    interval_seconds=effective_interval(collector.code),
                    requires_key=collector.requires_key,
                )
            )
        await session.flush()

    async def _process_items(
        self,
        session: AsyncSession,
        collector: BaseCollector,
        items: list[RawItem],
        report: IngestReport,
        classify: bool,
    ) -> None:
        """处理采集到的条目（分流 + 分类 + 入库）"""

        # 分流：技术库 / 论文库 / 政策库 / 资讯/ 漏洞库
        # 政策项必须先于 news 判定——policy_collector 同时打了
        # is_policy 与 is_news 两个标记，若不优先分流会被当成
        # 社区资讯写进 vulnerabilities 表，导致政策数据实际丢失
        tech_items = [i for i in items if i.extra.get("is_technique")]
        paper_items = [i for i in items if i.extra.get("is_paper")]
        policy_items = [i for i in items if i.extra.get("is_policy")]
        news_items = [
            i for i in items
            if i.extra.get("is_news") and not i.extra.get("is_policy")
        ]
        vuln_items = [
            i for i in items
            if not (
                i.extra.get("is_technique")
                or i.extra.get("is_paper")
                or i.extra.get("is_policy")
                or i.extra.get("is_news")
            )
        ]

        # 技术库入库
        if tech_items:
            report.techniques = await self._ingest_techniques(session, tech_items)

        # 论文库入库
        if paper_items:
            report.papers = await self._ingest_papers(session, paper_items)

        # 政策法规库入库（赛题要求的第赛题三类知识底座）
        if policy_items:
            report.policies = await self._ingest_policies(session, policy_items)
        # 社区资讯：以正文形式并入漏洞表（作为情报条目）
        if news_items:
            # 资讯本身已按 AI 主题词过滤，无需再分类，故不返回 AI 计数
            n, _ = await self._ingest_vulnerabilities(session, news_items, None)
            report.new += n

        # 漏洞库：执行 AI 相关性分类
        if vuln_items:
            classifications: list[ClassificationResult] | None = None
            if classify:
                self.log.info("classifying", count=len(vuln_items))
                classifications = await self.classifier.classify_batch(
                    [
                        (i.title, i.description[:600], i.affected_assets)
                        for i in vuln_items
                    ]
                )
            n, ai_n = await self._ingest_vulnerabilities(session, vuln_items, classifications)
            report.new += n
            report.ai_security += ai_n

            # 分类指标埋点（按判定方式与结果分类统计）
            for res in classifications or []:
                metrics.record_classify(res.method, res.is_ai_security)

    async def _ingest_vulnerabilities(
        self,
        session: AsyncSession,
        items: list[RawItem],
        classifications: list[ClassificationResult] | None,
    ) -> tuple[int, int]:
        """
        漏洞/资讯入库（跨源去重合并）

        Returns:
            (新增数, AI 安全条目数)
        """
        new_count = 0
        ai_count = 0
        skipped = 0
        ingest_now = datetime.now(UTC)
        # 记录本次实际处理的条目（新增 + 更新已有）——
        # 汇总数字无法追溯「采到了什么」，前端要展示具体条目。
        touched: list[str] = []

        for idx, item in enumerate(items):
            classification = classifications[idx] if classifications else None
            is_ai = classification.is_ai_security if classification else False

            # 计算采集延迟（赛题性能指标：监测时效性）。
            # 放在 try 之外：它只做算术与指标上报，不会触碰数据库，
            # 不会成为脏数据的来源。
            latency_seconds = None
            if item.published_at:
                published = item.published_at
                if published.tzinfo is None:
                    published = published.replace(tzinfo=UTC)
                latency_seconds = int((ingest_now - published).total_seconds())
                # 仅统计有效延迟（排除负值与超 30 天的异常值）
                if 0 <= latency_seconds <= 2_592_000:
                    metrics.record_ingest_latency(item.origin_source, latency_seconds)

            # ---------- 单条隔离 ----------
            # 为什么要 try：入库是批量事务，一条脏数据（如字段超长、
            # 类型不符）会抛异常并**回滚整批**，导致这次采集全部白跑。
            # 历史故障正是如此：一条超长 cve_id 让整批 OSV 采集回滚，
            # 且被 supervisor 判为「非可恢复错误」永不重试 → 静默丢数据。
            #
            # 这里把每条包进 SAVEPOINT：单条失败只回滚这一条，
            # 其余条目照常入库，脏数据计入 skipped 计数。
            # **宁可少一条，也不要整批丢。**
            nested = await session.begin_nested()
            try:
                await self._ingest_one_vulnerability(
                    session, item, classification, ingest_now, latency_seconds
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整批
                await nested.rollback()
                skipped += 1
                self.log.warning(
                    "ingest_item_skipped",
                    source=item.origin_source,
                    error=str(exc)[:200],
                )
                continue
            else:
                await nested.commit()
                new_count += 1
                if len(self._record_ids) < _RECORD_LIMIT:
                    self._record_ids.append(_safe_dedup_key(item.dedup_key, FIELD_LIMITS['dedup_key']))
                else:
                    self._record_ids_complete = False
                if len(touched) < _SAMPLE_LIMIT:
                    touched.append(_safe_dedup_key(item.dedup_key, FIELD_LIMITS["dedup_key"]))
                if is_ai:
                    ai_count += 1

        # 累积到实例属性（跨 news/vuln 两批入库共用同一份）
        if touched:
            self._touched_ids = (self._touched_ids + touched)[:_SAMPLE_LIMIT]

        if skipped:
            self.log.warning(
                "ingest_partial_failure",
                source=items[0].origin_source if items else "?",
                skipped=skipped,
                total=len(items),
            )

        return new_count, ai_count

    async def _ingest_one_vulnerability(
        self,
        session: AsyncSession,
        item: RawItem,
        classification: ClassificationResult | None,
        ingest_now: datetime,
        latency_seconds: int | None,
    ) -> None:
        """入库单条漏洞情报（含跨源去重合并）

        单条失败由调用方通过 SAVEPOINT 隔离，此处只负责业务逻辑。
        """
        # 去重键可能超长（POC 源为 poc:<owner>/<repo>，
        # GitHub 仓库名上限 100 + owner 39，最长可达 144 > 128）。
        # 用 _safe_dedup_key 而非直接截断，避免长键前缀相同导致去重碰撞。
        dedup_key = _safe_dedup_key(item.dedup_key, FIELD_LIMITS["dedup_key"])

        # 采集延迟（赛题性能指标）由调用方 _ingest_vulnerabilities 统一算好
        # 经参数传入，此处直接使用——**不要在这里重算**：
        # 曾经本函数重算并覆盖入参，两处公式一旦漂移，落库值与统计口径
        # 就会静默不一致。指标上报（metrics.record_ingest_latency）
        # 同样只在调用方做一次，避免重复计数。

        # 跨源去重：按 dedup_key 查询
        existing = await session.scalar(
            select(Vulnerability).where(Vulnerability.cve_id == dedup_key)
        )

        # ghsa 历史行兜底：2026-10-14 之前 dedup_key 曾用「CVE 或 GHSA」，GitHub
        # 事后分配 CVE 后同一公告会换成 CVE 键二次入库（实测 4 对双胞胎，
        # 33h 延迟实为 CVE 分配延迟）。新采集器统一 ghsa_id 为键后，primary
        # miss 时按 extra->>'ghsa_id' 再查一次，保证同一公告只保留一行。
        if existing is None and item.origin_source == "ghsa":
            ghsa_id = (item.extra or {}).get("ghsa_id") or dedup_key
            if ghsa_id:
                existing = await session.scalar(
                    select(Vulnerability).where(
                        Vulnerability.origin_source == "ghsa",
                        Vulnerability.extra["ghsa_id"].astext == ghsa_id,
                    )
                )

        if existing:
            # 已存在则合并来源信息与内容
            existing.modified_at = item.modified_at or existing.modified_at
            # 顺带修正历史遗留的严重度写法（见 _normalize_severity 注释）
            normalized = _normalize_severity(item.severity)
            if normalized:
                existing.severity = normalized
            # 保留多源溯源：该漏洞被几个源同时收录
            previous = (existing.raw_data or {}).get("all_sources", [])
            sources = list(previous)
            if item.origin_source not in sources:
                sources.append(item.origin_source)
            merged = item.to_dict()
            merged["all_sources"] = sources
            existing.raw_data = merged
        else:
            record = Vulnerability(
                cve_id=dedup_key,
                title=_clip(item.title, FIELD_LIMITS["title"]),
                description=item.description,
                source_name=_clip(item.source_name, FIELD_LIMITS["source_name"]),
                source_url=_clip(item.source_url, FIELD_LIMITS["source_url"]),
                cvss_score=item.cvss_score,
                cvss_vector=_clip(item.cvss_vector, FIELD_LIMITS["cvss_vector"]),
            cvss_version=item.cvss_version,
            severity=_normalize_severity(item.severity),
                origin_source=item.origin_source,
                published_at=item.published_at,
                modified_at=item.modified_at,
                ingest_at=ingest_now,
                collect_latency_seconds=latency_seconds,
                affected_assets=[
                    _clip_asset(a) for a in item.affected_assets
                ],
                references=item.references,
                content_hash=item.content_hash,
                extra={
                    **item.extra,
                    "all_sources": [item.origin_source],
                    "is_news": item.extra.get("is_news", False),
                },
            )
            if classification:
                record.ai_category = classification.category
                record.ai_relevance_score = classification.score
                record.ai_relevance_method = classification.method
                record.ai_keywords = classification.keywords
                if classification.subcategory:
                    record.extra = {
                        **(record.extra or {}),
                        "ai_subcategory": classification.subcategory,
                    }
            session.add(record)
            await session.flush()

            # 受影响资产落库（支撑知识图谱构建）
            for asset in item.affected_assets:
                if not asset.get("product") and not asset.get("cpe"):
                    continue
                safe = _clip_asset(asset)
                session.add(
                    Asset(
                        vulnerability_id=record.id,
                        cpe=safe.get("cpe"),
                        vendor=safe.get("vendor"),
                        product=safe.get("product"),
                        ecosystem=safe.get("ecosystem"),
                        version_start=safe.get("version_start"),
                        version_end=safe.get("version_end"),
                        version_constraint=safe.get("version_constraint"),
                        is_ai_component=bool(asset.get("is_ai_component", False)),
                    )
                )


    async def _ingest_papers(self, session: AsyncSession, items: list[RawItem]) -> int:
        """论文入库（arXiv 格式 upsert）"""
        new_count = 0
        for item in items:
            arxiv_id = item.external_id
            payload = item.extra

            stmt = (
                pg_insert(Paper)
                .values(
                    arxiv_id=arxiv_id,
                    title=item.title,
                    abstract=item.description,
                    authors=payload.get("authors", []),
                    categories=payload.get("categories", []),
                    published_at=item.published_at,
                    updated_at=item.modified_at,
                    pdf_url=payload.get("pdf_url"),
                    ai_topics=payload.get("ai_topics", []),
                )
                .on_conflict_do_update(
                    index_elements=[Paper.arxiv_id],
                    set_={
                        "title": item.title,
                        "abstract": item.description,
                        "updated_at": item.modified_at,
                        "ai_topics": payload.get("ai_topics", []),
                    },
                )
            )
            await session.execute(stmt)
            new_count += 1
        return new_count

    async def _ingest_policies(self, session: AsyncSession, items: list[RawItem]) -> int:
        """
        政策法规入库（upsert）

        赛题要求的赛题赛题三类知识底座之一。用 doc_key 做幂等键——
        policy_collector 用「源 + 标题哈希」构造，稳定可重复。

        逐条 savepoint 隔离：单条数据异常（如字段超长、约束冲突）
        只回滚该条，不让整批采集失败。政策源一次可取回数十条，
        一条坏数据导致全批丢失是不可接受的。
        """
        count = 0
        for item in items:
            payload = item.extra
            doc_key = item.dedup_key
            try:
                async with session.begin_nested():
                    stmt = (
                        pg_insert(Policy)
                        .values(
                            doc_key=doc_key,
                            title=item.title[:1024],
                            summary=item.description,
                            source_name=item.source_name,
                            source_url=item.source_url,
                            policy_kind=payload.get("policy_kind"),
                            org=payload.get("org"),
                            region=payload.get("region"),
                            doc_numbers=payload.get("doc_numbers", []),
                            ai_keywords=payload.get("ai_keywords", []),
                            origin_source=item.origin_source,
                            published_at=item.published_at,
                        )
                        .on_conflict_do_update(
                            # 政策标题/摘要可能后续修订，按最新内容覆盖。
                            # 用 index_elements 指定冲突列，不依赖约束名——
                            # 约束名由数据库按命名策略生成，跨环境不稳定
                            index_elements=[Policy.doc_key],
                            set_={
                                "title": item.title[:1024],
                                "summary": item.description,
                                "policy_kind": payload.get("policy_kind"),
                                "doc_numbers": payload.get("doc_numbers", []),
                                "ai_keywords": payload.get("ai_keywords", []),
                                "published_at": item.published_at,
                            },
                        )
                    )
                    await session.execute(stmt)
                count += 1
            except Exception as exc:  # noqa: BLE001
                self.log.warning(
                    "policy_ingest_skipped",
                    doc_key=doc_key,
                    title=item.title[:80],
                    error=str(exc)[:150],
                )
        return count

    async def _ingest_techniques(self, session: AsyncSession, items: list[RawItem]) -> int:
        """攻击技术入库（ATLAS 格式 upsert）"""
        count = 0
        for item in items:
            payload = item.extra
            atlas_id = payload.get("atlas_id")
            if not atlas_id:
                continue

            stmt = (
                pg_insert(Technique)
                .values(
                    atlas_id=atlas_id,
                    name=payload.get("atlas_name", "")[:512],
                    tactic=payload.get("tactic"),
                    tactics=payload.get("tactics", []),
                    description=item.description,
                    sub_techniques=payload.get("sub_techniques", []),
                    platform=payload.get("platform"),
                    matrix=payload.get("matrix"),
                    maturity=payload.get("maturity"),
                    attack_refs=payload.get("attack_refs", []),
                    is_case_study=bool(payload.get("is_case_study", False)),
                    source_version="ATLAS",
                )
                .on_conflict_do_update(
                    index_elements=[Technique.atlas_id],
                    set_={
                        "name": payload.get("atlas_name", "")[:512],
                        "tactic": payload.get("tactic"),
                        "tactics": payload.get("tactics", []),
                        "description": item.description,
                        "sub_techniques": payload.get("sub_techniques", []),
                        "maturity": payload.get("maturity"),
                        "attack_refs": payload.get("attack_refs", []),
                        "is_case_study": bool(payload.get("is_case_study", False)),
                    },
                )
            )
            await session.execute(stmt)
            count += 1
        return count

    async def _update_cursor(
        self, session: AsyncSession, code: str, cursor_value: str
    ) -> None:
        """推进增量游标"""
        existing = await session.scalar(
            select(CollectorCursor).where(CollectorCursor.source_code == code)
        )
        if existing:
            existing.cursor_value = cursor_value
            existing.last_run_at = datetime.now(UTC)
            existing.total_synced += 1
        else:
            session.add(
                CollectorCursor(
                    source_code=code,
                    cursor_value=cursor_value,
                    last_run_at=datetime.now(UTC),
                    total_synced=1,
                )
            )

    async def _detect_gap(self, collector: BaseCollector) -> float:
        """
        检测采集断档时长（小时）。无断档返回 0。

        ----------
        判定标准（刻意保守，宁可漏判不可误判）----------
        只有**超过 2.5 个调度周期**才算断档：
        · 阈值太松→ 正常轮次被误判，每次都拉超大窗口，浪费上游配额
        · 阈值太紧 → 短暂关机（几分钟）也触发，白做一次大回溯

        另外**首次运行**（没有 last_run_at）返回 0：
        那是「初次建库」，不是「断档」，各源自己的
        default_lookback 才是正确语义。

        ----------
        为什么用 collection_logs 而非 cursor.last_run_at ----------
        cursor.last_run_at 只在该源**成功入库**后推进；
        而「采集失败也会留下日志」能反映「系统确实中断过」。
        这里取两者的较早值：任一维度有断档就该补偿。
        """
        try:
            from sqlalchemy import func, select

            from ..知识库.models import CollectionLog, CollectorCursor

            # 用**有效周期**：运维把某源调慢后，断档阈值必须跟着放宽，
            # 否则刚设成 12 小时的源会被按 2 小时判为「断档」而放大回溯窗口，
            # 采回一堆重复数据。
            interval = max(effective_interval(collector.code), 1)
            # 2.5 倍周期作为阈值，留出调度浮动余量
            threshold_h = interval / 3600 * 2.5

            async with get_session() as session:
                cur = (
                    await session.execute(
                        select(CollectorCursor.last_run_at).where(
                            CollectorCursor.source_code == collector.code
                        )
                    )
                ).scalar()
                # 该源最近一次「采集尝试」的时间（不论成败）
                last_attempt = await session.scalar(
                    select(func.max(CollectionLog.finished_at)).where(
                        CollectionLog.source_code == collector.code,
                        CollectionLog.status == "success",
                    )
                )

            # 首次运行：交给各源自己的 default_lookback
            if cur is None and last_attempt is None:
                return 0.0

            now = datetime.now(UTC)
            refs = [t for t in (cur, last_attempt) if t is not None]
            if not refs:
                return 0.0
            last = min(refs)  # 最早的一个=最长的断档

            gap_h = (now - last).total_seconds() / 3600
            if gap_h <= threshold_h:
                return 0.0
            return gap_h
        except Exception as exc:  # noqa: BLE001
            # 断档检测失败**不应阻断采集** —— 最坏情况退回原行为
            self.log.warning(
                "gap_detect_failed", source=collector.code, error=str(exc)[:120]
            )
            return 0.0

    async def _create_log(
        self,
        session: AsyncSession,
        collector: BaseCollector,
        result: CollectorResult,
        trigger: str = "schedule",
    ) -> int:
        """创建采集日志

        trigger 必须由调用方如实传入（schedule / manual / api）——
        写死成 "schedule" 会让「手动采集」在数据库里也长得像自动采集，
        排查时无法区分两者，也让看板上的执行来源不可信。
        """
        log = CollectionLog(
            source_code=collector.code,
            trigger=trigger,
            started_at=datetime.now(UTC),
            fetched_count=result.fetched_count,
            status="running" if not result.error else "failed",
            error=result.error,
        )
        session.add(log)
        await session.flush()
        return log.id

    async def _finish_log(
        self, session: AsyncSession, log_id: int, report: IngestReport
    ) -> None:
        """完成采集日志"""
        log = await session.get(CollectionLog, log_id)
        if log:
            log.finished_at = datetime.now(UTC)
            log.new_count = report.new
            log.updated_count = report.updated
            log.failed_count = report.skipped
            log.latency_ms = report.elapsed_ms
            log.status = "failed" if report.error else "success"
            log.metrics = {
                "ai_security": report.ai_security,
                "papers": report.papers,
                "techniques": report.techniques,
                "policies": report.policies,
                # 本次采集涉及的条目 ID（采样）——
                # 前端「采集通知」要展示具体采到了什么，
                # 而汇总数字无法追溯，只能在这里落库。
                "sample_ids": report.sampled_ids,
                # Keep the small notification sample and a separate bounded full
                # membership list for exact batch follow-up questions.
                'record_ids': report.record_ids,
                'record_ids_complete': report.record_ids_complete,
            }

    async def get_cursor(self, code: str) -> str | None:
        """读取采集游标"""
        async with get_session() as session:
            cursor = await session.scalar(
                select(CollectorCursor).where(CollectorCursor.source_code == code)
            )
            return cursor.cursor_value if cursor else None
