"""
采集器抽象基类

定义所有数据源采集器的统一协议。新增数据源只需继承 BaseCollector
并实现 fetch 与 normalize，无需关心限流、重试、游标、日志等横切逻辑。

这是"系统可扩展性"与"采集自动化程度"两项得分的技术支撑。
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..后端服务.config import get_settings
from ..后端服务.logger import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class RawItem:
    """
    归一化后的原始情报条目

    所有采集器输出的统一格式，由入库层负责去重与落库。
    """

    # --- 标识 ---
    dedup_key: str  # 跨源去重键
    origin_source: str  # 统一来源码，如 nvd / ghsa / arxiv
    external_id: str  # 源内唯一 ID

    # --- 主体内容 ---
    title: str
    description: str = ""
    source_name: str | None = None  # 数据源原始名称
    source_url: str | None = None

    # --- 评分 ---
    cvss_score: float | None = None
    cvss_vector: str | None = None
    cvss_version: str | None = None
    severity: str | None = None

    # --- 时间（赛题性能指标：监测时效性的基准）---
    published_at: datetime | None = None
    modified_at: datetime | None = None

    # --- 附加结构 ---
    affected_assets: list[dict[str, Any]] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """内容哈希，用于检测同一漏洞的内容变更"""
        payload = f"{self.title}|{self.description[:500]}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的字典"""
        return {
            "dedup_key": self.dedup_key,
            "origin_source": self.origin_source,
            "external_id": self.external_id,
            "title": self.title,
            "description": self.description,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "cvss_score": self.cvss_score,
            "cvss_vector": self.cvss_vector,
            "cvss_version": self.cvss_version,
            "severity": self.severity,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "modified_at": self.modified_at.isoformat() if self.modified_at else None,
            "affected_assets": self.affected_assets,
            "references": self.references,
            "content_hash": self.content_hash,
            "extra": self.extra,
        }


@dataclass(slots=True)
class CollectorResult:
    """单次采集的结果汇总"""

    source_code: str
    items: list[RawItem] = field(default_factory=list)
    new_cursor: str | None = None  # 新的增量游标
    fetched_count: int = 0
    error: str | None = None
    elapsed_ms: int = 0
    extra_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.error is None


class RateLimiter:
    """
    令牌桶限流器

    NVD 等源对请求速率有硬性限制，超限会返回 403。
    """

    def __init__(self, rps: float, burst: int | None = None) -> None:
        self.rps = rps
        self.burst = burst or max(1, int(rps))
        self._tokens = float(self.burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个令牌，不足则等待"""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self.burst, self._tokens + elapsed * self.rps)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_time = (1 - self._tokens) / self.rps
                await asyncio.sleep(wait_time)


class BaseCollector(abc.ABC):
    """
    数据源采集器基类

    子类需实现：
    - code / name / category: 数据源元信息
    - fetch: 实际抓取逻辑
    - normalize(raw): 归一化为 RawItem 列表

    基类负责：HTTP 客户端管理、限流、重试、增量游标、日志、异常兜底。
    """

    # --- 子类需覆盖 ---
    code: str = "base"
    name: str = "未命名数据源"
    category: str = "漏洞库"
    homepage: str = ""
    requires_key: bool = False
    default_interval: int = 21600
    # 允许调整的采集周期区间（秒）。
    #
    # 为什么需要区间而不是随便设：
    #   · 下限由**上游限流/配额**决定 —— 设得比上游能承受的还快，
    #     只会撞 429/403，拿到的是错误而不是更早的数据；
    #   · 上限防止被设成「几天一次」，那等于实质关闭了自动采集。
    #
    # 看板的周期下拉只在此区间内提供档位，越界选项置灰并说明原因。
    # 取值依据写在 interval_basis 里 —— 数字必须可追溯，
    # 否则后人只看到一个魔法数，不知道它是算出来的还是拍脑袋定的。
    min_interval: int = 3600
    max_interval: int = 86400
    interval_basis: str = ""
    # 该源的 AI 安全相关性天然倾向，用于优化过滤阈值
    ai_native: bool = False

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: httpx.AsyncClient | None = None
        self._limiter = RateLimiter(self._settings.collector_rate_limit_rps)
        self.log = get_logger(f"collector.{self.code}")
        # 断档补偿：本实例本次应回溯的小时数（None = 用各类默认值）。
        # 由 ingest_pipeline 在采集前根据「距上次成功采集多久」设置。
        self.gap_lookback_hours: int | None = None

    def lookback_hours(self, default: int) -> int:
        """
        本次采集应回溯多少小时。

        断档补偿时取「断档时长 + 缓冲」与default 的**较大者** ——
        保证正常轮次行为完全不变（default 更大就用 default），
        只有断档很久时才放大窗口。
        """
        if self.gap_lookback_hours is None:
            return default
        return max(default, self.gap_lookback_hours)

    # ---------- HTTP 管理 ----------

    async def get_client(self) -> httpx.AsyncClient:
        """惰性初始化 HTTP 客户端"""
        if self._client is None:
            headers = {
                "User-Agent": "AI-Security-Intel-Platform/1.0 (competition-research)",
                "Accept": "application/json",
            }
            if self.code == "nvd" and self._settings.nvd_api_key:
                # NVD 的 apiKey 只能走**请求头**，不能走查询参数。
                # 2026-10 实测：`?apiKey=xxx` 一律返回 404
                # `Invalid parameter: apiKey`（NVD 已弃用该传法），
                # 而请求头方式正常返回 200。
                # 别把这里"顺手统一"成 params —— 那是把可用改成不可用。
                headers["apiKey"] = self._settings.nvd_api_key
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self._settings.collector_timeout_seconds),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """释放连接"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ---------- 重试装饰器 ----------

    def _retrying(self):
        """网络异常的指数退避重试装饰器"""
        return retry(
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
            stop=stop_after_attempt(self._settings.collector_max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            reraise=True,
        )

    # ---------- 增量游标 ----------

    @staticmethod
    def cursor_from_time(dt: datetime) -> str:
        """时间转游标字符串"""
        return dt.astimezone(UTC).isoformat()

    @staticmethod
    def default_lookback(hours: int = 24) -> datetime:
        """
        默认回溯时间

        首次运行时无游标，回溯 24 小时——对应赛题性能指标 ≤24h 的合格线。
        """
        return datetime.now(UTC) - timedelta(hours=hours)

    def lookback_from(self, hours: int = 24) -> datetime:
        """
        按「本次实际回溯小时数」计算起始时刻。

        与 default_lookback 的区别：会读取 `gap_lookback_hours`，
        断档补偿时自动放大窗口。各源**优先用这个**替换直接调
        `default_lookback(hours=...)`，即可获得断档补全能力。
        """
        return datetime.now(UTC) - timedelta(hours=self.lookback_hours(hours))

    def _parse_iso(self, value: str | None) -> datetime | None:
        """解析 ISO8601 时间字符串（统一返回带时区的 aware datetime）"""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            # 无时区后缀的串（NVD published、KEV dateAdded 等）按 UTC 解释。
            #
            # 绝不能保留 naive：asyncpg 编码 timestamptz 时会在**客户端**
            # 把 naive 值按本机本地时区换算（语义等同 naive.timestamp()
            # 默认 naive 是本地时间），国内 +08 环境下 published_at 会
            # 整体早存 8 小时——NVD 报 19:17:39，库里存成 11:17:39+00。
            # 而采集延迟计算把同一个值当 UTC 算，两处口径不一致：
            # 2026-10-13 实测 nvd 7298 行 + cisa_kev 1740 行全部早 8 小时，
            # 看板「24 小时发布」窗口因此实际框成 32 小时。
            dt = dt.replace(tzinfo=UTC)
        return dt

    # ---------- 抽象方法 ----------

    @abc.abstractmethod
    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        抓取原始数据

        Args:
            cursor: 上次同步位置，None 表示首次运行

        Returns:
            源原始格式的数据列表
        """

    @abc.abstractmethod
    def normalize(self, raw: Any) -> list[RawItem]:
        """
        归一化为 RawItem

        Args:
            raw: fetch 返回的单条原始数据

        Returns:
            归一化条目列表（一条原始数据可能对应多条）
        """

    # ---------- 统一执行入口 ----------

    async def collect(self, cursor: str | None = None) -> CollectorResult:
        """
        采集主流程（统一入口）

        封装限流、重试、异常捕获、耗时统计。子类不需重写此方法。
        """
        start = time.perf_counter()
        result = CollectorResult(source_code=self.code)

        try:
            await self._limiter.acquire()
            raws = await self.fetch(cursor)
            result.fetched_count = len(raws)

            for raw in raws:
                try:
                    items = self.normalize(raw)
                    result.items.extend(items)
                except Exception as exc:  # noqa: BLE001, PERF203
                    result.extra_metrics["normalize_errors"] = (
                        result.extra_metrics.get("normalize_errors", 0) + 1
                    )
                    self.log.warning("normalize_failed", error=str(exc)[:200])

            # 游标推进到本次抓取的最新时间
            if result.items:
                times = [i.published_at or i.modified_at for i in result.items]
                times = [t for t in times if t is not None]
                if times:
                    result.new_cursor = self.cursor_from_time(max(times))

            self.log.info(
                "collect_success",
                fetched=result.fetched_count,
                normalized=len(result.items),
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)[:500]
            self.log.error("collect_failed", error=result.error)
        finally:
            result.elapsed_ms = int((time.perf_counter() - start) * 1000)

        return result

    async def health_check(self) -> dict[str, Any]:
        """
        数据源健康探测

        访问主页确认可达性，供监控看板展示。
        """
        try:
            client = await self.get_client()
            resp = await client.get(self.homepage or "/", timeout=15.0)
            return {"available": resp.status_code < 500, "status_code": resp.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": str(exc)[:200]}
