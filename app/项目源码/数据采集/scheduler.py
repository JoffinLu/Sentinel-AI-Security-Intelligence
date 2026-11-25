"""
定时采集调度器

按各数据源声明的间隔执行增量采集，对应"从采集到问答的全流程自动化"要求。
调度间隔与"监测时效性"赛题性能指标直接相关：
漏洞库源 6 小时、社区源 1 小时、论文与标准源 12-24 小时。
"""

from __future__ import annotations

import asyncio
import datetime as dt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..后端服务 import metrics
from ..后端服务.logger import get_logger
from .ingest_pipeline import IngestPipeline

# 别名导入单独一条：isort 默认不把 `as` 别名与普通名合并进同一语句
# （combine_as_imports=False），混写会被 I001 拦下
from .registry import effective_interval as _registry_interval
from .registry import (
    get_collector,
    get_collectors,
    get_interval_overrides,
    set_interval_override,
)

logger = get_logger("scheduler")

# 周期档位阶梯（秒 → 展示名）。
#
# 为什么是这几个数：全部是 30 分钟的整数倍，且以「整小时/整天」收尾。
# 3 小时、5 小时、7 小时这类非整除值会让「今天几点跑、明天几点跑」
# 逐日漂移，运维无法预判，也难以与上游的固定发布节奏对齐。
# 需要更细的值时，接口层允许传区间内任意秒数，不必拘泥于阶梯。
INTERVAL_LADDER: list[tuple[int, str]] = [
    (600, "10 分钟"),
    (900, "15 分钟"),
    (1800, "30 分钟"),
    (3600, "1 小时"),
    (7200, "2 小时"),
    (21600, "6 小时"),
    (43200, "12 小时"),
    (86400, "1 天"),
    (604800, "7 天"),
]


def _format_interval(seconds: int) -> str:
    """
    秒 → 人类可读（选能整除的最大单位）

    踩过的坑：原来先判「整除周」再 `seconds // 604800`，
    604800 被整除周命中却算出 1，配上「天」变成「1 天」
    —— ATLAS 的上限提示因此显示成「1 天 ~ 1 天」。
    现在统一用「天/小时/分钟」三档（与阶梯标签同一套词汇），
    604800 → 7 天，不再引入「周」这种第二种天数说法。
    """
    if seconds <= 0:
        return f"{seconds} 秒"
    for unit_seconds, unit_name in ((86400, "天"), (3600, "小时"), (60, "分钟")):
        if seconds % unit_seconds == 0:
            return f"{seconds // unit_seconds} {unit_name}"
    return f"{seconds} 秒"


class CollectionScheduler:
    """
    采集调度器

    单例运行，start 后按各源间隔自动执行采集。
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._pipeline = IngestPipeline()
        self._started = False
        # 自动采集是否被手动暂停。暂停时调度器仍在运行（界面可查数据），
        # 但所有定时任务被挂起，不再自动发起采集。
        self._paused = False
        # 防止同一源并发采集（长耗时任务会重叠）
        self._running: set[str] = set()
        # 后台任务（如启动补采）的强引用。
        # **必须持有**：`asyncio.create_task` 的返回值若无人引用，
        # 任务可能在执行途中被垃圾回收 —— 实测症状是「快的跑完了、
        # 慢的静默消失」（0.2s 的 arxiv 成功，115s 的 osv 无任何记录）。
        self._background: set[asyncio.Task] = set()
        # 各源上次执行结果：{code: {"at": datetime, "ok": bool, "new": int}}
        # APScheduler 只给next_run_time，不给上次执行时间，
        # 而「上次执行」恰恰是证明自动采集确实在跑的关键证据——
        # 监控看板要展示它，操作员才能确认自动化不是摆设。
        self._last_exec: dict[str, dict[str, object]] = {}
        # 运维自定义的周期覆盖值存放在 registry（见 registry.set_interval_override
        # 的说明：它是唯一不造成导入环的中转站）。此处不持有副本，
        # 避免两处状态不一致。
        # 单次采集后自动富化的条数上限。防止冷启动回填时一次触发
        # 上千条富化，把 LLM 网关打爆（429 熔断后要等冷却）。
        self.auto_enrich_batch_limit = 20
        # 自动富化总开关。设False 则只采集不富化（便于对比排查）
        self.auto_enrich_enabled = True
        # 单次采集后自动向量化的条数上限。本地 bge-small-zh 约 30 条/秒，
        # 20 条不到 1 秒；设上限是为了防止冷启动大批量回填时占用过久。
        self.auto_vectorize_batch_limit = 20
        # 自动向量化总开关
        self.auto_vectorize_enabled = True

    @property
    def running(self) -> bool:
        return self._started

    @property
    def paused(self) -> bool:
        """
        自动采集是否已暂停

        与 `running` 的区别很关键：
          running=False → 调度器没启动（服务未启动）
          running=True + paused=True → 调度器在跑，但所有定时任务被挂起
        运维人员「不想采集但还想用界面查数据」时需要的是后者。
        """
        return bool(self._paused)

    def pause(self) -> dict[str, object]:
        """
        暂停所有定时采集

        用 APScheduler 的 pause_job 而非 shutdown：
        任务保留在调度器里，resume 可原样恢复，
        且 next_run_time 被保留——恢复后能看到原本排期的时间点。
        直接 shutdown 会丢失所有任务，恢复时得全部重建。
        """
        if not self._started:
            return {"ok": False, "reason": "调度器未启动，无需暂停"}
        if self._paused:
            return {"ok": True, "paused": True, "reason": "已处于暂停状态"}

        for job in self._scheduler.get_jobs():
            self._scheduler.pause_job(job.id)
        self._paused = True
        logger.info("scheduler_paused", jobs=len(self._scheduler.get_jobs()))
        return {
            "ok": True,
            "paused": True,
            "jobs": len(self._scheduler.get_jobs()),
        }

    def resume(self) -> dict[str, object]:
        """恢复所有定时采集"""
        if not self._started:
            return {"ok": False, "reason": "调度器未启动，请先启动服务"}
        if not self._paused:
            return {"ok": True, "paused": False, "reason": "调度器未处于暂停状态"}

        for job in self._scheduler.get_jobs():
            self._scheduler.resume_job(job.id)
        self._paused = False
        logger.info("scheduler_resumed", jobs=len(self._scheduler.get_jobs()))
        return {
            "ok": True,
            "paused": False,
            "jobs": len(self._scheduler.get_jobs()),
        }

    def start(self) -> None:
        """启动调度"""
        if self._started:
            return

        for code, collector in get_collectors().items():
            self._scheduler.add_job(
                self._run_job,
                trigger=IntervalTrigger(seconds=self.effective_interval(code)),
                args=[code],
                id=f"collect_{code}",
                name=f"采集 {collector.name}",
                replace_existing=True,
                # 错峰启动，避免所有源同时打向上游
                jitter=30,
                # 错过执行时间后 1 小时内仍补跑。APScheduler 默认只容忍 1 秒，
                # 网络抖动或事件循环繁忙就足以让一次采集被直接丢弃。
                # 注意：这只覆盖「调度器在运行、任务被拖延」的情况；
                # 进程整个停过导致的错过由 catch_up_missed() 负责。
                misfire_grace_time=3600,
                # 积压多次只补跑一次（默认即 True，显式写出以免误解）
                coalesce=True,
                # 同一个源不允许并发运行（默认即 1，显式写出以免误解）
                max_instances=1,
            )
            logger.info(
                "job_scheduled",
                source=code,
                interval_seconds=self.effective_interval(code),
                custom=get_interval_overrides().get(code),
            )

        self._scheduler.start()
        self._started = True
        logger.info("scheduler_started", jobs=len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        """停止调度"""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("scheduler_stopped")

    # ---------- 周期自定义（监控看板「周期」列可调） ----------

    def effective_interval(self, code: str) -> int:
        """
        该源当前**实际生效**的周期（秒）

        解析顺序：运维自定义覆盖值 → 采集器 default_interval。
        没有覆盖记录时返回默认值，因此默认行为与改造前完全一致。

        同步方法（不查库）：覆盖值在启动时由 load_interval_overrides()
        一次性载入 registry，set_interval/reset_interval 同步维护。
        单进程部署下内存即权威；库是重启后的恢复来源。
        """
        return _registry_interval(code)

    async def load_interval_overrides(self) -> None:
        """
        从 scheduler_settings 表载入自定义周期

        必须在 start() **之前**调用（start 按有效周期建 job）。
        查询失败不抛异常：此时按默认周期启动，只是自定义值本次不生效
        —— 采集可用性优先于配置恢复。
        """
        try:
            from sqlalchemy import select

            from ..知识库.database import get_session
            from ..知识库.models import SchedulerSetting

            async with get_session() as session:
                rows = (
                    (await session.execute(select(SchedulerSetting)))
                    .scalars()
                    .all()
                )
            overrides = {r.source_code: int(r.interval_seconds) for r in rows}
            # 先清后写：避免上次残留的覆盖值在本进程继续生效
            for stale in set(get_interval_overrides()) - set(overrides):
                set_interval_override(stale, None)
            for code, seconds in overrides.items():
                set_interval_override(code, seconds)
            if overrides:
                logger.info("interval_overrides_loaded", overrides=overrides)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interval_overrides_load_failed",
                error=str(exc)[:200],
            )
    def interval_options(self, code: str) -> list[dict[str, object]]:
        """
        该源可选周期档位（供前端下拉）

        只返回落在 [min_interval, max_interval] 内的档位。
        区间由采集器按上游限流声明，越界档位**不出现在下拉里**，
        并在界面上单独说明原因 —— 比「给了选项但报了错」更可理解。

        默认值一定在结果中：即便某天 default_interval 不在阶梯上，
        用户仍能选回默认，不会被困在自定义值上。
        """
        collector = get_collector(code)
        if collector is None:
            return []
        lo = int(getattr(collector, "min_interval", 3600))
        hi = int(getattr(collector, "max_interval", 86400))
        current = self.effective_interval(code)

        options = [
            {"seconds": seconds, "label": label, "current": seconds == current}
            for seconds, label in INTERVAL_LADDER
            if lo <= seconds <= hi
        ]
        default = int(collector.default_interval)
        if lo <= default <= hi and not any(o["seconds"] == default for o in options):
            options.append(
                {
                    "seconds": default,
                    "label": _format_interval(default),
                    "current": default == current,
                }
            )
            options.sort(key=lambda o: int(o["seconds"]))  # type: ignore[arg-type,return-value]
        return options

    async def set_interval(self, code: str, seconds: int) -> dict[str, object]:
        """
        设置某数据源的采集周期

        先校验区间再落库，最后才重排 job —— 顺序很重要：
        校验失败时不能留下任何副作用（不写库、不改排期）。

        校验通过但调度器未启动时只落库，返回 applied_immediately=False，
        下次 start() 会按新周期建 job。

        Args:
            code: 数据源代码
            seconds: 目标周期（秒）。允许区间内任意值，不必是阶梯档位；
                     阶梯只是前端下拉给的便捷选项。
        """
        collector = get_collector(code)
        if collector is None:
            return {"ok": False, "error": f"未知数据源: {code}"}
        lo = int(getattr(collector, "min_interval", 3600))
        hi = int(getattr(collector, "max_interval", 86400))
        if not lo <= seconds <= hi:
            return {
                "ok": False,
                "error": (
                    f"{collector.name} 的周期需在 "
                    f"{_format_interval(lo)} ~ {_format_interval(hi)} 之间"
                ),
                # 附带依据：让调用方知道这个区间不是随意划的
                "reason": getattr(collector, "interval_basis", ""),
                "min_seconds": lo,
                "max_seconds": hi,
            }

        await self._persist_interval(code, int(seconds))
        set_interval_override(code, int(seconds))
        applied = self._reschedule(code, int(seconds))
        logger.info("interval_updated", source=code, seconds=seconds, applied=applied)
        return {
            "ok": True,
            "source_code": code,
            "interval_seconds": int(seconds),
            "applied_immediately": applied,
        }

    async def reset_interval(self, code: str) -> dict[str, object]:
        """恢复某数据源的默认周期（删除覆盖记录）"""
        collector = get_collector(code)
        if collector is None:
            return {"ok": False, "error": f"未知数据源: {code}"}
        await self._delete_interval(code)
        set_interval_override(code, None)
        default = int(collector.default_interval)
        applied = self._reschedule(code, default)
        logger.info("interval_reset", source=code, seconds=default, applied=applied)
        return {
            "ok": True,
            "source_code": code,
            "interval_seconds": default,
            "applied_immediately": applied,
        }

    def _reschedule(self, code: str, seconds: int) -> bool:
        """
        重排该源 job 的周期，返回是否立即生效

        用 reschedule_job 而不是删了重建：job 的 jitter /
        misfire_grace_time / max_instances 等配置会被保留，
        重建则要在这里重复一遍，两处迟早不一致。
        """
        job_id = f"collect_{code}"
        if not self._started or self._scheduler.get_job(job_id) is None:
            return False
        try:
            self._scheduler.reschedule_job(
                job_id, trigger=IntervalTrigger(seconds=seconds)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("reschedule_failed", source=code, error=str(exc)[:150])
            return False
        return True

    async def _persist_interval(self, code: str, seconds: int) -> None:
        """写入/更新覆盖值（get_session 退出时自动提交）"""
        from sqlalchemy import select

        from ..知识库.database import get_session
        from ..知识库.models import SchedulerSetting

        async with get_session() as session:
            row = await session.scalar(
                select(SchedulerSetting).where(SchedulerSetting.source_code == code)
            )
            if row is None:
                session.add(
                    SchedulerSetting(source_code=code, interval_seconds=seconds)
                )
            else:
                row.interval_seconds = seconds

    async def _delete_interval(self, code: str) -> None:
        """删除覆盖记录，恢复默认周期"""
        from sqlalchemy import delete

        from ..知识库.database import get_session
        from ..知识库.models import SchedulerSetting

        async with get_session() as session:
            await session.execute(
                delete(SchedulerSetting).where(SchedulerSetting.source_code == code)
            )

    async def _run_job(self, code: str) -> None:
        """调度任务入口"""
        if code in self._running:
            logger.warning("job_skipped_concurrent", source=code)
            return
        self._running.add(code)
        try:
            await self.collect_now(code, trigger="schedule")
        finally:
            self._running.discard(code)

    async def collect_now(self, code: str, trigger: str = "schedule") -> dict:
        """
        立即执行一次采集

        同时被定时调度与 API 手动触发复用。
        注意：本方法不检查 paused 状态——暂停只挂起定时任务，
        手动触发始终可用。否则暂停后操作员连「临时采一次」都做不到。

        Args:
            code: 数据源代码
            trigger: 触发来源，如实落库（schedule=定时 / manual=界面手动 /
                     api=接口调用）。不传会默认按 schedule 记，
                     导致手动采集在数据库里也长得像自动采集。
        """
        collector = get_collector(code)
        if collector is None:
            return {"error": f"未知数据源: {code}"}

        cursor = await self._pipeline.get_cursor(code)
        report = await self._pipeline.run(collector, cursor, classify=True, trigger=trigger)

        # 源可用性指标
        metrics.source_health.labels(source=code).set(0 if report.error else 1)

        # 记录本次执行结果，供监控看板展示「上次执行」
        self._last_exec[code] = {
            "at": dt.datetime.now(dt.UTC),
            "ok": not report.error,
            "new": report.new,
            "elapsed_ms": report.elapsed_ms,
        }

        result = report.to_dict()

        # ---- 自动富化新入库的 AI 漏洞 ----
        # 赛题要求情报富化模块：对采集到的漏洞信息进行多维扩展与深度加工，
        # 采集与富化应当是连贯流水线，而不是靠人工点按钮——
        # 否则新采到的漏洞要等人工介入才被富化，问答层就检索不到它们。
        if report.ai_security and not report.error and self.auto_enrich_enabled:
            enriched = await self._auto_enrich(report.ai_security)
            result["auto_enriched"] = enriched

        # ---- 自动向量化新增数据 ----
        # 优化指标（2）要求「从采集到问答的全流程自动化」。
        # 向量化是采集→问答之间的关键一环：问答层靠 pgvector 做语义检索，
        # 新数据不入向量库就检索不到。缺了这一步，自动化是断的。
        if report.new > 0 and not report.error and self.auto_vectorize_enabled:
            result["auto_vectorized"] = await self._auto_vectorize(report.new)

        logger.info(
            "scheduled_collect_done",
            source=code,
            new=report.new,
            ai_security=report.ai_security,
            failed=bool(report.error),
        )
        return result

    async def _auto_vectorize(self, new_count: int) -> dict[str, object]:
        """
        对新增数据自动向量化

        优化指标（2）「系统自动化程度：从采集到问答的全流程自动化」——
        采集与富化都已自动，但向量化此前只能手动跑
        （`python -m 项目源码.问答模块.vectorize`），
        导致新数据在问答层检索不到，自动化链路是断的。

        嵌入模型是本地 bge-small-zh，编码速度约 30 条/秒，
        20 条约需 0.6 秒，成本很低，不需要设太保守的上限。
        """
        if new_count <= 0:
            return {"processed": 0, "reason": "无新增数据"}

        try:
            from ..问答模块.vectorize import (
                vectorize_papers,
                vectorize_policies,
                vectorize_vulns,
            )

            limit = min(new_count, self.auto_vectorize_batch_limit)
            results: list[dict[str, object]] = []

            # 论文与政策是独立表，需分别处理（漏洞表是问答主检索对象）
            vuln_res = await vectorize_vulns(limit=limit)
            results.append(vuln_res)

            # 只有当这两个表确实有新增时才跑——它们各自的采集周期更长，
            # 大多数漏洞采集轮次不会产生新论文/政策，跳过以省时间
            paper_res = await vectorize_papers(limit=limit)
            if paper_res.get("processed"):
                results.append(paper_res)
            policy_res = await vectorize_policies(limit=limit)
            if policy_res.get("processed"):
                results.append(policy_res)

            processed = sum(int(r.get("processed", 0)) for r in results)
            total_s = round(sum(float(r.get("elapsed_s", 0)) for r in results), 2)
            logger.info(
                "auto_vectorize_done",
                new_records=new_count,
                processed=processed,
                elapsed_s=total_s,
            )
            return {
                "candidates": new_count,
                "processed": processed,
                "elapsed_s": total_s,
            }
        except Exception as exc:  # noqa: BLE001
            # 嵌入模型加载失败等原因不应影响采集主流程。
            # 漏掉的向量可以事后用 vectorize 脚本补跑。
            logger.warning("auto_vectorize_failed", error=str(exc)[:200])
            return {"error": str(exc)[:200]}

    async def _auto_enrich(self, ai_count: int) -> dict[str, object]:
        """
        对新入库的 AI 漏洞自动执行 7 维富化（经智能体编排器）

        优化指标（1）要求「Agent 自主决策、任务编排与工具调用」，
        所以这里不再直接调 `enrich_batch`，而是走 LangGraph 编排器——
        让 triage（优先级判定）真正参与决策：已富化过的、非高危的
        会被智能体主动跳过，而不是无差别重跑。

        只处理「尚未富化」的记录——重复富化会反复调 LLM，既慢又烧配额。
        实现方式是先查出未富化的 CVE 列表；
        不能直接用 ai_only=True，因为那会把已富化的也捞出来重跑
        （enrich_batch 内部排序是「未富化优先，但会用已富化的补满 limit」）。
        """
        if ai_count <= 0:
            return {"skipped": 0, "reason": "无新增 AI 漏洞"}

        # ---- 限流预检：冷却期内直接跳过本轮 ----
        # LLM 处于 429 冷却期时，逐条重试必然失败（每条空耗
        # 3 次重试 + 退避 + fallback ≈ 20-40 秒），几十条会把
        # 采集接口拖到十几分钟。数据已入库不会丢，
        # 等冷却结束后的下一轮采集再消化。
        from ..后端服务.llm_client import get_llm_client

        if get_llm_client().is_rate_limited():
            logger.info("auto_enrich_skipped_rate_limited", ai_count=ai_count)
            return {
                "candidates": ai_count,
                "processed": 0,
                "reason": "LLM 限流冷却期，本轮跳过（数据已入库，下轮补富化）",
            }

        try:
            from sqlalchemy import select

            from ..智能体编排.orchestrator import get_workflow
            from ..知识库.database import get_session
            from ..知识库.models import Enrichment, Vulnerability

            # 取出尚未富化的 AI 漏洞（新采集入库的）
            async with get_session() as session:
                done = set(
                    (
                        await session.execute(
                            select(Vulnerability.cve_id)
                            .join(
                                Enrichment,
                                Enrichment.vulnerability_id == Vulnerability.id,
                            )
                            .where(Vulnerability.ai_relevance_score >= 0.5)
                            .distinct()
                        )
                    ).scalars()
                )
                pending = (
                    await session.execute(
                        select(Vulnerability.cve_id)
                        .where(Vulnerability.ai_relevance_score >= 0.5)
                        # 不加 limit：加了会截断，可能把待富化记录挡在窗口外
                        # （实测：待富化的 CVE 排在第 61 位之后，
                        #  limit 60 就会漏掉它，导致自动富化永远不触发）
                    )
                ).scalars().all()

            targets = [c for c in pending if c not in done][: self.auto_enrich_batch_limit]

            if not targets:
                logger.info("auto_enrich_no_pending", ai_count=ai_count)
                return {"candidates": ai_count, "processed": 0, "reason": "无待富化记录"}

            # 逐条走编排器：每条都是一次完整的智能体决策过程。
            # 并发压到 2——编排器内部的富化节点要调 LLM，
            # 与采集共用同一网关限流池，并发高会触发 429 熔断。
            sem = asyncio.Semaphore(2)

            async def _one(cve_id: str) -> dict[str, object]:
                async with sem:
                    state = await get_workflow().run(workflow_type="enrich", cve_id=cve_id)
                    return {
                        "cve_id": cve_id,
                        "status": state.status.value
                        if hasattr(state.status, "value")
                        else str(state.status),
                        "elapsed_ms": state.elapsed_ms,
                        "nodes": len(state.node_results),
                        # 记录智能体的决策理由——这是「可解释性」的举证材料
                        "triage": (state.get("triage") or {}).get("reason")
                        or next(
                            (n.reasoning for n in state.node_results
                             if n.name == "triage" and n.reasoning),
                            None,
                        ),
                    }

            results = await asyncio.gather(
                *[_one(c) for c in targets], return_exceptions=True
            )

            # 回退保障：若编排器整体失效（而非个别条目失败），
            # 退回批量富化。编排化是「加分项」，数据完整性是「底线」——
            # 不能因为编排出问题就让新漏洞富化不到。
            if all(isinstance(r, BaseException) for r in results) and results:
                logger.warning(
                    "auto_enrich_orchestrator_failed_fallback",
                    errors=str(results[0])[:150],
                )
                from ..富化模块.orchestrator import get_orchestrator

                fb = await get_orchestrator().enrich_batch(
                    limit=len(targets), ai_only=False, cve_ids=targets, concurrency=2
                )
                return {
                    "candidates": ai_count,
                    "selected": len(targets),
                    "processed": getattr(fb, "succeeded", 0),
                    "failed": getattr(fb, "failed", 0),
                    "via": "batch_fallback",
                }

            ok = sum(1 for r in results if isinstance(r, dict))
            skipped = sum(
                1 for r in results
                if isinstance(r, dict) and r.get("status") in ("skipped", "failed")
            )
            logger.info(
                "auto_enrich_done",
                candidates=ai_count,
                selected=len(targets),
                processed=ok,
                skipped_or_failed=skipped,
                via="langgraph_orchestrator",
            )
            return {
                "candidates": ai_count,
                "selected": len(targets),
                "processed": ok,
                "skipped_or_failed": skipped,
                "via": "langgraph_orchestrator",
                "details": [r for r in results if isinstance(r, dict)],
            }
        except Exception as exc:  # noqa: BLE001
            # 富化失败绝不能影响采集主流程——数据已经入库了，
            # 富化可以事后补跑，这里只记日志
            logger.warning("auto_enrich_failed", error=str(exc)[:200])
            return {"error": str(exc)[:200]}

    async def collect_all(
        self, trigger: str = "api", max_concurrency: int = 4
    ) -> list[dict]:
        """
        并发采集全部数据源

        为什么从串行改成并发：
          串行要把 9 个源逐个跑完，实测约 3.8 分钟
          （osv 115s + poc 32s + 其余都在 17s 内）；
          而定时任务本身是**各自并发的**（APScheduler 每个源一个独立 job），
          两者行为不一致——手动触发没有理由比自动慢这么多。

        并发是否安全（三点已核实）：
          · 限速：每个采集器持有**自己的** RateLimiter（2 rps），
            且打的是不同服务器——并发不会让任何单一上游多收一个请求
          · 数据库：pipeline.run 每次 async with get_session() 开独立会话，
            连接池 20+10=30，9 个并发只占 9 条
          · 状态：_last_exec[code] 与 prometheus 指标都按 source 区分，
            不同源之间没有共享可变状态

        为什么仍加并发上限：9 个源同时跑会推高本机瞬时内存与出口带宽，
          用信号量限制在 4 个更稳，同时仍比串行快一倍以上。

        Args:
            trigger: 触发来源，供调用方如实标注（api / manual）。
                     默认 "api"——这个方法由接口批量触发，
                     不是定时任务，写成 schedule 会掩盖真实来源。
            max_concurrency: 同时进行的采集数上限。
        """
        codes = list(get_collectors())
        sem = asyncio.Semaphore(max_concurrency)

        async def _one(code: str) -> dict:
            async with sem:
                # 与 _run_job 一致地防重复：定时任务正在跑该源时跳过，
                # 否则同一个源会被同时采两遍（浪费上游配额）
                if code in self._running:
                    return {"source_code": code, "skipped": "该源正在采集中"}
                self._running.add(code)
                try:
                    return await self.collect_now(code, trigger=trigger)
                finally:
                    self._running.discard(code)

        # return_exceptions=True：某个源抛异常不应中断其余源的采集。
        # 串行版本会因第一个异常直接跳出循环，使后续源全部不执行。
        raw = await asyncio.gather(*(_one(c) for c in codes), return_exceptions=True)
        results: list[dict] = []
        for code, item in zip(codes, raw, strict=True):
            if isinstance(item, BaseException):
                results.append({"source_code": code, "error": str(item)[:200]})
            else:
                results.append(item)
        return results

    async def catch_up_missed(self) -> list[str]:
        """
        启动时补跑「已错过周期」的采集

        为什么需要它：
          APScheduler 默认使用 MemoryJobStore，进程重启后 job 状态丢失，
          所有任务按「启动时刻 + 周期」重新排期——**错过的周期永远不补**。
          `misfire_grace_time` 解决不了这个场景（它只对「调度器在运行、
          任务被事件循环拖延」有效，对「整个进程停过」无效）。

          实测后果：系统当天多次重启后，5 个 12 小时周期的源
          （osv / arxiv / poc / policy / atlas）超过 24 小时没执行过，
          而看板的「上次」一列一直停在昨天，容易被误读成采集器故障。

        做法：查每个源在 collection_logs 中最后一次**成功**的时间，
          若「距今 > 该源周期」则立即以 schedule 来源补跑一次。

        补跑用 create_task 异步发起，不阻塞应用启动——
        osv 一次要 115 秒，若 await 会把整个启动流程卡住。
        """
        from sqlalchemy import func, select

        from ..知识库.database import get_session
        from ..知识库.models import CollectionLog

        now = dt.datetime.now(dt.UTC)
        overdue: list[str] = []

        try:
            async with get_session() as session:
                # 只取 code：周期判定走 effective_interval（覆盖值→默认值），
                # 不再需要 collector 实例
                for code in get_collectors():
                    last = await session.scalar(
                        select(func.max(CollectionLog.started_at))
                        .where(CollectionLog.source_code == code)
                        .where(CollectionLog.status == "success")
                    )
                    if last is None:
                        continue
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=dt.UTC)
                    # 用**有效周期**而不是 default_interval：运维把某源
                    # 调慢后，超期判定必须跟着放宽，否则会把它判成「超期未采」
                    # 而反复补跑 —— 用户刚设成 12 小时，系统却按 2 小时催它。
                    if (now - last).total_seconds() > self.effective_interval(code):
                        overdue.append(code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("catch_up_query_failed", error=str(exc)[:200])
            return []

        for code in overdue:
            # 走 _run_job 而不是 collect_now：前者自带「正在采集中则跳过」，
            # 避免与定时任务撞车
            task = asyncio.create_task(self._run_job(code))
            # 强引用必须在任务完成前一直持有，否则会被 GC 回收
            self._background.add(task)
            task.add_done_callback(self._background.discard)

        if overdue:
            logger.info("catch_up_missed", sources=overdue, count=len(overdue))
        return overdue

    def status(self) -> list[dict[str, object]]:
        """
        调度状态

        供监控看板展示：运行状态、采集周期、上次执行、下次执行。
        「上次执行」优先取内存记录（本次进程内），
        没有则回落到 collection_logs 表——服务重启后内存清空，
        但历史执行记录仍在库里，这样看板不会出现空白。
        """
        out: list[dict[str, object]] = []
        for job in self._scheduler.get_jobs():
            nxt = getattr(job, "next_run_time", None)
            code = (job.id or "").replace("collect_", "")
            last = self._last_exec.get(code)
            last_at = last["at"].isoformat() if last else None
            out.append(
                {
                    "job_id": job.id,
                    "name": job.name,
                    "source_code": code,
                    # 暂停时 APScheduler 的 next_run_time 为 None，
                    # 显式回传 paused 标记，前端才能区分「暂停中」与「排期异常」
                    "paused": self._paused,
                    "next_run": nxt.isoformat() if nxt else None,
                    "last_run": last_at,
                    "last_ok": bool(last["ok"]) if last else None,
                    "last_new": int(last["new"]) if last else None,
                    "interval_seconds": int(
                        job.trigger.interval.total_seconds()
                        if hasattr(job.trigger, "interval")
                        else 0
                    ),
                    # 默认周期与是否自定义：前端要能区分「系统默认值」
                    # 与「运维改过的值」，并提供「恢复默认」入口。
                    # 调度器未启动时 job.trigger 读不到，回落到解析值。
                    "default_interval": int(
                        get_collector(code).default_interval
                        if get_collector(code) is not None
                        else 0
                    ),
                    "is_custom": code in get_interval_overrides(),
                    # 可选档位与允许区间：随 status 一起返回，
                    # 前端不必为拿几个静态选项单独发一次请求
                    # （status 本身是被轮询的）。
                    "options": self.interval_options(code),
                    "min_seconds": int(
                        getattr(c, "min_interval", 3600)
                        if (c := get_collector(code)) is not None
                        else 3600
                    ),
                    "max_seconds": int(
                        getattr(c, "max_interval", 86400)
                        if (c := get_collector(code)) is not None
                        else 86400
                    ),
                    # 区间依据，前端在越界提示里展示：
                    # 用户需要知道「为什么这个源不能调到 10 分钟」，
                    # 而不只是看到选项不存在。
                    "interval_basis": (
                        getattr(c, "interval_basis", "")
                        if (c := get_collector(code)) is not None
                        else ""
                    ),
                }
            )
        return out

    async def status_with_history(self) -> list[dict[str, object]]:
        """
        带历史回填的调度状态

        内存里没有执行记录时（如服务刚重启），从 collection_logs
        查最近一次成功执行补上，避免看板显示「从未执行」。
        """
        rows = self.status()
        missing = [r["source_code"] for r in rows if r.get("last_run") is None]
        if not missing:
            return rows

        try:
            from sqlalchemy import select

            from ..知识库.database import get_session
            from ..知识库.models import CollectionLog

            async with get_session() as session:
                for code in missing:
                    stmt = (
                        select(CollectionLog)
                        .where(CollectionLog.source_code == code)
                        .where(CollectionLog.status == "success")
                        .order_by(CollectionLog.finished_at.desc().nullslast())
                        .limit(1)
                    )
                    log = (await session.execute(stmt)).scalar_one_or_none()
                    if log and log.finished_at:
                        for r in rows:
                            if r["source_code"] == code:
                                r["last_run"] = log.finished_at.isoformat()
                                r["last_ok"] = True
                                r["last_new"] = log.new_count
        except Exception as exc:  # noqa: BLE001
            # 历史回填是锦上添花，失败不应影响调度状态展示
            logger.warning("scheduler_history_backfill_failed", error=str(exc)[:150])

        return rows


# 全局单例
_scheduler: CollectionScheduler | None = None


def get_scheduler() -> CollectionScheduler:
    """获取调度器单例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = CollectionScheduler()
    return _scheduler


# ========== 统计查询 ==========

# 延迟看板的数据源顺序（与 registry 的 9 个采集器一一对应；
# atlas 技术表无时间戳字段，指标不适用，仍占行以示完整）
_LATENCY_SOURCES = [
    "nvd", "ghsa", "cisa_kev", "osv", "poc_repo",
    "rss", "arxiv", "policy", "atlas",
]
# 总体（样本级合并）只含 6 个漏洞源：arxiv/policy 的 published_at
# 语义不同（论文提交时刻 / 政策正式发布日期），并入会稀释漏洞监测时效
_OVERALL_VULN_SOURCES = {"nvd", "ghsa", "cisa_kev", "osv", "poc_repo", "rss"}


async def collect_overview() -> dict:
    """
    采集概览统计

    支撑监控看板的赛题硬性指标面板，也是赛题性能指标的数据来源。
    """
    from sqlalchemy import func, select

    from ..知识库.database import get_session
    from ..知识库.models import (
        CollectionLog,
        CollectorCursor,
        Paper,
        Source,
        Technique,
        Vulnerability,
    )

    async with get_session() as session:
        # 总量统计
        total_vuln = await session.scalar(select(func.count()).select_from(Vulnerability))
        ai_vuln = await session.scalar(
            select(func.count())
            .select_from(Vulnerability)
            .where(Vulnerability.ai_relevance_score >= 0.5)
        )
        total_paper = await session.scalar(select(func.count()).select_from(Paper))
        total_tech = await session.scalar(select(func.count()).select_from(Technique))

        # 按来源统计（漏洞/资讯类）
        source_rows = await session.execute(
            select(Vulnerability.origin_source, func.count()).group_by(
                Vulnerability.origin_source
            )
        )
        # RSS 与政策法规采集器的 origin_source 是具体 feed（xianzhi/nist 等），
        # 汇总回逻辑数据源，保证看板统计口径正确
        from .community_collector import RSS_FEEDS
        from .policy_collector import POLICY_SOURCES

        rss_feed_codes = {code for code, _, _, _ in RSS_FEEDS}
        policy_feed_codes = {s["code"] for s in POLICY_SOURCES}
        by_source: dict[str, int] = {}
        for code, cnt in source_rows:
            if code in rss_feed_codes:
                key = "rss"
            elif code in policy_feed_codes:
                key = "policy"
            else:
                key = code
            by_source[key] = by_source.get(key, 0) + cnt

        # 论文与技术分属独立数据源，需单独并入，避免看板显示为 0
        paper_count = await session.scalar(select(func.count()).select_from(Paper))
        if paper_count:
            by_source["arxiv"] = paper_count

        tech_count = await session.scalar(
            select(func.count()).select_from(Technique).where(Technique.is_case_study.is_(False))
        )
        if tech_count:
            by_source["atlas"] = tech_count

        # 政策法规同理——政策数据在独立的 policies 表，
        # 不并入的话看板会显示 0（而/api/policies 明明有数据，对不上）
        from ..知识库.models import Policy

        policy_count = await session.scalar(select(func.count()).select_from(Policy))
        if policy_count:
            by_source["policy"] = policy_count

        # 数据源健康
        src_rows = await session.execute(
            select(Source.code, Source.name, Source.category, Source.is_active)
        )
        sources = [
            {"code": c, "name": n, "category": cat, "active": act}
            for c, n, cat, act in src_rows
        ]

        # 游标状态
        cursor_rows = await session.execute(
            select(CollectorCursor.source_code, CollectorCursor.last_run_at)
        )
        last_runs = {c: (t.isoformat() if t else None) for c, t in cursor_rows}

        # 最近采集日志
        log_rows = await session.execute(
            select(
                CollectionLog.source_code,
                CollectionLog.status,
                CollectionLog.new_count,
                CollectionLog.latency_ms,
                CollectionLog.finished_at,
            )
            .order_by(CollectionLog.id.desc())
            .limit(20)
        )
        recent_logs = [
            {
                "source": c,
                "status": s,
                "new_count": n,
                "elapsed_ms": e,
                "finished_at": (t.isoformat() if t else None),
            }
            for c, s, n, e, t in log_rows
        ]

        # ---------- 时效性：分源采集延迟（赛题性能指标：监测时效性） ----------
        # 口径 2026-10-14 定稿（经 9 数据源特征全量核查 + 三轮试算，
        # 详见 latency_stats 模块文档）：
        # ① 发布窗口 7 天、全表统一——排除冷启动历史回填污染；
        # ② 回合有效性：距同源上次成功 ≤ max(2×周期, 2h)，首回合无效，
        #    停摆/补采整回合剔除（机器休眠是稀疏源样本为 0 的主因）；
        # ③ ghsa 双钥匙按同源 URL 首次入库去重（目录型 URL 跳过）；
        # ④ 总体 = 6 漏洞源样本级合并；arxiv/policy 语义不同，单列。
        from .latency_stats import (
            DEFAULT_WINDOW_DAYS,
            LatencyRecord,
            LogEvent,
            build_rounds,
            collect_valid_samples,
            dedup_by_source_url,
            summarize_overall,
            summary_from_samples,
        )

        now = dt.datetime.now(dt.UTC)

        # 回合构建用日志：最近 30 天全量。记录窗口（7 天）< 日志窗口
        # （30 天），首回合不会被误判为有效；展示用 recent_logs 仍取 20 条。
        round_cutoff = now - dt.timedelta(days=30)
        round_rows = await session.execute(
            select(
                CollectionLog.source_code,
                CollectionLog.started_at,
                CollectionLog.finished_at,
                CollectionLog.status,
            ).where(CollectionLog.started_at >= round_cutoff)
        )
        events_by_source: dict[str, list[LogEvent]] = {}
        for code, started, finished, status in round_rows:
            events_by_source.setdefault(code, []).append(
                LogEvent(
                    source=code,
                    started_at=started,
                    finished_at=finished,
                    status=status,
                )
            )

        # 周期取 registry 实际生效值（运维可调），与调度器同源
        intervals = {code: _registry_interval(code) for code in _LATENCY_SOURCES}
        rounds_by_source = {
            code: build_rounds(events, intervals[code])
            for code, events in events_by_source.items()
        }

        # 漏洞情报（RSS 资讯的 origin_source 是具体 feed，归一为 rss/policy）
        vuln_rows = await session.execute(
            select(
                Vulnerability.origin_source,
                Vulnerability.source_url,
                Vulnerability.ingest_at,
                Vulnerability.published_at,
                Vulnerability.collect_latency_seconds,
            ).where(Vulnerability.collect_latency_seconds.isnot(None))
        )
        records_by_source: dict[str, list[LatencyRecord]] = {}
        for code, url, ingest, published, lat in vuln_rows:
            if code in rss_feed_codes:
                logical = "rss"
            elif code in policy_feed_codes:
                logical = "policy"
            else:
                logical = code
            records_by_source.setdefault(logical, []).append(
                LatencyRecord(
                    source=logical,
                    latency_seconds=lat,
                    published_at=published,
                    ingest_at=ingest,
                    source_url=url,
                )
            )

        # 论文/政策分属独立表且无延迟列，按 ingest - published 现算
        # （asyncpg 对 timestamptz 一律返回 aware UTC，可直接相减）
        for model, code in ((Paper, "arxiv"), (Policy, "policy")):
            table_rows = await session.execute(
                select(model.ingest_at, model.published_at).where(
                    model.published_at.isnot(None)
                )
            )
            for ingest, published in table_rows:
                if ingest is None or published is None:
                    continue
                records_by_source.setdefault(code, []).append(
                    LatencyRecord(
                        source=code,
                        latency_seconds=(ingest - published).total_seconds(),
                        published_at=published,
                        ingest_at=ingest,
                    )
                )

        # 分源统计 + 总体合并（总体只并入 6 个漏洞源）
        latency_by_source: list[dict] = []
        overall_samples: list[int] = []
        overall_valid_rounds = 0
        overall_total_rounds = 0
        for code in _LATENCY_SOURCES:
            records = dedup_by_source_url(records_by_source.get(code, []))
            rounds = rounds_by_source.get(code, [])
            samples, counts = collect_valid_samples(
                records, rounds, now=now, window_days=DEFAULT_WINDOW_DAYS
            )
            valid_rounds = sum(1 for w in rounds if w.valid)
            summary = summary_from_samples(
                samples,
                valid_rounds=valid_rounds,
                total_rounds=len(rounds),
                interval_seconds=intervals[code],
                window_days=DEFAULT_WINDOW_DAYS,
                **counts,
            )
            summary["source"] = code
            if code == "atlas":
                # ATLAS 技术表无任何时间戳字段，延迟指标不适用
                summary["not_applicable"] = True
            latency_by_source.append(summary)
            if code in _OVERALL_VULN_SOURCES:
                overall_samples.extend(samples)
                overall_valid_rounds += valid_rounds
                overall_total_rounds += len(rounds)

        latency_overall = summarize_overall(
            overall_samples,
            valid_rounds=overall_valid_rounds,
            total_rounds=overall_total_rounds,
            window_days=DEFAULT_WINDOW_DAYS,
            contributors=sorted(_OVERALL_VULN_SOURCES),
        )

    return {
        "totals": {
            "vulnerabilities": total_vuln or 0,
            "ai_security": ai_vuln or 0,
            "papers": total_paper or 0,
            "techniques": total_tech or 0,
            "sources": len(sources),
        },
        "by_source": by_source,
        "sources": sources,
        "last_runs": last_runs,
        "latency_by_source": latency_by_source,
        "latency_overall": latency_overall,
        # 兼容旧前端字段：与 latency_overall 同值（新口径）
        "latency_stats": latency_overall,
        "recent_logs": recent_logs,
        "generated_at": dt.datetime.now().isoformat(),
    }
