"""
Prometheus 指标定义

对应决赛评分中的"监控告警"与"赛题性能指标"两项：
把采集时效性、富化置信度、LLM 调用状态等抽象为可观测指标，
支撑 Grafana 看板与 Alertmanager 告警规则。
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info

# 使用独立注册表，避免与默认指标冲突，便于测试隔离
REGISTRY = CollectorRegistry()

# ========== 采集监测 ==========

collect_total = Counter(
    "intel_collect_total",
    "采集任务执行总数",
    labelnames=("source", "status"),
    registry=REGISTRY,
)

collect_items = Counter(
    "intel_collect_items_total",
    "采集获得的条目总数",
    labelnames=("source", "kind"),
    registry=REGISTRY,
)

collect_duration = Histogram(
    "intel_collect_duration_seconds",
    "单次采集耗时",
    labelnames=("source",),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)

# 监测时效性：漏洞发布到被系统采集的延迟
ingest_latency = Histogram(
    "intel_ingest_latency_seconds",
    "情报从发布到入库的延迟（赛题性能指标：监测时效性）",
    labelnames=("source",),
    buckets=(3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800),
    registry=REGISTRY,
)

source_health = Gauge(
    "intel_source_available",
    "数据源可用性（1 可用 / 0 不可用）",
    labelnames=("source",),
    registry=REGISTRY,
)

# ========== AI 分类 ==========

classify_total = Counter(
    "intel_classify_total",
    "AI 相关性分类总数",
    labelnames=("method", "result"),
    registry=REGISTRY,
)

classify_duration = Histogram(
    "intel_classify_duration_seconds",
    "分类耗时",
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 3, 10, 30),
    registry=REGISTRY,
)

# ========== 富化 ==========

enrich_total = Counter(
    "intel_enrich_total",
    "富化任务总数",
    labelnames=("dimension", "status"),
    registry=REGISTRY,
)

enrich_duration = Histogram(
    "intel_enrich_duration_seconds",
    "单维度富化耗时",
    labelnames=("dimension",),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
    registry=REGISTRY,
)

enrich_confidence = Gauge(
    "intel_enrich_confidence",
    "各维度富化平均置信度",
    labelnames=("dimension",),
    registry=REGISTRY,
)

enrich_pending = Gauge(
    "intel_enrich_pending",
    "待富化条目数",
    registry=REGISTRY,
)

# ========== LLM ==========

llm_total = Counter(
    "intel_llm_total",
    "LLM 调用总数",
    labelnames=("backend", "status"),
    registry=REGISTRY,
)

llm_duration = Histogram(
    "intel_llm_duration_seconds",
    "LLM 调用耗时",
    labelnames=("backend",),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
    registry=REGISTRY,
)

llm_degraded = Gauge(
    "intel_llm_degraded_mode",
    "LLM 是否处于降级模式（1 是 / 0 否）",
    registry=REGISTRY,
)

llm_rate_limited = Counter(
    "intel_llm_rate_limited_total",
    "LLM 触发限流次数",
    registry=REGISTRY,
)

# ========== 问答 ==========

qa_total = Counter(
    "intel_qa_total",
    "问答请求总数",
    labelnames=("intent", "degraded", "backend"),
    registry=REGISTRY,
)

qa_duration = Histogram(
    "intel_qa_duration_seconds",
    "问答响应耗时（赛题性能指标：响应速度要求 ≤5s）",
    buckets=(0.5, 1, 2, 3, 5, 8, 15, 30),
    registry=REGISTRY,
)

# ========== 知识库 ==========

db_records = Gauge(
    "intel_db_records",
    "数据库各表记录数",
    labelnames=("table",),
    registry=REGISTRY,
)

build_info = Info(
    "intel_build",
    "系统构建信息",
    registry=REGISTRY,
)


def record_collect(source: str, success: bool, count: int, elapsed_ms: int) -> None:
    """记录一次采集的完整指标"""
    collect_total.labels(source=source, status="success" if success else "failed").inc()
    collect_items.labels(source=source, kind="fetched").inc(count)
    collect_duration.labels(source=source).observe(elapsed_ms / 1000)


def record_ingest_latency(source: str, seconds: int) -> None:
    """记录采集延迟（赛题性能指标举证数据）"""
    ingest_latency.labels(source=source).observe(seconds)


def record_classify(method: str, is_ai: bool) -> None:
    """记录一次分类结果"""
    classify_total.labels(method=method, result="ai" if is_ai else "non_ai").inc()


def record_llm(backend: str, success: bool, elapsed_ms: int) -> None:
    """记录一次 LLM 调用"""
    llm_total.labels(backend=backend, status="ok" if success else "fail").inc()
    llm_duration.labels(backend=backend).observe(elapsed_ms / 1000)


def record_enrich(dimension: str, success: bool, confidence: float, elapsed_ms: int) -> None:
    """记录一次富化结果"""
    enrich_total.labels(
        dimension=dimension, status="ok" if success else "fail"
    ).inc()
    enrich_duration.labels(dimension=dimension).observe(elapsed_ms / 1000)
    enrich_confidence.labels(dimension=dimension).set(confidence)
