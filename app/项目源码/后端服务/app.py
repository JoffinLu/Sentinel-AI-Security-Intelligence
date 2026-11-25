"""
FastAPI 应用主入口

提供健康检查、采集控制、情报查询、监控指标四类接口。
监控端点 /metrics 供 Prometheus 抓取，是"监控告警"评分项的落地。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import String, Text, cast, func, or_, select

from ..后端服务 import metrics
from ..后端服务.config import get_settings
from ..后端服务.llm_client import get_llm_client
from ..后端服务.logger import configure_logging, get_logger
from ..数据采集.registry import close_all, effective_interval, get_collectors
from ..数据采集.scheduler import collect_overview, get_scheduler
from ..知识库.database import close_database, health_check, init_database
from ..知识库.models import Paper, Policy, Source, Technique, Vulnerability
from ..运行时 import resource

configure_logging()
log = get_logger("api")
settings = get_settings()

# 常见 OpenAI 兼容服务商预设 —— 点了直接填进输入框，省得手打地址
LLM_PRESETS: list[dict[str, str]] = [
    {"name": "DeepSeek", "api_base": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"name": "通义千问", "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"name": "Kimi", "api_base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"name": "智谱 GLM", "api_base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    {"name": "OpenAI", "api_base": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"name": "Ollama 本地", "api_base": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动初始化 + 关闭清理"""
    log.info(
        "app_starting",
        env="production" if settings.log_format == "json" else "development",
    )

    # 初始化数据库（幂等）
    try:
        await init_database()
        log.info("database_ready")
    except Exception as exc:  # noqa: BLE001
        log.error("database_init_failed", error=str(exc)[:200])
        if not settings.allow_degraded_start:
            raise

    # 预热嵌入模型
    # 首次加载约 18 秒，若不预热会拖慢首个问答请求。
    # ⚠️ 必须放**后台线程**（而非 asyncio 任务）：模型加载是同步阻塞 I/O，
    # 放进事件循环会阻塞整个 loop（含端口就绪/首个响应），
    # 低配机器上表现为「服务启动要一两分钟才就绪」。
    # 失败时检索会自动降级为关键词模式。
    def _warmup_embedding_sync() -> None:
        try:
            from ..问答模块.retrieval import get_retriever

            retriever = get_retriever()
            if retriever.embedding.warmup():
                log.info("embedding_warmup_done")
            else:
                log.warning("embedding_warmup_failed", note="检索将降级为关键词模式")
        except Exception as exc:  # noqa: BLE001
            log.warning("embedding_warmup_error", error=str(exc)[:200])

    import threading as _threading

    _threading.Thread(
        target=_warmup_embedding_sync, name="embedding-warmup", daemon=True
    ).start()

    # 启动定时采集
    try:
        # 必须先载入自定义周期再 start()：start() 按「有效周期」建 job，
        # 顺序反了会让运维设过的周期在本次启动中被默认值覆盖一次
        # （要等下次重启才恢复，属于难自查的间歇性失效）。
        await get_scheduler().load_interval_overrides()
        get_scheduler().start()
        # 补跑错过的周期。APScheduler 用 MemoryJobStore，进程停止期间
        # 错过的任务不会自动补（重启后按「启动时刻 + 周期」重新排期）。
        # 不 await 采集本身——它内部用 create_task 发起，只做一次查询即返回，
        # 否则 osv 那 115 秒会卡住整个应用启动。
        import os as _portable_os
        if _portable_os.environ.get("SENTINEL_START_PAUSED") == "1":
            get_scheduler().pause()
            caught = []
        else:
            caught = await get_scheduler().catch_up_missed()
        if caught:
            log.info("scheduler_catch_up", sources=caught, count=len(caught))
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler_start_failed", error=str(exc)[:200])

    # 构建信息
    metrics.build_info.info(
        {
            "version": "0.1.0",
            "stage": "P0-P1",
            "embedding_dim": str(settings.embedding_dim),
        }
    )

    yield

    # 关闭清理
    get_scheduler().shutdown()
    await close_all()
    await get_llm_client().close()
    await close_database()
    log.info("app_stopped")


app = FastAPI(
    title="AI 安全知识情报系统 API",
    description="智能体驱动的 AI 安全知识情报系统 —— 多源采集、图谱富化与智能问答",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# 态势总览：单独一个模块（一次返回时间范围内的全部分析数据，
# 避免多个接口各自支持时间参数导致口径不一致）
from .graph_view import router as graph_view_router  # noqa: E402
from .ops import router as ops_router  # noqa: E402
from .settings_view import router as settings_router  # noqa: E402
from .situation import router as situation_router  # noqa: E402

app.include_router(situation_router)
app.include_router(ops_router)
app.include_router(settings_router)
# 挂在 /api/graph 下会与 app.py 内已有的 /api/graph/stats 等路由共存：
# FastAPI 按注册顺序匹配，两边路径不重叠（stats/trend/rebuild 与
# overview/node/path 互不冲突）。
app.include_router(graph_view_router)

# 拓展模块（ROEN 网络入侵检测演示，移植自 MD1.0）
from ..拓展模块.routes import router as roen_router  # noqa: E402

app.include_router(roen_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 用户自定义 LLM 配置 ==========
# 动机：默认走公共免费网关，容易被限流（429）。
# 让用户可换成自己的 API Key —— 现有 LLMClient 本就是 OpenAI 兼容格式，
# 所以换服务商实质只是换 base_url / api_key / model 三个值。


@app.get("/api/llm/config", tags=["系统"], summary="读取 LLM 配置（Key 掩码）")
async def llm_config_get() -> dict[str, Any]:
    """
    返回当前生效的 LLM 配置。

    **API Key 只返回掩码**（如 `sk-...x9f2`）——
    任何接口都不回传完整 Key，避免在响应里泄露。
    """
    from . import llm_settings
    from .llm_client import get_llm_client

    cur = llm_settings.read_env_values()
    get_llm_client()  # 确保客户端已初始化（保留原调用语义；返回值此处不需要）
    defaults = llm_settings.get_defaults()

    return {
        "api_base": cur.get("LLM_API_BASE", ""),
        "model": cur.get("LLM_MODEL", ""),
        "key_masked": llm_settings.mask_key(cur.get("LLM_API_KEY", "")),
        "key_configured": bool(cur.get("LLM_API_KEY", "").strip()),
        # 是否与出厂默认不同（用于提示「已自定义」）
        "is_custom": any(
            cur.get(k, "") != defaults.get(k, "") for k in llm_settings.ALLOWED_KEYS
        ),
        "defaults": {
            "api_base": defaults.get("LLM_API_BASE", ""),
            "model": defaults.get("LLM_MODEL", ""),
        },
        "last_saved_at": llm_settings.last_saved_at(),
        "presets": LLM_PRESETS,
    }


class LLMSaveBody(BaseModel):
    """保存 LLM 配置的请求体"""

    api_base: str = Field(..., min_length=1, max_length=300, description="OpenAI 兼容接口地址")
    model: str = Field(..., min_length=1, max_length=120, description="模型名称")
    api_key: str | None = Field(
        None,
        max_length=300,
        description="API Key；留空表示沿用当前 Key（便于只改模型名）",
    )


@app.post("/api/llm/config", tags=["系统"], summary="保存 LLM 配置并立即生效")
async def llm_config_save(body: LLMSaveBody) -> dict[str, Any]:
    """
    写入 .env 并**热更新**运行中的客户端。

    - 只允许改 LLM_API_BASE / LLM_API_KEY / LLM_MODEL 三个键
    - api_key 传空字符串或 null 时沿用当前 Key
    - 不重启服务即可生效（靠 apply_config 重建连接池）
    """
    from . import llm_settings
    from .llm_client import get_llm_client

    cur = llm_settings.read_env_values()
    raw_key = (body.api_key or "").strip()
    reused_key = not raw_key
    key = raw_key or cur.get("LLM_API_KEY", "")
    if not key:
        return {"ok": False, "message": "API Key 不能为空"}

    # 换了地址却没给新 Key，多半是漏填而不是想复用 ——
    # 这种情况直接拒掉，否则会拿旧 key 去打新服务，401 还很难排查。
    if reused_key and body.api_base.strip() != cur.get("LLM_API_BASE", ""):
        return {
            "ok": False,
            "message": "换了 API 地址就必须同时填 API Key（不能沿用旧的）",
        }

    ok, msg = llm_settings.write_env_values(
        {"LLM_API_BASE": body.api_base.strip(), "LLM_MODEL": body.model.strip(), "LLM_API_KEY": key}
    )
    if not ok:
        return {"ok": False, "message": msg}

    # 热更新：关闭旧连接池 + 重建 + 清限流冷却
    client = get_llm_client()
    await client.apply_config(body.api_base.strip(), key, body.model.strip())
    llm_settings.mark_saved()

    # 立刻探活，让用户马上知道结果
    probe = await client.health_check()
    return {
        "ok": True,
        "message": msg,
        "available": probe["primary"]["available"],
        "model": probe["primary"]["model"],
    }


class LLMTestBody(BaseModel):
    """试连测试的请求体"""

    api_base: str = Field(..., min_length=1, max_length=300)
    model: str = Field(..., min_length=1, max_length=120)
    api_key: str = Field(..., min_length=1, max_length=300)


@app.post("/api/llm/config/test", tags=["系统"], summary="测试 LLM 连接（不保存）")
async def llm_config_test(body: LLMTestBody) -> dict[str, Any]:
    """
    用**临时客户端**试连，不动当前配置、不污染限流状态。

    这样用户在弹窗里填错 key 不会打断正在服务的 LLM。
    """
    from .llm_client import get_llm_client

    return await get_llm_client().probe(body.api_base, body.api_key, body.model)


@app.post("/api/llm/config/restore", tags=["系统"], summary="恢复出厂默认配置")
async def llm_config_restore() -> dict[str, Any]:
    """
    把三个键写回进程启动时的值，
    并热更新让默认配置重新生效。
    """
    from . import llm_settings
    from .llm_client import get_llm_client

    d = llm_settings.get_defaults()
    if not d:
        return {"ok": False, "message": "缺少出厂默认配置"}
    # 恢复默认时也要有key —— 默认快照里本就有
    ok, msg = llm_settings.write_env_values(
        {
            "LLM_API_BASE": d.get("LLM_API_BASE", ""),
            "LLM_MODEL": d.get("LLM_MODEL", ""),
            "LLM_API_KEY": d.get("LLM_API_KEY", ""),
        }
    )
    if not ok:
        return {"ok": False, "message": msg}

    client = get_llm_client()
    await client.apply_config(
        d.get("LLM_API_BASE", ""), d.get("LLM_API_KEY", ""), d.get("LLM_MODEL", "")
    )
    return {"ok": True, "message": f"已恢复默认：{d.get('LLM_MODEL')}"}


# ========== 健康检查 ==========


@app.get("/api/health", tags=["系统"], summary="系统健康状态")
async def health() -> dict[str, Any]:
    """
    依赖探活

    返回各依赖的可用性，是"系统状态可见性"的直接体现。
    即使部分依赖不可用也返回 200，由 degraded 字段标识，
    便于监控区分"进程存活"与"功能完整"。

    手册 4.1：**并行探测 + 各自超时上限**。
    串行时总耗时是各依赖之和——LLM 网关抽风会把整个 health
    拖住，顶栏状态与监控看板一起卡住；并行后取最慢的那个，
    且每项都有独立超时，慢依赖不会无限拖。
    """
    import asyncio

    async def _probe(
        factory: Any, timeout: float, label: str
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(factory(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            # 异常文本可能含连接串/密钥，只记类型，响应给固定文案
            log.warning(
                "health_probe_failed", component=label, error_type=type(exc).__name__
            )
            return {
                "available": False,
                "error": "依赖探测超时或暂不可用",
                "error_type": type(exc).__name__,
            }

    db, llm = await asyncio.gather(
        _probe(health_check, 8.0, "database"),
        _probe(lambda: get_llm_client().health_check(), 8.0, "llm"),
    )

    registered = list(get_collectors().keys())
    degraded = not db.get("available") or llm.get("degraded_mode", False)

    return {
        "status": "degraded" if degraded else "healthy",
        "degraded": degraded,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "components": {
            "database": db,
            "llm": llm,
            "collectors": {
                "registered": len(registered),
                "codes": registered,
            },
            # 必须同时给出 running 与 paused：
            # running 只表示「调度器对象已启动」，即使被 pause
            # 挂起所有任务，它依然为 True。若只给 running，
            # 前端拿不到暂停状态，会在暂停后仍显示「采集中」。
            "scheduler": {
                "running": get_scheduler().running,
                "paused": get_scheduler().paused,
            },
        },
        "llm_stats": get_llm_client().stats,
    }


# ========== 监控指标 ==========


@app.get("/metrics", response_class=PlainTextResponse, tags=["监控"])
async def prometheus_metrics() -> str:
    """Prometheus 指标暴露端点"""
    return generate_latest(metrics.REGISTRY).decode("utf-8")


# ========== 采集控制 ==========


@app.get("/api/sources", tags=["采集"], summary="数据源列表")
async def list_sources() -> dict[str, Any]:
    """
    全部数据源的元信息与入库量

    注意：不同数据源落在不同表（漏洞表/论文表/技术表），
    统计时需分别聚合，否则论文与技术类源会显示为 0。
    """
    from ..知识库.database import get_session
    from ..知识库.models import Paper, Technique

    async with get_session() as session:
        # RSS 采集器入库时 origin_source 记的是具体 feed（xianzhi/aws_security 等），
        # 需映射回逻辑数据源 rss，否则看板显示为 0。
        # feed 清单从采集器模块反查，避免在两处硬编码同一份配置。
        from ..数据采集.community_collector import RSS_FEEDS
        from ..数据采集.policy_collector import POLICY_SOURCES

        rss_feed_codes = {code for code, _, _, _ in RSS_FEEDS}
        # 政策法规同样按 feed 记录 origin_source，需一并映射，
        # 否则赛题要求的「政策法规」这一类在统计中会显示为 0
        policy_feed_codes = {s["code"] for s in POLICY_SOURCES}

        all_rows = await session.execute(
            select(Vulnerability.origin_source, func.count()).group_by(
                Vulnerability.origin_source
            )
        )
        counts: dict[str, int] = {}
        for code, n in all_rows:
            if code in rss_feed_codes:
                key = "rss"
            elif code in policy_feed_codes:
                key = "policy"
            else:
                key = code or "unknown"
            counts[key] = counts.get(key, 0) + n

        # arXiv 论文
        paper_total = await session.scalar(select(func.count()).select_from(Paper))
        if paper_total:
            counts["arxiv"] = paper_total

        # MITRE ATLAS 技术（排除案例研究）
        tech_total = await session.scalar(
            select(func.count()).select_from(Technique).where(Technique.is_case_study.is_(False))
        )
        if tech_total:
            counts["atlas"] = tech_total

    sources = []
    for code, collector in get_collectors().items():
        sources.append(
            {
                "code": code,
                "name": collector.name,
                "category": collector.category,
                "homepage": collector.homepage,
                # 有效周期（含运维自定义值），与「定时调度」卡显示一致
                "interval_hours": round(effective_interval(code) / 3600, 1),
                "ai_native": collector.ai_native,
                "records": counts.get(code, 0),
            }
        )
    return {"total": len(sources), "sources": sources}


# 手册 4.7：静态路由必须先于动态路由注册。
# 顺序颠倒时 POST /api/collect/all 会被下面�� /api/collect/{code} 抢先匹配
# （code="all"），走进「未知数据源」的分支，批量采集接口形同失效。
@app.post("/api/collect/all", tags=["采集"], summary="触发全部数据源采集")
async def trigger_collect_all() -> dict[str, Any]:
    """依次采集全部数据源（串行，避免上游限流）"""
    results = await get_scheduler().collect_all()
    ok = sum(1 for r in results if not r.get("error"))
    return {"total": len(results), "succeeded": ok, "results": results}


@app.post("/api/collect/{code}", tags=["采集"], summary="手动触发采集")
async def trigger_collect(code: str) -> dict[str, Any]:
    """立即执行指定数据源的采集"""
    if code not in get_collectors():
        raise HTTPException(status_code=404, detail=f"未知数据源: {code}")
    # trigger="manual"：这是人在界面上点的，落库必须如实标 manual，
    # 否则数据库里手动与自动采集长得一样，排查时无法区分
    result = await get_scheduler().collect_now(code, trigger="manual")
    return {"triggered": code, "result": result}


# ========== 采集监控 ==========


@app.get("/api/monitor/overview", tags=["监控"], summary="采集概览")
async def monitor_overview() -> dict[str, Any]:
    """
    采集概览统计

    包含总量、来源分布、时效性分位数、最近日志，
    是"监测时效性"赛题性能指标的数据来源。
    """
    return await collect_overview()
@app.get("/api/monitor/scheduler", tags=["监控"], summary="调度状态")
async def scheduler_status() -> dict[str, Any]:
    """
    定时任务状态

    包含上次执行与下次执行时间——两者一起才能证明
    「自动化监测」真的在持续运行，而不是只注册了任务。
    """
    return {
        "running": get_scheduler().running,
        "paused": get_scheduler().paused,
        "jobs": await get_scheduler().status_with_history(),
    }


class SchedulerIntervalBody(BaseModel):
    """调整采集周期的请求体。

    interval_seconds 留空 = 恢复该源默认周期（与 /reset 等价），
    这样前端一个接口即可覆盖「设置」与「恢复默认」两种操作。
    """

    source_code: str = Field(..., min_length=1, max_length=64, description="数据源代码")
    interval_seconds: int | None = Field(
        None, ge=60, le=604800, description="目标周期（秒）；留空表示恢复默认"
    )


@app.post("/api/monitor/scheduler/interval", tags=["监控"], summary="设置采集周期")
async def scheduler_set_interval(body: SchedulerIntervalBody) -> dict[str, Any]:
    """
    设置某数据源的采集周期，立即生效并持久化

    周期必须落在该源声明的 [min_interval, max_interval] 内 ——
    这个区间由上游 API 的限流/配额决定，不是任意划的：
    把 NVD 设成 10 分钟只会撞 429，拿到错误而不是更早的数据。
    越界时返回 200 + ok:false 与依据说明（不用 4xx：这是业务校验，
    不是协议错误，前端要读 reason 展示给用户）。

    生效链路：校验 → 落库 scheduler_settings → reschedule_job 重排。
    落库是必须的：APScheduler 用 MemoryJobStore，不持久化的话
    重启后自定义周期会静默变回默认值。
    """
    scheduler = get_scheduler()
    if body.interval_seconds is None:
        return await scheduler.reset_interval(body.source_code)
    return await scheduler.set_interval(body.source_code, body.interval_seconds)


@app.post("/api/monitor/scheduler/pause", tags=["监控"], summary="暂停自动采集")
async def scheduler_pause() -> dict[str, Any]:
    """
    暂停所有定时采集

    与关闭服务的区别：调度器仍在运行，界面、问答、图谱全部照常可用，
    只是不再自动发起采集。用于「演示前冻结数据」或
    「担心上游限流时先停一停」的场景。

    任务不销毁，恢复后 next_run_time 保持原有排期。
    """
    return get_scheduler().pause()


@app.post("/api/monitor/scheduler/resume", tags=["监控"], summary="恢复自动采集")
async def scheduler_resume() -> dict[str, Any]:
    """恢复被暂停的定时采集"""
    return get_scheduler().resume()


# ========== 问答接口 ==========


def _qa_record(elapsed_s: float, ok: bool, error: str | None) -> None:
    """把一次问答执行记入运维面板。

    问答页有四条链路（/qa/ask、/qa/stream 走 qa_agent；
    /qa/free、/qa/free/stream 走 free_agent），
    **每条都必须记**——漏掉任何一条，面板上的「问答 Agent」
    就会在用户实际提问后依然显示「未触发」，看着像摆设。
    """
    from . import ops_history

    ops_history.append_history(
        "qa", ok=ok, ms=elapsed_s * 1000.0, agent="qa", error=error
    )


class QAHistoryMessage(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(max_length=16000)


class QAConversationBody(BaseModel):
    history: list[QAHistoryMessage] = Field(default_factory=list, max_length=40)

    def messages(self):
        return [message.model_dump() for message in self.history]


@app.post("/api/qa/free", tags=["问答"], summary="自由问答（Agent 模式）")
async def qa_free(
    question: str = Query(..., min_length=1, max_length=2000, description="用户问题"),
    session_id: str = Query("default", max_length=64),
    mode: Literal['auto','agent','retrieval','classic'] = Query("auto", description="auto/agent=Agent自主查询；retrieval=检索问答；classic=显式历史兼容"),
    body: QAConversationBody | None = Body(default=None),
) -> dict[str, Any]:
    """
    自由问答：让 LLM 拿着「数据地图」自己查库回答。

    与 /api/qa/ask 的分工：
      · /api/qa/ask（classic）—— 正则意图 → 模板/图谱，**确定性快**，
        赛题要求的意图识别 / 引用溯源 / 推理链 / P95≤5s 靠它兜住
      · /api/qa/free（agent）—— LLM 自主调工具，能回答
        「哪个最多」「有哪些含义」这类需要现算的问题

    auto和agent进入全知识库自由分析；retrieval只检索和阅读资料，保留多轮追问；classic仅用于显式兼容旧接口。
    """
    from ..问答模块.free_agent import ask_free as agent_ask
    # All default requests use the same general analyst; classic is explicit compatibility only.
    use_agent = mode != 'classic'

    if not use_agent:
        from ..问答模块.qa_agent import get_qa_agent

        # ---------- 指定了 CVE 编号却「查无此条」 ----------
        # 实测：问 CVE-2021-43881（库里没有），旧路径会返回
        # 一条完全无关的漏洞（CVE-2026-102723）—— 因为检索
        # 按语义相似度返回了「最接近的」若干条，而不是空。
        # 这比明说「没有」危险得多：用户会以为那就是答案。
        # 这里补一道精确校验：编号在结果里找不到，就如实告知。
        m = re.search(r"CVE-\d{4}-\d{4,7}", question, re.IGNORECASE)
        if m:
            wanted = m.group(0).upper()
            from sqlalchemy import select

            from ..知识库.database import get_session
            from ..知识库.models import Vulnerability

            async with get_session() as s:
                exists = await s.scalar(
                    select(Vulnerability.cve_id).where(
                        Vulnerability.cve_id.ilike(wanted)
                    ).limit(1)
                )
            if not exists:
                return {
                    "answer": (
                        f"知识库中未收录 **{wanted}**。\n\n"
                        "可能原因：\n"
                        "1. 该漏洞尚未被采集到（当前覆盖 NVD / GHSA / OSV / CISA KEV 等 9 类数据源）\n"
                        "2. 编号有误\n\n"
                        "你可以换个关键词检索，或问我「知识库里有哪些高危漏洞」。"
                    ),
                    "intent": "fact",
                    "intent_label": "精确查询",
                    "confidence": 0.0,
                    "citations": [],
                    "reasoning": [
                        {
                            "step": "精确查询",
                            "detail": f"按编号精确检索 {wanted}",
                            "evidence": "库中无此编号，未返回任何漏洞",
                        }
                    ],
                    "llm_backend": "none",
                    "mode": "classic",
                }

        agent = get_qa_agent()
        _t0 = time.perf_counter()
        result = await agent.ask(question, session_id=session_id)
        d = result.to_dict()
        d["mode"] = "classic"
        _qa_record(time.perf_counter() - _t0, True, None)
        return d

    _t1 = time.perf_counter()
    r = await agent_ask(question, session_id=session_id, history=body.messages() if body else None,
                        mode='retrieval' if mode == 'retrieval' else 'agent')
    ok = r.get('backend') not in ('timeout', 'max_turns', 'rate_limited') and not r.get('backend', '').startswith('error')
    _qa_record(time.perf_counter() - _t1, ok, None if ok else r.get('backend'))
    return r


@app.post("/api/qa/free/stream", tags=["问答"], summary="自由问答（Agent 流式）")
async def qa_free_stream(
    question: str = Query(..., min_length=1, max_length=2000, description="用户问题"),
    session_id: str = Query("default", max_length=64),
    mode: Literal['auto','agent','retrieval'] = Query("auto", description="auto/agent=Agent自主查询；retrieval=检索问答"),
    body: QAConversationBody | None = Body(default=None),
) -> Any:
    """
    Agent 模式的 SSE 流式输出。

    事件序列（与 /api/qa/stream 对齐，前端可复用同一套解析）：
      · delta —— 答案增量文本（**边收边推**，真流式）
      · reset —— 中间轮的过渡语需要撤回，通知前端清空已显示内容
      · done  —— 完整结果（citations / tool_calls 等）

    为什么 Agent 也需要流式：
    Agent 要 1~2 次 LLM 往返，体感 8~20 秒。
    非流式时界面整段空白，用户无法判断是在跑还是卡住了。

    ⚠️ reset 事件为什么必需：
    OpenAI 流式协议里 tool_calls 与 content 属于同一轮的增量，
    只有等这轮结束才知道是不是中间轮。所以中间轮的过渡语
    （"好的，我来查询…"）会先被推出去，必须靠 reset 撤回。
    """
    from ..问答模块.free_agent import ask_free as agent_ask, public_agent_result

    # 计时用：把本次流式问答耗时记入运维面板（time 已在模块顶层导入）

    async def gen():
        # 手册 4.2：**有界队列**（maxsize=256）。
        # 无界队列在慢客户端（网络卡、用户切页）时会持续堆积，
        # 生产任务还以为推成功了，内存一路涨；有界后 put 等待，
        # 自动把背压传回生产者。
        # 队列里放 str（增量）或 None（reset 标记）
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
        # 计时起点：用于把这次问答的耗时记入运维面板
        _t_freestream = time.perf_counter()

        async def on_delta(piece: str) -> None:
            await queue.put(piece)

        async def on_reset() -> None:
            await queue.put(None)

        task = asyncio.create_task(
            agent_ask(question, session_id=session_id,
                      history=body.messages() if body else None,
                      mode='retrieval' if mode == 'retrieval' else 'agent',
                      on_delta=on_delta, on_reset=on_reset)
        )
        heartbeat_at = asyncio.get_running_loop().time()
        # 队列非空就吐增量；任务结束且队列已空才收尾。
        # ⚠️ 原写法用 asyncio.wait + `if get.cancelled or True` 判断，
        # 那是个恒真条件，等于「任务一完成就 break」——
        # 任务完成时队列里还躺着没吐完的增量，于是全被丢弃（实测 delta 为 0）。
        try:
            while True:
                if not queue.empty():
                    item = queue.get_nowait()
                    if item is None:
                        yield "event: reset\ndata: {}\n\n"
                    else:
                        yield (
                            "event: delta\n"
                            f"data: {json.dumps({'delta': item}, ensure_ascii=False)}\n\n"
                        )
                    heartbeat_at = asyncio.get_running_loop().time()
                    continue
                if task.done():
                    # 任务已结束：把队列里剩下的吐完再走
                    while not queue.empty():
                        item = queue.get_nowait()
                        if item is None:
                            yield "event: reset\ndata: {}\n\n"
                        else:
                            yield (
                                "event: delta\n"
                                f"data: {json.dumps({'delta': item}, ensure_ascii=False)}\n\n"
                            )
                    break
                # 队列空且任务未完 → 短暂等待。
                # 用 wait_for 等队列而不是 sleep 轮询：既能立刻拿到新元素，
                # 又能在 10 秒没有任何增量时发心跳注释（防代理掐断长连接）。
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    now = asyncio.get_running_loop().time()
                    if now - heartbeat_at >= 10:
                        yield ": keep-alive\n\n"
                        heartbeat_at = now
                    continue
                if item is None:
                    yield "event: reset\ndata: {}\n\n"
                else:
                    yield (
                        "event: delta\n"
                        f"data: {json.dumps({'delta': item}, ensure_ascii=False)}\n\n"
                    )
                heartbeat_at = asyncio.get_running_loop().time()

            # 无论成功/异常都要用 done 收尾，否则前端会一直转圈
            try:
                r = task.result()
                _err = None
            except Exception as exc:  # noqa: BLE001
                _err = str(exc)[:180]
                r = {
                    "answer": f"问答失败：{exc}",
                    "citations": [],
                    "tool_calls": [],
                    "backend": "error",
                    "elapsed_ms": 0,
                    "refused": False,
                    "mode": 'retrieval' if mode == 'retrieval' else 'agent',
                }
            r = public_agent_result(r)

            # 记入运维面板。问答页默认走的是 free_agent（自由问答 Agent），
            # 此前这条链路**完全没有记录**，所以用户提问后
            # 面板「问答 Agent」始终显示未触发——看着像摆设。
            # backend=="error" 视为失败，成功率才有意义。
            _qa_record(
                time.perf_counter() - _t_freestream,
                ok=(_err is None and not r.get('backend', '').startswith('error') and r.get('backend') not in ('timeout', 'max_turns', 'rate_limited')),
                error=_err
                or (None if r.get("backend") != "error" else "qa_failed"),
            )
            yield (
                "event: done\n"
                f"data: {json.dumps(r, ensure_ascii=False)}\n\n"
            )
        finally:
            # 手册 4.2：**断连即取消生产任务**。
            # 客户端关页面/点停止后，生成任务还在烧 LLM 配额；
            # GeneratorExit（客户端断开时抛）也会走到这里。
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            # 关闭代理缓冲，否则增量会被攒在代理里一次性吐出
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/collect/feed", tags=["采集"], summary="新采集条目明细")
async def collect_feed(
    minutes: int = Query(10080, ge=1, le=43200, description="回看最近多少分钟（默认 7 天）"),
) -> dict[str, Any]:
    """
    返回最近采集到的**具体条目**（不只是「新增 N 条」）

    ---------- 为什么单独开接口 ----------
    原来的 `recent_logs` 只有 {source, new_count, finished_at}，
    只能告诉用户「某源采了 72 条」—— 仍然不知道**采到了什么**，
    用户要判断有没有价值的更新必须自己去情报中心翻。
    「采集到新东西」这件事只有落到具体条目上才有意义。

    ---------- 怎么找到「本次采集了哪些」 ----------
    `collection_logs.metrics.sample_ids` —— 入库时由
    ingest_pipeline 写入的条目 ID 采样（最多 30 个）。

    为什么不能靠时间字段反查（试过，行不通）：
      · `ingest_at` —— rss/poc 这类源的 dedup_key 稳定，
        重复采集走「合并已有条目」，**不产生新行**，ingest_at 不变
      · `modified_at` —— 取的是**源站发布时间**，不是入库时间，也不变
    实测：声称「新增 72 条」的那次采集，按时间窗反查得到 **0 行**。
    所以唯一可靠的做法是入库时就把 ID 记下来。
    """
    from ..知识库.database import get_session
    from ..知识库.models import CollectionLog, Vulnerability

    since = datetime.now(UTC) - timedelta(minutes=minutes)

    async with get_session() as session:
        log_rows = (
            await session.execute(
                select(
                    CollectionLog.id,
                    CollectionLog.source_code,
                    CollectionLog.new_count,
                    CollectionLog.fetched_count,
                    CollectionLog.finished_at,
                    CollectionLog.metrics,
                )
                .where(
                    CollectionLog.status == "success",
                    # 只要求「抓到了东西」，不要求「有新增」。
                    # 原来用 new_count > 0 过滤，会把「采到 1734 条但全部重复」
                    # 这类记录整条排掉 —— 结果列表里只剩 rss 一个源，
                    # 其他 8 个源**采过却看不见**，像是没在工作。
                    CollectionLog.fetched_count > 0,
                    CollectionLog.finished_at >= since,
                )
                .order_by(CollectionLog.finished_at.desc())
                # 放宽到 60 条再在 Python 里按源收敛：
                # rss 每小时一轮、nvd 6 小时、atlas 7 天，
                # 若在 SQL 里 limit(20)，rss 会吃掉配额把其他源挤掉
                # （实测 9 个源里有 5 个因此「消失」）。
                .limit(60)
            )
        ).all()

        groups: list[dict[str, Any]] = []
        # 跨组去重：同一个源两次采集往往会带回**同一批条目**
        # （dedup_key 稳定 → 走「合并已有条目」）。若不做去重，
        # 接口会返回「安全社区 +72」两次、条目一模一样，
        # 看起来就像重复内容。实测 60 条里只有 30 条唯一。
        seen_ids: set[str] = set()
        for log_id, source_code, new_count, fetched_count, finished_at, metrics in log_rows:
            ids = (metrics or {}).get("sample_ids") or []
            # 已在更早的分组里出现过 → 本组不再展示该条目
            ids = [i for i in ids if i not in seen_ids]
            items: list[dict[str, Any]] = []
            if ids:
                rows = (
                    await session.execute(
                        select(
                            Vulnerability.cve_id,
                            Vulnerability.title,
                            Vulnerability.severity,
                            Vulnerability.cvss_score,
                            Vulnerability.source_url,
                        ).where(Vulnerability.cve_id.in_(ids))
                    )
                ).all()
                # 按入库时的顺序返回，SQL 的 IN 查询不保证顺序
                by_id = {r[0]: r for r in rows}
                for cid in ids:
                    r = by_id.get(cid)
                    if r is None:
                        continue
                    items.append(
                        {
                            "id": r[0],
                            "title": (r[1] or "")[:110],
                            "severity": r[2],
                            "cvss": r[3],
                            # 跳转链接：有 CVE 编号走 NVD 官方详情页；
                            # 无编号的非漏洞情报（社区博客/论文/POC 仓库/政策等）
                            # 回落到采集时保存的原文链接，避免「有链接却点不开」。
                            "url": (
                                f"https://nvd.nist.gov/vuln/detail/{r[0]}"
                                if r[0].startswith("CVE-")
                                else (r[4] or None)
                            ),
                        }
                    )
            groups.append(
                {
                    "log_id": log_id,
                    "source": source_code,
                    "new_count": new_count,
                    # 本次抓到的总量（去重前）—— 与 new_count 对比
                    # 就能看出「抓了很多但都是已存在的」
                    "fetched_count": fetched_count,
                    "finished_at": finished_at.isoformat() if finished_at else None,
                    "items": items,
                    # 去重后剩余的 id（本次真正「可展示」的新条目）
                    "item_ids": ids,
                }
            )
            seen_ids.update(ids)

    # ---------- 按源收敛 ----------
    # 问题：rss 每小时一轮，nvd 6 小时、atlas 7 天一轮。
    # 若直接按时间倒序取N 条，结果会被 rss 大部分占满，
    # 用户以为「只有 rss 在工作」。
    #
    # 策略：**有明细的源**（rss/社区类）保留最多 5 条 ——
    # 因为明细才是用户真正想看的内容；
    # **无明细的源**每个只留最近一条 —— 它的价值是「证明在跑」，
    # 不是刷屏。这样 9 个源都能露出，也不会有信息被截断。
    with_items = [g for g in groups if g["items"]]
    without_items = [g for g in groups if not g["items"]]
    seen_sources: set[str] = set()
    one_per_source: list[dict[str, Any]] = []
    for g in without_items:  # groups 已按时间倒序，首次出现即最近
        if g["source"] in seen_sources:
            continue
        seen_sources.add(g["source"])
        one_per_source.append(g)

    # ---------- 扁平化成「每条数据一行」 ----------
    # 用户要的是一张扁平表（序号 / 时间 / 数据条 / 数据源），
    # 而 groups 是「按采集次数分组的嵌套结构」——
    # 前端要自己再拆一层，且无法统一编号。
    # 这里直接把每条采集到的数据摊平成一行，附上它所属的采集与数据源。
    rows: list[dict[str, Any]] = []
    for g in with_items[:5] + one_per_source:
        src = g["source"]
        stamp = g["finished_at"]
        for item in g["items"]:
            rows.append(
                {
                    "source": src,
                    "finished_at": stamp,
                    "id": item["id"],
                    "title": item["title"],
                    "severity": item["severity"],
                    "cvss": item["cvss"],
                    "url": item["url"],
                }
            )

    # 采集批次列表（最近若干次采集，无论有没有新增数据）
    #
    # 为什么前端需要它：rows 只包含**采到明细**的采集。
    # 像 atlas 这类源 `new_count=0`（采集了但无新增、也没记录明细）
    # 根本不出现在 rows 里 —— 前端若只比对 rows，
    # 就永远检测不到「又采集了一次」这个事实。
    # 未读通知应以「采集批次」为单位，而不是以数据行为单位。
    batches = [
        {
            "log_id": g["log_id"],
            "source": g["source"],
            "finished_at": g["finished_at"],
            "new_count": g["new_count"],
            "fetched_count": g["fetched_count"],
        }
        for g in (with_items[:5] + one_per_source)
    ]

    return {
        "groups": with_items[:5] + one_per_source,
        "rows": rows,
        "batches": batches,
        "generated_at": datetime.now(UTC).isoformat(),
    }


@app.get("/api/kb/overview", tags=["知识底座"], summary="知识库全貌（数据地图）")
async def kb_overview() -> dict[str, Any]:
    """各表条数、字段含义与分布统计——供 Agent 注入，也供前端展示。"""
    from ..问答模块.kb_overview import get_overview

    return await get_overview()
@app.post("/api/qa/ask", tags=["问答"], summary="情报问答")
async def qa_ask(
    question: str = Query(..., min_length=1, max_length=500, description="用户问题"),
    session_id: str = Query("default", max_length=64, description="会话标识"),
    use_graph: bool = Query(True, description="是否启用图谱多跳"),
) -> dict[str, Any]:
    """
    情报问答

    返回答案、引用溯源、推理链、推荐追问四部分。
    响应时间受 5 秒预算约束——超时会自动降级为结构化模板作答，
    保证 P95 ≤5s（赛题性能指标）。
    """
    from ..问答模块.qa_agent import get_qa_agent
    from . import ops_history

    # 计时用（time 已在模块顶层导入）
    agent = get_qa_agent()
    _t0 = time.perf_counter()
    result = await agent.ask(
        question, session_id=session_id, use_graph=use_graph
    )
    # 问答页走的是这条直连链路，不经过编排器——
    # 若不在此记录，运维面板的「问答 Agent」永远显示未触发
    # （此前用户提问后看到「待触发」，正是缺了这条记录）。
    ops_history.append_history(
        "qa",
        ok=True,
        ms=(time.perf_counter() - _t0) * 1000,
        agent="qa",
    )
    return result.to_dict()


@app.post("/api/qa/stream", tags=["问答"], summary="流式问答（SSE）")
async def qa_stream(
    question: str = Query(..., min_length=1, max_length=500),
    session_id: str = Query("default", max_length=64),
) -> Any:
    """
    SSE 流式输出

    事件序列：meta（检索结果）→ delta（逐段答案）→ done（完整数据）

    之所以用「逐段」而非「逐 token」：网关的流式支持不稳定，
    而分段推送已能把首字延迟压到 1 秒内，且实现更可靠。
    """
    from fastapi.responses import StreamingResponse

    from ..问答模块.qa_agent import get_qa_agent
    from . import ops_history
    # 计时用（time 已在模块顶层导入）

    async def event_stream():
        agent = get_qa_agent()
        _t0 = time.perf_counter()
        try:
            result = await agent.ask(question, session_id=session_id)
        except Exception as exc:  # noqa: BLE001
            import json as _json

            err = _json.dumps({"error": str(exc)[:200]}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"
            ops_history.append_history(
                "qa", ok=False, ms=(time.perf_counter() - _t0) * 1000,
                agent="qa", error=str(exc)[:180],
            )
            return

        # 与 /api/qa/ask 同理：问答页直连链路必须记入运维面板，
        # 否则「问答 Agent」永远显示未触发
        ops_history.append_history(
            "qa", ok=True, ms=(time.perf_counter() - _t0) * 1000, agent="qa",
        )

        import json as _json

        # 1) 先推元信息，让前端立刻渲染检索结果骨架
        meta = {
            "intent": result.intent,
            "intent_label": result.intent_label,
            "confidence": result.confidence,
            "citations": [c.to_dict() for c in result.citations],
            "reasoning": [r.to_dict() for r in result.reasoning],
            "graph_paths": result.graph_paths,
            "retrieval_hits": result.retrieval_hits,
            "papers": result.papers,
            "policies": result.policies,
        }
        yield f"event: meta\ndata: {_json.dumps(meta, ensure_ascii=False)}\n\n"

        # 2) 分段推送答案。
        # 关键：每段之间必须真正让出事件循环，否则所有片段会在同一
        # 微任务内瞬间推完——前端收到的是一个「一次性全文」，
        # 视觉上等同非流式，违背「像对话 AI 一样逐字输出」的要求。
        import asyncio

        chunks = _split_answer(result.answer)
        if len(chunks) > _STREAM_MAX_CHUNKS:
            # 超长答案按上限截断推送，完整内容仍在 done 事件里
            chunks = chunks[:_STREAM_MAX_CHUNKS]
        for chunk in chunks:
            payload = {"delta": chunk}
            yield f"event: delta\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(_STREAM_DELAY_MS / 1000)

        # 3) 收尾
        done = result.to_dict()
        yield f"event: done\ndata: {_json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _split_answer(answer: str, min_len: int = 24) -> list[str]:
    """
    把答案切成适合流式推送的片段（按标点优先，不断词）

    切分粒度兼顾两点：
    ① 片段不过小——太碎会让前端渲染开销超过网络开销
    ② 片段不过大——单段超过 ~120 字时，用户要等很久才看到动静
    """
    if not answer:
        return []
    chunks: list[str] = []
    buf = ""
    for ch in answer:
        buf += ch
        if (ch in "。！？\n；" or len(buf) >= min_len * 3) and len(buf) >= min_len:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


# 逐段推送的节奏（毫秒）。取 28ms 是权衡结果：
# 快到 perceived latency 接近真实打字速度（~35 字/秒），
# 慢到不会让单次 SSE 事件循环空转占用 CPU。
_STREAM_DELAY_MS = 28
# 单次请求最多推送多少片段，防止超长答案把连接拖得太久
_STREAM_MAX_CHUNKS = 200


@app.get("/api/qa/sessions", tags=["问答"], summary="会话统计")
async def qa_sessions() -> dict[str, Any]:
    """当前活跃会话数"""
    from ..问答模块.intent import get_session_count

    return {"active_sessions": get_session_count()}


@app.delete("/api/qa/session/{session_id}", tags=["问答"], summary="清空会话")
async def qa_clear_session(session_id: str) -> dict[str, Any]:
    """清空指定会话的上下文"""
    from ..问答模块.qa_agent import get_qa_agent
    from ..问答模块.conversation import clear_conversation

    get_qa_agent().clear_session(session_id)
    clear_conversation(session_id)
    return {"cleared": session_id}


@app.get("/api/qa/search", tags=["问答"], summary="仅检索（调试用）")
async def qa_search(
    q: str = Query(..., min_length=1, max_length=200),
    top_k: int = Query(5, le=20),
) -> dict[str, Any]:
    """
    只做检索不生成答案

    用于调试检索质量，也是评测集验证的基础接口。
    """
    from ..问答模块.retrieval import get_retriever

    retriever = get_retriever()
    retriever.embedding.warmup()
    result = await retriever.search(q, top_k=top_k)
    return result.to_dict()


# ========== 智能体编排接口 ==========


@app.get("/api/orchestration/graph", tags=["编排"], summary="工作流结构")
async def orchestration_graph() -> dict[str, Any]:
    """
    LangGraph 状态机结构

    展示节点、边与并行/条件边等编排特性，
    对应「基于 LangGraph 的 AI Agent 编排」评分项。
    """
    from ..智能体编排.orchestrator import get_workflow

    return get_workflow().graph_structure()


@app.get("/api/orchestration/agents", tags=["编排"], summary="Agent 健康状态")
async def orchestration_agents() -> dict[str, Any]:
    """
    各 Agent 的心跳与熔断状态

    连续失败达阈值的 Agent 会被熔断跳过，
    避免单个组件故障拖垮整条流水线——这是「自愈能力」的直接体现。
    """
    from ..智能体编排.orchestrator import get_workflow

    agents = get_workflow().agent_health()
    return {
        "agents": agents,
        "total": len(agents),
        "healthy": sum(1 for a in agents if a["healthy"]),
        "circuit_open": [a["name"] for a in agents if a["circuit_open"]],
    }


@app.post("/api/orchestration/run", tags=["编排"], summary="执行工作流")
async def orchestration_run(
    workflow_type: str = Query("qa", description="pipeline / enrich / qa"),
    cve_id: str | None = Query(None, description="目标漏洞编号"),
    source_code: str | None = Query(None, description="指定数据源"),
    question: str | None = Query(None, description="问句"),
) -> dict[str, Any]:
    """
    执行一次智能体工作流

    返回每个节点的执行状态、耗时与决策依据，
    前端据此展示编排流程的可解释性。
    """
    from ..智能体编排.orchestrator import get_workflow

    state = await get_workflow().run(
        workflow_type=workflow_type,
        cve_id=cve_id,
        source_code=source_code,
        question=question,
    )
    return state.to_dict()


@app.get("/api/orchestration/review", tags=["编排"], summary="待人工核验队列")
async def orchestration_review() -> dict[str, Any]:
    """
    待人工核验的低置信度富化结果

    支撑赛题要求「人机协同」能力：低置信度结果不直接采信，
    交由人工确认后再进入知识库。
    """
    from sqlalchemy import select

    from ..知识库.database import get_session
    from ..知识库.models import Enrichment, Vulnerability

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Enrichment, Vulnerability.cve_id, Vulnerability.title)
                .join(Vulnerability, Vulnerability.id == Enrichment.vulnerability_id)
                .where(Enrichment.status == "success", Enrichment.confidence < 0.5)
                .order_by(Enrichment.confidence)
                .limit(50)
            )
        ).all()

    items = [
        {
            "cve_id": cve_id,
            "title": title,
            "dimension": e.dimension,
            "confidence": round(e.confidence or 0, 3),
            "content": e.content,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        }
        for e, cve_id, title in rows
    ]
    return {
        "total": len(items),
        "items": items,
        "note": "置信度低于 0.5 的富化结果，建议人工核验后再用于问答引用",
    }


# ========== 图谱接口 ==========


@app.get("/api/graph/stats", tags=["图谱"], summary="图谱统计")
async def graph_stats() -> dict[str, Any]:
    """知识图谱节点与关系统计"""
    from ..问答模块.graph import get_graph_retriever

    return get_graph_retriever().health_check()


@app.post("/api/graph/rebuild", tags=["图谱"], summary="重建图谱")
async def graph_rebuild() -> dict[str, Any]:
    """从 PostgreSQL 重新构建知识图谱"""
    from ..问答模块.graph import get_graph_builder

    builder = get_graph_builder()
    stats = await builder.build_from_database()
    return stats


@app.get("/api/graph/component/{keyword}", tags=["图谱"], summary="按组件查关联")
async def graph_component(keyword: str) -> dict[str, Any]:
    """查某组件的漏洞与关联组件（供应链传导）"""
    from ..问答模块.graph import get_graph_retriever

    g = get_graph_retriever()
    rows, paths = g.find_by_component(keyword, limit=15)
    related = g.find_related_components(keyword, limit=10)
    return {
        "component": keyword,
        "vulnerabilities": rows,
        "related_components": related,
        "graph_paths": [p.to_dict() for p in paths],
    }


@app.get("/api/graph/trend", tags=["图谱"], summary="趋势聚合")
async def graph_trend(technique: str | None = None) -> dict[str, Any]:
    """图谱聚合统计"""
    from ..问答模块.graph import get_graph_retriever

    return get_graph_retriever().trend_analysis(technique_keyword=technique)


# ========== 富化接口 ==========


@app.get("/api/enrich/stats", tags=["富化"], summary="富化质量统计")
async def enrich_stats() -> dict[str, Any]:
    """
    7 维富化的质量分层统计

    返回各维度的覆盖率、平均置信度与高低置信分布，
    为赛题要求「富化完整度 ≥80%」「准确率 ≥90%」提供可复核依据。
    """
    from ..富化模块.orchestrator import get_orchestrator

    return await get_orchestrator().get_statistics()


@app.get("/api/enrich/{cve_id}", tags=["富化"], summary="查询富化结果")
async def get_enrichment(cve_id: str) -> dict[str, Any]:
    """返回指定漏洞的 7 维富化详情"""
    from ..富化模块.orchestrator import get_orchestrator

    result = await get_orchestrator().get_enrichment(cve_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"未找到漏洞: {cve_id}")
    return result


@app.post("/api/enrich/{cve_id}", tags=["富化"], summary="触发单个富化")
async def trigger_enrich(cve_id: str) -> dict[str, Any]:
    """立即对指定漏洞执行 7 维富化"""
    from ..富化模块.orchestrator import get_orchestrator

    report = await get_orchestrator().enrich_one(cve_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"未找到漏洞: {cve_id}")
    return report.to_dict()


@app.post("/api/enrich-batch", tags=["富化"], summary="批量富化")
async def trigger_enrich_batch(
    limit: int = Query(20, le=200, description="处理数量"),
    ai_only: bool = Query(True, description="仅富化 AI 安全相关漏洞"),
    concurrency: int = Query(3, le=8, description="并发数，需匹配 LLM 网关承受能力"),
) -> dict[str, Any]:
    """
    批量执行 7 维富化

    赛题硬性指标富化完整度 ≥80%。
    返回值中的 completeness_rate 即为达到 80% 完整度的漏洞占比。
    """
    from ..富化模块.orchestrator import get_orchestrator

    batch = await get_orchestrator().enrich_batch(
        limit=limit, ai_only=ai_only, concurrency=concurrency
    )
    return batch.to_dict()


# ========== 情报查询 ==========


@app.get("/api/vulns", tags=["情报"], summary="漏洞情报列表")
async def list_vulns(
    ai_only: bool = Query(False, description="仅返回 AI 安全相关"),
    category: str | None = Query(None, description="按 AI 分类过滤"),
    severity: str | None = Query(None, description="按严重程度过滤"),
    source: str | None = Query(None, description="按来源过滤"),
    keyword: str | None = Query(None, description="标题关键词搜索"),
    limit: int = Query(50, le=500, description="返回条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    include_extra: bool = Query(False, description="导出用：附完整描述与富化 extra（含资产/POC/修复建议等）"),
) -> dict[str, Any]:
    """分页查询漏洞情报"""
    from ..知识库.database import get_session

    async with get_session() as session:
        stmt = select(Vulnerability)
        count_stmt = select(func.count()).select_from(Vulnerability)

        if ai_only:
            stmt = stmt.where(Vulnerability.ai_relevance_score >= 0.5)
            count_stmt = count_stmt.where(Vulnerability.ai_relevance_score >= 0.5)
        if category:
            stmt = stmt.where(Vulnerability.ai_category == category)
            count_stmt = count_stmt.where(Vulnerability.ai_category == category)
        if severity:
            stmt = stmt.where(Vulnerability.severity == severity.upper())
            count_stmt = count_stmt.where(Vulnerability.severity == severity.upper())
        if source:
            stmt = stmt.where(Vulnerability.origin_source == source)
            count_stmt = count_stmt.where(Vulnerability.origin_source == source)
        if keyword:
            pattern = f"%{keyword}%"
            # **必须搜 cve_id，不能只搜 title**。
            # 之前只搜 title，导致「从最新高危情报点编号跳到情报中心」
            # 必然搜不到——CVE 编号存在 cve_id 字段里，title 只有
            # "PraisonAI: AgentOS defaults to..." 这种描述文字。
            # 表现是：URL 带上了关键词、搜索框也显示了，但列表「暂无数据」。
            # 用户搜编号是最常见的入口，不能只覆盖标题。
            #
            # 再加 references：GHSA 这类条目常把原始编号放在参考链接里
            # （github.com/advisories/GHSA-xxxx-xxxx-xxxx）。
            # 注意 references 是 **jsonb**，不能直接 ilike，
            # 必须先 ::text 转字符串，否则 SQL 会报 type 不匹配。
            cond = or_(
                Vulnerability.title.ilike(pattern),
                Vulnerability.cve_id.ilike(pattern),
                cast(Vulnerability.references, Text).ilike(pattern),
            )
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        total = await session.scalar(count_stmt)
        stmt = stmt.order_by(Vulnerability.published_at.desc().nullslast()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).scalars().all()

        # 导出模式：批量取富化维度内容（一次 IN 查询，避免逐条 N+1）。
        # 富化维度存于 enrichments 表（vulnerability_id + dimension + content），
        # 维度清单：asset/cvss/fix/paper/poc/risk/technique。
        enrichment_by_vuln: dict[int, dict[str, Any]] = {}
        if include_extra and rows:
            from ..知识库.models import Enrichment

            vids = [v.id for v in rows]
            enr_rows = (
                await session.execute(
                    select(Enrichment).where(
                        Enrichment.vulnerability_id.in_(vids),
                        Enrichment.status == "success",
                    )
                )
            ).scalars().all()
            for e in enr_rows:
                enrichment_by_vuln.setdefault(e.vulnerability_id, {})[e.dimension] = (
                    e.content or {}
                )

        items = [
            {
                "cve_id": v.cve_id,
                # 导出模式（include_extra）给全文描述；普通列表截断 200 字即可
                "title": (v.title or "") if include_extra else (v.title or "")[:200],
                "severity": v.severity,
                "cvss_score": v.cvss_score,
                "ai_category": v.ai_category,
                "ai_relevance": round(v.ai_relevance_score, 3),
                "ai_method": v.ai_relevance_method,
                "source": v.origin_source,
                "published_at": v.published_at.isoformat() if v.published_at else None,
                "latency_seconds": v.collect_latency_seconds,
                "enrichment_status": v.enrichment_status,
                # 富化产出的风险定级，供前端展示与筛选
                "risk_priority": (v.extra or {}).get("risk_priority"),
                "risk_level": (v.extra or {}).get("risk_level"),
                "url": v.source_url,
                # 导出模式：附完整描述 + 富化 extra（资产/POC/风险/修复建议等都在这里）
                **(
                    {
                        "description": v.description,
                        "extra": v.extra or {},
                        # 按维度聚合的富化内容（导出列直接从这里取）
                        "enrichment": enrichment_by_vuln.get(v.id, {}),
                    }
                    if include_extra
                    else {}
                ),
            }
            for v in rows
        ]

    return {"total": total or 0, "limit": limit, "offset": offset, "items": items}


@app.get("/api/papers", tags=["情报"], summary="学术论文列表")
async def list_papers(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0, description="跳过前 N 条，用于分页"),
    keyword: str | None = Query(None, description="按标题/作者模糊搜索"),
    topic: str | None = Query(None, description="按 AI 主题过滤，如 RAG 安全"),
) -> dict[str, Any]:
    """
    查询 AI 安全论文

    支持关键词搜索、主题过滤与分页：论文基数已到 200+，
    只靠「取最新 N 条」既无法让用户找到关心的那篇，
    也无法翻页看完。

    ⚠ 分页必须**同时**有 offset 与**稳定的排序**：
    排序键只有一个 published_at 时，同一天/同一秒的论文顺序
    由数据库自行决定，翻页可能出现重复或漏读。
    故排序追加 arxiv_id 作为唯一 tiebreaker。
    """
    from ..知识库.database import get_session

    async with get_session() as session:
        stmt = select(Paper)
        cnt_stmt = select(func.count()).select_from(Paper)
        if keyword:
            # 标题与作者都可能命中，用 ILIKE 兼容大小写
            pattern = f"%{keyword.strip()}%"
            cond = or_(Paper.title.ilike(pattern), Paper.authors.cast(String).ilike(pattern))
            stmt = stmt.where(cond)
            cnt_stmt = cnt_stmt.where(cond)
        if topic:
            # ai_topics 是 JSONB 数组，用「包含」判断
            cond = Paper.ai_topics.contains([topic])
            stmt = stmt.where(cond)
            cnt_stmt = cnt_stmt.where(cond)

        total = await session.scalar(cnt_stmt)
        rows = (
            await session.execute(
                stmt.order_by(
                    Paper.published_at.desc().nullslast(),
                    #唯一 tiebreaker：保证翻页时同一条不会跨页重复/丢失
                    Paper.arxiv_id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        items = [
            {
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "authors": (p.authors or [])[:3],
                "ai_topics": p.ai_topics,
                "published_at": p.published_at.isoformat() if p.published_at else None,
                "pdf_url": p.pdf_url,
            }
            for p in rows
        ]
    return {"total": total or 0, "items": items}


@app.get("/api/policies", tags=["情报"], summary="政策法规列表")
async def list_policies(
    limit: int = Query(50, le=200),
    kind: str | None = Query(None, description="按政策属性过滤：法律/标准/指南/政策"),
) -> dict[str, Any]:
    """
    查询 AI 安全政策法规

    赛题要求同步采集最新的 AI 安全学术论文、技术标准与政策法规等信息，
    这是第赛题三类知识底座的对外查询入口（论文见 /api/papers，
    技术标准见 /api/techniques）。
    """
    from ..知识库.database import get_session

    async with get_session() as session:
        stmt = select(Policy)
        cnt_stmt = select(func.count()).select_from(Policy)
        if kind:
            stmt = stmt.where(Policy.policy_kind == kind)
            cnt_stmt = cnt_stmt.where(Policy.policy_kind == kind)

        total = await session.scalar(cnt_stmt)
        rows = (
            await session.execute(
                stmt.order_by(Policy.published_at.desc().nullslast()).limit(limit)
            )
        ).scalars().all()
        items = [
            {
                "doc_key": p.doc_key,
                "title": p.title,
                "summary": (p.summary or "")[:300],
                "policy_kind": p.policy_kind,
                "org": p.org,
                "region": p.region,
                "doc_numbers": p.doc_numbers or [],
                "source_name": p.source_name,
                "source_url": p.source_url,
                "published_at": p.published_at.isoformat() if p.published_at else None,
            }
            for p in rows
        ]
    return {"total": total or 0, "items": items}


@app.get("/api/techniques", tags=["情报"], summary="攻击技术列表")
async def list_techniques(limit: int = Query(50, le=200)) -> dict[str, Any]:
    """
    查询 MITRE ATLAS 攻击技术

    排序说明：ATLAS 是**版本化基线**，每条技术没有「发布时间」，
    按时间排没有意义（techniques 表也确实没有时间字段）。
    因此按 `atlas_id` 升序 —— 它是层级编号，天然有序且稳定，
    父技术（AML.T0000）会紧邻其子技术（AML.T0000.000/.001），
    正好符合按基线顺序阅读的直觉。
    """
    from ..知识库.database import get_session

    async with get_session() as session:
        # total 必须与过滤条件一致，否则前端「共 N 条」与实际可翻条数对不上
        # （原先统计全表 227，实际过滤后只有 170）。
        base = select(Technique).where(Technique.is_case_study.is_(False))
        total = await session.scalar(
            select(func.count()).select_from(base.subquery())
        )
        rows = (
            await session.execute(
                base.order_by(Technique.atlas_id.asc()).limit(limit)
            )
        ).scalars().all()
        items = [
            {
                "atlas_id": t.atlas_id,
                "name": t.name,
                "tactic": t.tactic,
                "description": (t.description or "")[:180],
                "platform": t.platform,
                "maturity": t.maturity,
            }
            for t in rows
        ]
    return {"total": total or 0, "items": items}


@app.get("/api/kb-freshness", tags=["知识底座"], summary="赛题三类知识底座的条数与新鲜度")
async def kb_freshness() -> dict[str, Any]:
    """
    赛题三类知识底座的**条数 + 最近采集时间**

    ---------- 为什么要单独开一个接口 ----------
    前端卡片要展示「共 N 篇 · 更新于 X」，但：
      - N 来自 /api/papers、/api/techniques、/api/policies（各自分页接口）
      - X 来自 /api/monitor/overview 的 last_runs（调度器视角）
    若让前端自己拼，要么发4 次请求，要么在前端做无意义的映射
    （知识底座 → 数据源代码 arxiv/atlas/policy）。

    这里在后端完成「底座 → 数据源」的映射并一次返回，
    前端只负责渲染。**映射关系属于后端知识，不该泄漏到前端。**
    """
    from ..知识库.database import get_session
    from ..知识库.models import CollectionLog

    # 赛题三类知识底座→ 产出它的数据源代码
    BASIN_SOURCES = {"papers": ["arxiv"], "techniques": ["atlas"], "policies": ["policy", "rss"]}

    async with get_session() as session:
        counts = {
            "papers": await session.scalar(select(func.count()).select_from(Paper)),
            "techniques": await session.scalar(
                select(func.count())
                .select_from(Technique)
                .where(Technique.is_case_study.is_(False))
            ),
            "policies": await session.scalar(select(func.count()).select_from(Policy)),
            # 各底座最新一条内容的时间（业务时间，与采集时间互补）
            "papers_latest": await session.scalar(select(func.max(Paper.published_at))),
            "policies_latest": await session.scalar(select(func.max(Policy.published_at))),
        }

        # 最近一次**成功**采集时间，直接查 collection_logs。
        #
        # ---------- 为什么不用 collector_cursors ----------
        # monitor/overview 的 last_runs 来自 collector_cursors（增量游标表），
        # 但游标只对「增量拉取」的源创建 —— 全量覆盖型的 atlas 不建游标，
        # 于是 last_runs 里恰好缺 atlas（实测：atlas 明明 10-04 采集成功
        # 拉回 227 条，last_runs 却没有它，前端只能显示「从未更新」的假象）。
        #
        # collection_logs 记录每一次采集的事实，才是权威来源。
        run_rows = await session.execute(
            select(CollectionLog.source_code, CollectionLog.finished_at)
            .where(CollectionLog.status == "success", CollectionLog.finished_at.isnot(None))
            .order_by(CollectionLog.finished_at.desc())
        )
        last_runs: dict[str, str] = {}
        for code, finished_at in run_rows:
            # 同一源可能有多条成功记录，只取最新一条
            last_runs.setdefault(code, finished_at.isoformat())

    # 调度间隔（秒）：用于判断「多久没更新算陈旧」。
    # **从采集器实时读取**而不是硬编码 —— 硬编码会在有人调整
    # default_interval 后悄悄失准（判定结果错误却没人发现）。
    collectors = get_collectors()
    # 用**有效周期**（含运维自定义值）而不是 default_interval：
    # 陈旧判定若按默认值算，用户把某源调慢后会反复收到假告警。
    intervals = {
        code: effective_interval(code) for code, _ in collectors.items()
    }
    now = datetime.now(UTC)

    items = []
    for basin, sources in BASIN_SOURCES.items():
        runs = [last_runs.get(s) for s in sources if last_runs.get(s)]
        # 用 datetime 比较而非字符串 max：ISO 字符串带时区偏移时
        # 字典序不等于时间序（+08:00 会排在 Z 之后），排序不可靠。
        parsed = [datetime.fromisoformat(r) for r in runs]
        last_at = max(parsed) if parsed else None
        # 陈旧判定用**最宽松**的那个源作为基准（取 max 间隔）。
        #
        # 为什么不是取 min：多源底座里各源职责不同，间隔也天然不同。
        # 以 policies 为例 —— policy 源 12 小时一轮是主采集，
        # rss 源 1 小时一轮只是社区快讯补充。若取 min(=1h)当阈值，
        # 只要 rss 稍有延迟就整个底座被判「陈旧」，
        # 这是**用最不可靠的源去否定整体状态**，会制造假告警。
        # 任一源按自己的节奏更新过，就算这个底座是健康的。
        max_interval = max(intervals.get(s, 43_200) for s in sources)
        stale = last_at is None or (now - last_at).total_seconds() > max_interval * 2

        items.append(
            {
                "basin": basin,
                "total": int(counts.get(basin) or 0),
                # 该底座所有来源中最近的一次采集时间
                "last_collected_at": last_at.isoformat() if last_at else None,
                "sources": sources,
                # 距今秒数，前端可据此显示「x 小时前采集」
                "seconds_since_collect": (
                    int((now - last_at).total_seconds()) if last_at else None
                ),
                # 是否已陈旧（超过 2 个调度周期未更新）
                "stale": stale,
                "interval_seconds": max_interval,
                # 最新内容发布时间（业务时间），无则 null
                "latest_content_at": (
                    counts[f"{basin}_latest"].isoformat()
                    if counts.get(f"{basin}_latest")
                    else None
                ),
            }
        )

    return {
        "items": items,
        "generated_at": now.isoformat(),
    }


@app.get("/api/stats", tags=["情报"], summary="分类统计")
async def stats() -> dict[str, Any]:
    """
    AI 分类与严重程度分布

    用于前端看板图表。
    """
    from ..知识库.database import get_session

    async with get_session() as session:
        cat_rows = await session.execute(
            select(Vulnerability.ai_category, func.count())
            .where(Vulnerability.ai_category.isnot(None))
            .group_by(Vulnerability.ai_category)
        )
        # 严重度大小写归一：采集源写入的 severity 存在
        # "Critical"/"CRITICAL" 混杂，直接分组会把同一档拆成多根柱
        # （实测 Critical 2 + CRITICAL 803 被拆开），
        # 表现为横轴标签与柱形对不上。用 upper() 合并后再分组。
        sev_rows = await session.execute(
            select(func.upper(Vulnerability.severity), func.count())
            .where(Vulnerability.severity.isnot(None))
            .group_by(func.upper(Vulnerability.severity))
        )
        method_rows = await session.execute(
            select(Vulnerability.ai_relevance_method, func.count()).group_by(
                Vulnerability.ai_relevance_method
            )
        )

    return {
        "by_ai_category": {c: n for c, n in cat_rows},
        "by_severity": {s: n for s, n in sev_rows},
        "by_classify_method": {m: n for m, n in method_rows},
    }


@app.get("/api/stats/knowledge-base", tags=["情报"], summary="知识底座总览")
async def knowledge_base_stats() -> dict[str, Any]:
    """
    赛题三类知识底座的实时数量与富化覆盖情况

    赛题要求同步采集最新的 AI 安全学术论文、技术标准与政策法规等信息，
    构建完整的 AI 安全知识底座。这个端点把三类底座的规模显式暴露出来，
    供前端展示（而不是把数字硬编码在页面上）。
    """
    from ..知识库.database import get_session

    async with get_session() as session:
        vuln_total = await session.scalar(
            select(func.count()).select_from(Vulnerability)
        )
        ai_total = await session.scalar(
            select(func.count())
            .select_from(Vulnerability)
            .where(Vulnerability.ai_category.isnot(None))
        )
        paper_total = await session.scalar(select(func.count()).select_from(Paper))
        tech_total = await session.scalar(select(func.count()).select_from(Technique))
        policy_total = await session.scalar(select(func.count()).select_from(Policy))
        enriched_total = await session.scalar(
            select(func.count())
            .select_from(Vulnerability)
            .where(Vulnerability.ai_category.isnot(None))
            .where(Vulnerability.technique_refs.isnot(None))
        )
        source_total = await session.scalar(select(func.count()).select_from(Source))

    return {
        # 情报主体
        "vulnerabilities": vuln_total or 0,
        "ai_vulnerabilities": ai_total or 0,
        "enriched_ai_vulnerabilities": enriched_total or 0,
        "sources": source_total or 0,
        # 赛题三类知识底座
        "papers": paper_total or 0,
        "techniques": tech_total or 0,
        "policies": policy_total or 0,
        # 底座合计
        "knowledge_base_total": (paper_total or 0) + (tech_total or 0) + (policy_total or 0),
    }


# ========== 启动入口 ==========


# ------------------------------------------------------------
# 前端静态托管（生产 / 打包模式）
# ------------------------------------------------------------
# 开发环境前端由 vite dev（5173）提供，此处仅是额外便利；
# 打包环境（exe）由本段单进程提供前后端，用户只访问 8000 端口。
# 注意：必须挂载在所有 API 路由注册**之后** —— 否则 catch-all 会吞掉接口。
_frontend_dist = resource("前端界面", "dist")
if _frontend_dist.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # 静态资源（js/css/图片）走 StaticFiles（带缓存与 MIME）
    _assets = _frontend_dist / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="frontend-assets")

    # SPA fallback：静态文件优先，找不到的回退 index.html（前端路由接管）
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):  # noqa: ANN202
        # API / 文档路径不由本函数兜底（未匹配到具体路由时按 404 处理）
        if full_path.startswith(("api", "docs", "openapi", "redoc")):
            raise HTTPException(status_code=404)
        candidate = _frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_frontend_dist / "index.html")


def main() -> None:
    """以开发模式启动服务"""
    import uvicorn

    uvicorn.run(
        "项目源码.后端服务.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )


if __name__ == "__main__":
    main()
