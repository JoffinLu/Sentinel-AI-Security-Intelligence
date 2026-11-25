"""
数据库模型

对应所需的 6 类实体：漏洞主体、富化记录、受影响资产、攻击技术、学术论文、采集日志。
设计要点：
- vulnerability 表用 JSONB 存异构富化数据，规避频繁 DDL
- ai_relevance_score 支撑"AI 安全相关性过滤"这一核心技术
- collect_latency_seconds 支撑"监测时效性"赛题性能指标举证
- pgvector 索引支撑语义检索，GIN 索引支撑全文检索
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """ORM 基类"""

    pass


def utcnow() -> dt.datetime:
    """带时区的当前时间"""
    return dt.datetime.now(dt.UTC)


# ========== 数据源字典 ==========


class Source(Base):
    """数据源登记表：记录 9 类数据源的元信息与健康状态"""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(32))  # 漏洞库/学术/社区/标准
    homepage: Mapped[str | None] = mapped_column(String(1024))
    interval_seconds: Mapped[int] = mapped_column(Integer, default=21600)
    requires_key: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    total_collected: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ========== 采集周期自定义 ==========


class SchedulerSetting(Base):
    """
    采集周期自定义值（监控看板「周期」列可调）

    为什么单独建表，而不是复用 Source.interval_seconds：
      Source.interval_seconds 由采集流程自己维护（每次 run 都用
      collector.default_interval 覆写），拿它当配置会被采集行为冲掉。

    这里只存「运维人员显式设定的覆盖值」：**没有记录 = 用采集器默认值**，
    所以默认行为完全不受影响，删掉记录即恢复默认。

    为什么必须持久化：
      APScheduler 用的是 MemoryJobStore，进程重启后所有 job 按代码默认值
      重建。若不落库，用户设的周期会在重启后**静默变回默认值**——
      这是最坑的一种失效：设置时生效、重启后不承认，且没有任何提示。
    """

    __tablename__ = "scheduler_settings"

    source_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ========== 漏洞主体 ==========


class Vulnerability(Base):
    """
    漏洞主体表

    跨源统一存储的核心实体。cve_id 作为业务主键实现跨源去重，
    content_hash 用于检测同一漏洞的内容变更。
    """

    __tablename__ = "vulnerabilities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 业务主键，跨源去重键。
    # 长度需容纳 RSS 去重键（格式 rss:<feed_code>:<16位哈希>），
    # github_ai_security 等较长 feed_code 会达到 33 字符，故设 128。
    cve_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    # --- 描述信息 ---
    title: Mapped[str | None] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(256))  # 数据源原始名称
    source_url: Mapped[str | None] = mapped_column(String(1024))

    # --- 评分信息 ---
    cvss_score: Mapped[float | None] = mapped_column(Float)
    cvss_vector: Mapped[str | None] = mapped_column(String(128))
    cvss_version: Mapped[str | None] = mapped_column(String(8))
    severity: Mapped[str | None] = mapped_column(String(16), index=True)  # CRITICAL/HIGH/...

    # --- AI 安全分类（赛题四分类 + 我们的扩展）---
    ai_category: Mapped[str | None] = mapped_column(String(64), index=True)
    ai_relevance_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    ai_relevance_method: Mapped[str | None] = mapped_column(String(32))  # rule/embedding/llm
    ai_keywords: Mapped[list | None] = mapped_column(JSONB, default=list)

    # --- 溯源与时效（赛题性能指标举证）---
    # 长度需容纳 RSS 源的 feed_code（如 github_ai_security）
    origin_source: Mapped[str] = mapped_column(String(64), index=True)  # 统一来源码
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    modified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ingest_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    collect_latency_seconds: Mapped[int | None] = mapped_column(Integer)  # 发布→采集延迟

    # --- 富化数据（JSONB 规避频繁 DDL）---
    affected_assets: Mapped[list | None] = mapped_column(JSONB, default=list)
    references: Mapped[list | None] = mapped_column(JSONB, default=list)
    poc_refs: Mapped[list | None] = mapped_column(JSONB, default=list)
    paper_refs: Mapped[list | None] = mapped_column(JSONB, default=list)
    technique_refs: Mapped[list | None] = mapped_column(JSONB, default=list)
    fix_info: Mapped[dict | None] = mapped_column(JSONB)
    risk_assessment: Mapped[dict | None] = mapped_column(JSONB)
    enrichment_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    # --- 检索 ---
    embedding: Mapped[Any | None] = mapped_column(Vector(1024))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)  # 原始报文留档
    extra: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    # --- 关系 ---
    enrichments: Mapped[list[Enrichment]] = relationship(
        back_populates="vulnerability", cascade="all, delete-orphan"
    )
    assets: Mapped[list[Asset]] = relationship(
        back_populates="vulnerability", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_vuln_published_severity", "published_at", "severity"),
        Index("ix_vuln_ai_score", "ai_relevance_score"),
        # 全文检索索引，支撑混合检索中的 BM25 分支
        Index(
            "ix_vuln_fts",
            "title",
            "description",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops", "description": "gin_trgm_ops"},
        ),
    )


# ========== 富化记录 ==========


class Enrichment(Base):
    """
    富化记录表（7 维）

    每个维度一条记录，携带置信度与状态，支持失败重试与低置信度人工核验。
    """

    __tablename__ = "enrichments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vulnerability_id: Mapped[int] = mapped_column(
        ForeignKey("vulnerabilities.id", ondelete="CASCADE"), index=True
    )
    dimension: Mapped[str] = mapped_column(String(32), index=True)
    # cvss / asset / poc / paper / fix / technique / risk
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    content: Mapped[dict | None] = mapped_column(JSONB)
    backend: Mapped[str | None] = mapped_column(String(32))  # 产生该结果的 LLM 后端
    error: Mapped[str | None] = mapped_column(Text)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vulnerability: Mapped[Vulnerability] = relationship(back_populates="enrichments")

    __table_args__ = (
        UniqueConstraint("vulnerability_id", "dimension", name="uq_enrichment_vuln_dim"),
    )


# ========== 受影响资产 ==========


class Asset(Base):
    """受影响资产（组件、版本范围、依赖关系）"""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vulnerability_id: Mapped[int] = mapped_column(
        ForeignKey("vulnerabilities.id", ondelete="CASCADE"), index=True
    )
    cpe: Mapped[str | None] = mapped_column(String(512), index=True)
    vendor: Mapped[str | None] = mapped_column(String(256))
    # 真实数据中 CISA KEV 的 product 字段可达数百字符（产品系列全称），需放宽
    product: Mapped[str | None] = mapped_column(String(512), index=True)
    ecosystem: Mapped[str | None] = mapped_column(String(64), index=True)  # PyPI/npm/HuggingFace
    version_start: Mapped[str | None] = mapped_column(String(128))
    version_end: Mapped[str | None] = mapped_column(String(128))
    version_constraint: Mapped[str | None] = mapped_column(String(512))
    is_ai_component: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    vulnerability: Mapped[Vulnerability] = relationship(back_populates="assets")


# ========== 攻击技术 ==========


class Technique(Base):
    """MITRE ATLAS 攻击技术（AI 专用攻击基线）"""

    __tablename__ = "techniques"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    atlas_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    tactic: Mapped[str | None] = mapped_column(String(128), index=True)
    tactics: Mapped[list | None] = mapped_column(JSONB, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    sub_techniques: Mapped[list | None] = mapped_column(JSONB, default=list)
    platform: Mapped[str | None] = mapped_column(String(128))
    matrix: Mapped[str | None] = mapped_column(String(128))
    maturity: Mapped[str | None] = mapped_column(String(64))
    attack_refs: Mapped[list | None] = mapped_column(JSONB, default=list)
    is_case_study: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_version: Mapped[str | None] = mapped_column(String(64))


# ========== 学术论文 ==========


class Paper(Base):
    """AI 安全学术论文"""

    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    arxiv_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1024))
    abstract: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list | None] = mapped_column(JSONB, default=list)
    categories: Mapped[list | None] = mapped_column(JSONB, default=list)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    pdf_url: Mapped[str | None] = mapped_column(String(1024))
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_topics: Mapped[list | None] = mapped_column(JSONB, default=list)
    embedding: Mapped[Any | None] = mapped_column(Vector(1024))
    ingest_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Policy(Base):
    """
    AI 安全政策法规与标准文件

    赛题明确要求「同步采集最新的 AI 安全学术论文、技术标准与政策法规等信息，
    构建完整的 AI 安全知识底座」。论文与技术标准分别有papers /
    techniques 表承载，政策类此前没有独立表——采集结果被 news 分流
    误写入 vulnerabilities 表，导致三类底座实际只有两类可用。
    """

    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1024))
    summary: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(256))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    # 政策属性：法律 / 法规 / 指南 / 标准 / 政策解读
    policy_kind: Mapped[str | None] = mapped_column(String(32), index=True)
    # 发布机构，如 NIST、DarkReading、Schneier
    org: Mapped[str | None] = mapped_column(String(128), index=True)
    # 适用地区，如 US / EU / CN
    region: Mapped[str | None] = mapped_column(String(32), index=True)
    # 提取到的法规编号，如 GB/T 45654-2025、EU AI Act
    doc_numbers: Mapped[list | None] = mapped_column(JSONB, default=list)
    ai_keywords: Mapped[list | None] = mapped_column(JSONB, default=list)
    origin_source: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    embedding: Mapped[Any | None] = mapped_column(Vector(1024))
    ingest_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ========== 采集日志 ==========


class CollectionLog(Base):
    """
    采集日志

    记录每次采集的起止时间、条数、成功率、错误信息，
    是"监测时效性"与"系统稳定性"两项赛题性能指标的数据来源。
    """

    __tablename__ = "collection_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule/manual/api
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)  # 去重后新增
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSONB, default=dict)


# ========== 采集游标 ==========


class CollectorCursor(Base):
    """
    采集游标

    实现增量抓取：每个源记录上次同步到的位置，避免全量重复拉取。
    """

    __tablename__ = "collector_cursors"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cursor_value: Mapped[str | None] = mapped_column(String(256))  # 通常为时间戳
    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    total_synced: Mapped[int] = mapped_column(BigInteger, default=0)
