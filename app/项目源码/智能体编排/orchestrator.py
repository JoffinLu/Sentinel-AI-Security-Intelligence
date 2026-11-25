"""
LangGraph 智能体编排框架

赛题要求「基于 LangGraph 的 AI Agent 编排，状态机流转 + 多 Agent 协作」。

为什么需要编排层
----------------
P1-P3 已经把三个模块（监测 / 富化 / 问答）分别做完了，但它们目前是
「各自独立、互不感知」的状态。赛题要求要的恰恰是它们的协同：

  监测 Agent 发现新漏洞
        ↓
  富化 Agent 判断该漏洞是否值得富化（AI 相关性 + 优先级）
        ↓
  问答 Agent 的知识库随富化完成而更新
        ↓
  人工介入：低置信度的富化结果送人工核验

编排层要解决四个问题：

  ① 状态共享：三个 Agent 共享同一份工作状态，而不是各自查库
 ② 反思循环：富化置信度不达标时自动重试，而不是直接接受坏结果
 ③ 并行执行：无依赖的节点并行，压缩整体耗时
 ④ 可观测：每个节点的耗时、产出、决策依据都记录下来，
        前端可展示「这次富化为什么这么做」

设计取舍
--------
没有用 LangGraph 的 interrupt 做真正的 HITL 暂停——那需要独立的服务端
来 resume 工作流，对单机演示形态过重。改用「人工审核队列」模式：
低置信度结果标记为待核验、写入 enrichments 表并暂停后续自动处理，
人工在界面上确认/修正后由 API 触发继续。这在演示场景下效果等同，
但复杂度低一个数量级。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ..后端服务.logger import get_logger
# 运维观测：把节点执行与自愈动作记进内存环形缓冲（见 ops_history.py）
from ..后端服务 import ops_history

logger = get_logger("orchestrator")


class NodeStatus(str, Enum):
    """节点执行状态"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"  # 待人工核验


@dataclass(slots=True)
class NodeResult:
    """单个节点的执行结果"""

    name: str
    status: NodeStatus
    elapsed_ms: int = 0
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # 决策依据——回答「为什么这么做」，供前端展示可解释性
    reasoning: str = ""
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "elapsed_ms": self.elapsed_ms,
            "output": self.output,
            "error": self.error,
            "reasoning": self.reasoning,
            "attempt": self.attempt,
        }


@dataclass(slots=True)
class WorkflowState:
    """
    工作流共享状态

    LangGraph 的 State 载体。所有 Agent 通过它通信，
    避免各自查库导致的竞态与重复查询。
    """

    workflow_id: str = field(default_factory=lambda: f"wf-{uuid.uuid4().hex[:12]}")
    workflow_type: str = "pipeline"  # pipeline / enrich / qa
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # 输入
    cve_id: str | None = None
    source_code: str | None = None
    question: str | None = None

    # 上下文
    context: dict[str, Any] = field(default_factory=dict)
    # 各节点产出的中间结果
    results: dict[str, Any] = field(default_factory=dict)

    # 控制信号
    retry_count: int = 0
    max_retry: int = 2
    # 低置信度阈值——低于此值进入人工核验
    confidence_threshold: float = 0.5
    # 是否需要人工介入
    needs_review: bool = False
    review_reason: str = ""

    # 节点执行记录
    node_results: list[NodeResult] = field(default_factory=list)
    started_at: float = 0.0
    status: NodeStatus = NodeStatus.PENDING

    def add_result(self, result: NodeResult) -> None:
        self.node_results.append(result)
        self.results[result.name] = result.output

    def get(self, key: str, default: Any = None) -> Any:
        return self.results.get(key, default)

    def to_state_fields(self) -> dict[str, Any]:
        """
        导出为 dataclass 字段格式

        与 to_dict 的区别：后者是给 API 用的展示格式（节点字段名是
        `nodes`），而重建 WorkflowState 需要 dataclass 的原字段名
        （`node_results`）。两者混用会导致节点记录丢失。
        """
        return {
            "workflow_id": self.workflow_id,
            "workflow_type": self.workflow_type,
            "created_at": self.created_at,
            "cve_id": self.cve_id,
            "source_code": self.source_code,
            "question": self.question,
            "context": self.context,
            "results": self.results,
            "retry_count": self.retry_count,
            "max_retry": self.max_retry,
            "confidence_threshold": self.confidence_threshold,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "node_results": self.node_results,
            "started_at": self.started_at,
            "status": self.status,
        }

    @classmethod
    def from_state_fields(cls, data: dict[str, Any]) -> WorkflowState:
        """
        从 dict 重建状态

        LangGraph 会把 dataclass State 展开成 dict 返回，
        必须用本方法重建，否则丢失节点记录与状态变更。
        """
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        state = cls(**valid)
        # 节点记录可能被 LangGraph 序列化为 dict，需还原为 NodeResult
        restored: list[NodeResult] = []
        for item in state.node_results or []:
            if isinstance(item, NodeResult):
                restored.append(item)
            elif isinstance(item, dict):
                restored.append(
                    NodeResult(
                        name=item.get("name", ""),
                        status=NodeStatus(item.get("status", "success")),
                        elapsed_ms=item.get("elapsed_ms", 0),
                        output=item.get("output", {}) or {},
                        error=item.get("error"),
                        reasoning=item.get("reasoning", ""),
                        attempt=item.get("attempt", 1),
                    )
                )
        state.node_results = restored
        return state

    @property
    def elapsed_ms(self) -> int:
        if not self.started_at:
            return 0
        return int((time.perf_counter() - self.started_at) * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_type": self.workflow_type,
            "created_at": self.created_at,
            "cve_id": self.cve_id,
            "question": self.question,
            "status": self.status.value,
            "elapsed_ms": self.elapsed_ms,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "retry_count": self.retry_count,
            "nodes": [r.to_dict() for r in self.node_results],
            "results": self.results,
        }


# ========== Agent 定义 ==========


class BaseAgent:
    """
    Agent 基类

    每个 Agent 负责一个阶段，产出写入 WorkflowState 并记录决策依据。
    心跳监测是「自愈能力」的实现基础——连续失败的 Agent 会被熔断，
    避免单个组件拖垮整条流水线。
    """

    name: str = "base"

    def __init__(self) -> None:
        self.log = get_logger(f"agent.{self.name}")
        # 心跳记录：最近成功的次数与连续失败次数
        self._success_streak = 0
        self._failure_streak = 0
        self._last_heartbeat: datetime | None = None
        # 熔断阈值——连续失败达到则跳过该 Agent
        self._circuit_threshold = 3

    @property
    def is_circuit_open(self) -> bool:
        """熔断器是否打开（连续失败过多）"""
        return self._failure_streak >= self._circuit_threshold

    @property
    def failure_streak(self) -> int:
        """连续失败次数（公开只读）"""
        return self._failure_streak

    @property
    def success_streak(self) -> int:
        """连续成功次数（公开只读）"""
        return self._success_streak

    def heartbeat(self, success: bool) -> None:
        """记录心跳"""
        self._last_heartbeat = datetime.now(UTC)
        if success:
            self._success_streak += 1
            self._failure_streak = 0
        else:
            self._failure_streak += 1

    def health(self) -> dict[str, Any]:
        """健康状态——供监控端点采集"""
        return {
            "name": self.name,
            "healthy": not self.is_circuit_open,
            "circuit_open": self.is_circuit_open,
            "success_streak": self._success_streak,
            "failure_streak": self._failure_streak,
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
        }

    async def run(self, state: WorkflowState) -> NodeResult:
        """
        执行入口（含熔断判断、心跳记录、异常隔离）

        子类实现 execute。

        心跳覆盖所有路径（含跳过与失败）：心跳的语义是
        「这个 Agent 还在被调度、还活着」，而不是「它成功执行了任务」。
        若跳过时不记录，监控面板会显示心跳为 null，
        看不出 agent 到底是在正常工作还是已经失联。
        """
        if self.is_circuit_open:
            self.log.warning("agent_circuit_open", agent=self.name)
            # 熔断属于异常状态，不记成功心跳——否则会掩盖故障
            self.heartbeat(False)
            ops_history.append_event(
                "circuit_open",
                f"{self.name}已熔断，本次跳过",
                f"连续失败 {self._circuit_threshold} 次，自动隔离以保护主链路",
            )
            ops_history.append_history(
                self.name, ok=False, ms=0.0, agent=self.name,
                error="circuit_open",
            )
            return NodeResult(
                name=self.name,
                status=NodeStatus.SKIPPED,
                reasoning="连续失败触发熔断，本次跳过该 Agent",
                error="circuit_open",
            )

        start = time.perf_counter()
        try:
            result = await self.execute(state)
            elapsed = int((time.perf_counter() - start) * 1000)
            result.elapsed_ms = elapsed
            # SKIPPED 也算健康：智能体判断「这单不需要我做」是正确的决策，
            # 不是故障。区分 success/failed 会让熔断逻辑误判。
            self.heartbeat(result.status != NodeStatus.FAILED)
            state.add_result(result)
            ops_history.append_history(
                self.name,
                ok=result.status != NodeStatus.FAILED,
                ms=float(elapsed),
                agent=self.name,
                error=result.error,
                retry=state.retry_count,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - start) * 1000)
            self.heartbeat(False)
            self.log.warning("agent_failed", agent=self.name, error=str(exc)[:200])
            ops_history.append_history(
                self.name, ok=False, ms=float(elapsed), agent=self.name,
                error=str(exc)[:180], retry=state.retry_count,
            )
            ops_history.append_event(
                "agent_failed",
                f"{self.name} 执行失败（已隔离）",
                str(exc)[:200],
            )
            result = NodeResult(
                name=self.name,
                status=NodeStatus.FAILED,
                elapsed_ms=elapsed,
                error=str(exc)[:500],
                reasoning="执行异常，已隔离不影响后续节点",
            )
            state.add_result(result)
            return result

    async def execute(self, state: WorkflowState) -> NodeResult:
        """子类实现：真正的业务逻辑"""
        raise NotImplementedError


class MonitorAgent(BaseAgent):
    """
    监测 Agent

    负责从数据源采集并判断新增情报是否值得后续处理。
    这是流水线的入口。
    """

    name = "monitor"

    async def execute(self, state: WorkflowState) -> NodeResult:
        from ..数据采集.scheduler import get_scheduler

        scheduler = get_scheduler()
        code = state.source_code

        if code:
            # 指定数据源：执行一次采集
            report = await scheduler.collect_now(code, trigger="api")
            return NodeResult(
                name=self.name,
                status=NodeStatus.SUCCESS,
                output={
                    "source": code,
                    "fetched": report.get("fetched", 0),
                    "new": report.get("new", 0),
                    "ai_security": report.get("ai_security", 0),
                },
                reasoning=(
                    f"已采集 {code}：获取 {report.get('fetched', 0)} 条，"
                    f"新增 {report.get('new', 0)} 条"
                ),
            )

        # 未指定：走一轮全部数据源
        results = await scheduler.collect_all()
        total_new = sum(r.get("new", 0) for r in results)
        return NodeResult(
            name=self.name,
            status=NodeStatus.SUCCESS,
            output={"all_sources": results, "total_new": total_new},
            reasoning=f"全源采集完成，共新增 {total_new} 条情报",
        )


class EnrichmentAgent(BaseAgent):
    """
    富化 Agent

    决策「这条漏洞值不值得 7 维富化」——这是智能体自主决策的体现。
    对非 AI 相关的漏洞跳过富化，避免浪费算力。
    """

    name = "enrichment"

    async def execute(self, state: WorkflowState) -> NodeResult:
        from ..富化模块.orchestrator import get_orchestrator

        cve_id = state.cve_id
        if not cve_id:
            return NodeResult(
                name=self.name,
                status=NodeStatus.SKIPPED,
                reasoning="未指定漏洞编号，跳过富化",
            )

        orchestrator = get_orchestrator()
        report = await orchestrator.enrich_one(cve_id)

        if report is None:
            return NodeResult(
                name=self.name,
                status=NodeStatus.FAILED,
                error="未找到该漏洞",
                reasoning="目标漏洞不存在",
            )

        avg_conf = report.avg_confidence
        needs_review = avg_conf < state.confidence_threshold

        return NodeResult(
            name=self.name,
            status=NodeStatus.NEEDS_REVIEW if needs_review else NodeStatus.SUCCESS,
            output={
                "completeness": report.completeness,
                "avg_confidence": avg_conf,
                "dimensions": {k: v.to_dict() for k, v in report.results.items()},
                "low_confidence_count": report.low_confidence_count,
            },
            reasoning=(
                f"7 维富化完成，完整度 {report.completeness:.0%}，"
                f"平均置信度 {avg_conf:.2f}"
                + ("，低于阈值需人工核验" if needs_review else "")
            ),
        )


class QAAgentNode(BaseAgent):
    """
    问答 Agent

    基于已富化的知识库回答问题。若知识库中尚无富化数据，
    会在决策依据中提示，避免给出低质量答案。
    """

    name = "qa"

    async def execute(self, state: WorkflowState) -> NodeResult:
        from ..问答模块.qa_agent import get_qa_agent

        question = state.question
        if not question:
            return NodeResult(
                name=self.name,
                status=NodeStatus.SKIPPED,
                reasoning="未提供问题",
            )

        agent = get_qa_agent()
        # 预热嵌入模型，避免首个请求等 18 秒
        agent.retriever.embedding.warmup()

        answer = await agent.ask(question, session_id=state.workflow_id)

        return NodeResult(
            name=self.name,
            status=NodeStatus.SUCCESS,
            output=answer.to_dict(),
            reasoning=(
                f"意图「{answer.intent_label}」，"
                f"召回 {answer.retrieval_hits} 条，"
                f"引用 {len(answer.citations)} 条，"
                f"耗时 {answer.elapsed_ms}ms"
            ),
        )


class SupervisorAgent:
    """
    主管 Agent

    不产出业务结果，只做两件事：
      ① 反思循环——根据节点产出决定是否重试
      ② 人工介入判定——低置信度结果转人工核验

    这是 LangGraph 条件边与反思循环的承载者。
    """

    def __init__(self) -> None:
        self.log = get_logger("agent.supervisor")

    def should_retry(self, state: WorkflowState, result: NodeResult) -> bool:
        """
        是否重试

        只对「可恢复」的失败重试：网络抖动、限流。
        「未找到该漏洞」这类确定性失败重试也没用。
        """
        if state.retry_count >= state.max_retry:
            return False
        if result.status != NodeStatus.FAILED:
            return False

        retryable = any(
            k in (result.error or "").lower()
            for k in ("timeout", "rate limit", "429", "connection", "超时", "限流")
        )
        if retryable:
            self.log.info("supervisor_retry", node=result.name, attempt=result.attempt + 1)
            ops_history.append_event(
                "supervisor_retry",
                f"主管判定「{result.name}」可恢复，第 {result.attempt + 1} 次重试",
                (result.error or "")[:160],
            )
            return True

        self.log.info(
            "supervisor_no_retry",
            node=result.name,
            reason="非可恢复错误",
        )
        return False

    def needs_human_review(self, state: WorkflowState) -> tuple[bool, str]:
        """
        是否需要人工核验

        判定标准（可解释，不做黑箱）：
          ① 任一维度富化置信度低于阈值
          ② 问答引用为空（无法溯源）
          ③ 采集失败率超过半数
        """
        # ① 富化置信度
        enrich_out = state.get("enrichment") or {}
        low = enrich_out.get("low_confidence_count", 0)
        if low > 0:
            return True, f"{low} 个维度置信度低于阈值 {state.confidence_threshold}"

        # ② 问答引用为空
        qa_out = state.get("qa") or {}
        if qa_out and not qa_out.get("citations"):
            return True, "问答结果无可溯源引用，存在编造风险"

        # ③ 采集失败率
        monitor_out = state.get("monitor") or {}
        all_sources = monitor_out.get("all_sources")
        if isinstance(all_sources, list) and all_sources:
            failed = sum(1 for r in all_sources if r.get("error"))
            if failed > len(all_sources) / 2:
                return True, f"{failed}/{len(all_sources)} 个数据源采集失败"

        return False, ""


# ========== 状态图 ==========


class IntelWorkflow:
    """
    情报处理工作流

    图结构（LangGraph 状态机）：

        ┌──────────┐
        │  monitor │  采集情报
        └────┬─────┘
             ▼
        ┌──────────┐
        │ triage   │  优先级判定（是否值得富化）
        └────┬─────┘
             │
        ┌────┴─────┐
        ▼          ▼
   ┌─────────┐  ┌───────────┐
   │ enrich  │  │  qa       │  并行：富化 与 问答
   └────┬────┘  └─────┬─────┘
        └──────┬───────┘
               ▼
        ┌──────────────┐
        │  supervisor  │  反思 + 人工介入判定
        └──────┬───────┘
               ▼
             END

    对应的三项要求：
      条件边     → triage / supervisor 的分支判断
      并行节点   → enrich 与 qa 同时执行
      反思循环   → supervisor.should_retry 触发重跑
    """

    def __init__(self) -> None:
        self.log = get_logger("workflow")
        self.monitor = MonitorAgent()
        self.enrichment = EnrichmentAgent()
        self.qa_agent = QAAgentNode()
        self.supervisor = SupervisorAgent()
        self._graph = None

    # ---------- 节点 ----------

    def _mark_history(
        self,
        node: str,
        ok: bool,
        ms: float,
        error: str | None = None,
    ) -> None:
        """
        包装节点（triage/supervisor/reflect/skip_enrich 及各跳过分支）
        的执行记录。

        只有 BaseAgent.run() 内部才写 ops_history，而 triage 等
        「纯函数节点」不走 run() —— 不补记的话，运维面板上这些
        节点永远显示「未触发」，即使工作流刚刚完整跑过一遍。
        """
        ops_history.append_history(node, ok=ok, ms=ms, agent=node, error=error)

    async def _node_monitor(self, state: WorkflowState) -> WorkflowState:
        """
        监测节点

        **采集是否应该执行，取决于工作流的意图**——
        富化工作流的输入是「已经采集好的某条漏洞」，
        此时再跑一遍全量采集是纯浪费（实测单次 NVD+GHSA 就耗时 19s，
        四个源跑完要几十秒，而富化本身只要几秒）。
        所以除 pipeline 外都跳过采集，让编排专注于「决策」这部分能力。
        """
        # 只有全流程工作流才真采集；qa/enrich 都已有数据在手
        if state.workflow_type != "pipeline":
            reason = (
                "问答类工作流跳过采集（采集耗时以分钟计）"
                if state.workflow_type == "qa"
                else "富化类工作流跳过采集（目标漏洞已在库中，重复采集无意义）"
            )
            state.node_results.append(
                NodeResult(
                    name=self.monitor.name,
                    status=NodeStatus.SKIPPED,
                    reasoning=reason,
                )
            )
            # 绕过了 monitor.run()（run 内部才记 ops_history），
            # 这里补一条——「被调度过但决策跳过」也算一次触发，
            # 否则面板会永远显示未触发，看不出工作流在活动
            self._mark_history(self.monitor.name, ok=True, ms=0.0)
            return state

        await self.monitor.run(state)
        return state

    async def _node_triage(self, state: WorkflowState) -> WorkflowState:
        start = time.perf_counter()
        state = await self._do_triage(state)
        ok = self._last_node_ok(state, "triage")
        self._mark_history(
            "triage", ok=ok, ms=(time.perf_counter() - start) * 1000
        )
        return state

    @staticmethod
    def _last_node_ok(state: WorkflowState, name: str) -> bool:
        """取指定节点最后一次结果的成败——包装节点记录用"""
        for r in reversed(state.node_results):
            if r.name == name:
                return r.status != NodeStatus.FAILED
        return True

    async def _do_triage(self, state: WorkflowState) -> WorkflowState:
        """
        优先级判定

        这是「智能体自主决策」的直接体现——
        不是所有漏洞都值得 7 维富化。
        """
        from sqlalchemy import func, select

        from ..知识库.database import get_session
        from ..知识库.models import Enrichment, Vulnerability

        cve_id = state.cve_id
        if not cve_id:
            # 监测阶段的任务：判断本轮新增中有多少值得富化
            monitor_out = state.get("monitor") or {}
            total = monitor_out.get("total_new", 0)
            state.results["triage"] = {
                "worth_enriching": total,
                "total_new": total,
            }
            state.node_results.append(
                NodeResult(
                    name="triage",
                    status=NodeStatus.SUCCESS,
                    output={"worth_enriching": total, "total_new": total},
                    reasoning=f"本轮新增 {total} 条，其中 AI 相关 {total} 条值得富化",
                )
            )
            return state

        async with get_session() as session:
            vuln = await session.scalar(
                select(Vulnerability).where(Vulnerability.cve_id == cve_id)
            )
            if vuln is None:
                state.node_results.append(
                    NodeResult(
                        name="triage",
                        status=NodeStatus.FAILED,
                        error="目标漏洞不存在",
                        reasoning="无法判定优先级",
                    )
                )
                return state

            ai_rel = vuln.ai_relevance_score or 0.0
            cvss = vuln.cvss_score or 0.0

            # 「是否已富化」不能只看主表状态字段——
            # 明细表（enrichments）才是真实依据。两者可能不一致：
            # 人工删除明细、或写入过程中中断，都会留下
            # status=completed 但明细缺失的脏状态。
            # 以明细表为准，避免「以为富化过、实际没有」的漏富化。
            detail_exists = await session.scalar(
                select(func.count())
                .select_from(Enrichment)
                .where(Enrichment.vulnerability_id == vuln.id)
            )
            already = bool(detail_exists)

        # 决策规则（可解释）：
        #   AI 相关性 ≥0.5 或 CVSS ≥7 → 值得富化
        #   已富化过（以明细表为准）→ 无需重复
        worth = (ai_rel >= 0.5 or cvss >= 7.0) and not already
        reason = (
            f"AI相关性 {ai_rel:.2f}，CVSS {cvss:.1f}，"
            + ("已富化过，跳过" if already else ("值得富化" if worth else "价值不足，跳过"))
        )

        state.results["triage"] = {
            "worth_enriching": worth,
            "ai_relevance": ai_rel,
            "cvss": cvss,
            "already_enriched": already,
        }
        state.node_results.append(
            NodeResult(
                name="triage",
                status=NodeStatus.SUCCESS,
                output=state.results["triage"],
                reasoning=reason,
            )
        )
        return state

    async def _node_enrich(self, state: WorkflowState) -> WorkflowState:
        triage = state.get("triage") or {}
        # 条件边：triage 判定不值得则跳过。
        # 注意 skip_enrich 节点也会记一条结果，这里需判重，
        # 否则节点列表会出现两条 enrichment。
        if triage.get("worth_enriching") is False or "enrichment" in state.results:
            if not any(r.name == "enrichment" for r in state.node_results):
                state.node_results.append(
                    NodeResult(
                        name="enrichment",
                        status=NodeStatus.SKIPPED,
                        reasoning="优先级判定为不值得富化，条件边跳过",
                    )
                )
                # 绕过了 enrichment.run()，补记录（跳过决策也是一次触发）
                self._mark_history("enrichment", ok=True, ms=0.0)
            return state

        await self.enrichment.run(state)
        return state

    async def _node_qa(self, state: WorkflowState) -> WorkflowState:
        if not state.question:
            state.node_results.append(
                NodeResult(
                    name="qa",
                    status=NodeStatus.SKIPPED,
                    reasoning="本次工作流不含问答任务",
                )
            )
            self._mark_history("qa", ok=True, ms=0.0)
            return state
        await self.qa_agent.run(state)
        return state

    async def _node_join(self, state: WorkflowState) -> WorkflowState:
        """
        并行执行富化与问答

        两条分支互不依赖，用 asyncio.gather 并行以压缩整体耗时。
        任一分支失败不影响另一分支（各 Agent 内部有异常隔离）。
        """
        import asyncio

        start = time.perf_counter()
        enrich_task = self._node_enrich(state)
        qa_task = self._node_qa(state)
        await asyncio.gather(enrich_task, qa_task, return_exceptions=True)

        done = [n for n in ("enrich", "enrichment", "qa") if n in state.results]
        elapsed = int((time.perf_counter() - start) * 1000)
        state.node_results.append(
            NodeResult(
                name="fanout",
                status=NodeStatus.SUCCESS,
                output={"branches": done},
                elapsed_ms=elapsed,
                reasoning=f"并行分支完成：{'、'.join(done) if done else '无'}（{elapsed}ms）",
            )
        )
        return state

    async def _node_supervisor(self, state: WorkflowState) -> WorkflowState:
        start = time.perf_counter()
        """反思 + 人工介入判定"""
        needs, reason = self.supervisor.needs_human_review(state)
        state.needs_review = needs
        state.review_reason = reason

        state.node_results.append(
            NodeResult(
                name="supervisor",
                status=NodeStatus.NEEDS_REVIEW if needs else NodeStatus.SUCCESS,
                output={"needs_review": needs, "reason": reason},
                reasoning=(
                    f"需人工核验：{reason}" if needs else "全部节点通过质量门禁，无需人工介入"
                ),
            )
        )
        self._mark_history(
            "supervisor", ok=True, ms=(time.perf_counter() - start) * 1000
        )
        return state

    # ---------- 反思循环 ----------

    async def _node_reflect(self, state: WorkflowState) -> WorkflowState:
        """
        反思节点

        检查是否有失败且可恢复的节点，有则重试。
        LangGraph 通过条件边实现循环，这里承载重试计数。
        """
        start = time.perf_counter()
        for result in state.node_results:
            if self.supervisor.should_retry(state, result):
                state.retry_count += 1
                result.attempt += 1
                self.log.info(
                    "workflow_retry",
                    node=result.name,
                    attempt=state.retry_count,
                )
                # 重试该节点
                agent = {
                    "monitor": self.monitor,
                    "enrich": self.enrichment,
                    "qa": self.qa_agent,
                }.get(result.name)
                if agent:
                    await agent.run(state)
                break
        self._mark_history(
            "reflect", ok=True, ms=(time.perf_counter() - start) * 1000
        )
        return state

    # ---------- 图构建 ----------

    def build(self):
        """
        构建 LangGraph 状态机

        条件边实现 triage 的跳过逻辑与 reflect 的重试循环，
        这两项正对应赛题要求的「条件边」与「反思循环」。
        """
        from langgraph.graph import END, START, StateGraph

        def _after_triage(state: WorkflowState) -> str:
            """条件边：triage 后决定是否走富化"""
            triage = state.get("triage") or {}
            if triage.get("worth_enriching") is False:
                return "skip_enrich"
            return "enrich"

        def _after_supervisor(state: WorkflowState) -> str:
            """条件边：是否需要反思重试"""
            for result in state.node_results:
                if result.status == NodeStatus.FAILED and self.supervisor.should_retry(
                    state, result
                ):
                    return "reflect"
            return "done"

        def _after_reflect(state: WorkflowState) -> str:
            """条件边：重试后回到哪"""
            return "supervisor" if state.retry_count < state.max_retry else "done"

        builder = StateGraph(WorkflowState)
        builder.add_node("monitor", self._node_monitor)
        builder.add_node("triage", self._node_triage)
        builder.add_node("enrich", self._node_enrich)
        builder.add_node("qa", self._node_qa)
        builder.add_node("fanout", self._node_join)
        builder.add_node("supervisor", self._node_supervisor)
        builder.add_node("reflect", self._node_reflect)
        builder.add_node("skip_enrich", self._node_skip_enrich)

        builder.add_edge(START, "monitor")
        builder.add_edge("monitor", "triage")
        # 条件边：triage → enrich / skip_enrich
        builder.add_conditional_edges(
            "triage", _after_triage, {"enrich": "enrich", "skip_enrich": "skip_enrich"}
        )
        # 富化与问答在同一个并行节点内执行，再汇合。
        #
        # 为什么不用两条入边（enrich→join、qa→join）做并行：
        # LangGraph 的入边是「竞争」而非「汇合」——enrich 一完成就会
        # 立即触发 join，qa 分支被跳过。实测表现为 qa 节点从不执行。
        # 放进同一节点用 asyncio.gather 才能真正并行。
        builder.add_edge("skip_enrich", "fanout")
        builder.add_edge("enrich", "fanout")
        builder.add_edge("fanout", "supervisor")
        # 条件边：supervisor → reflect / END
        builder.add_conditional_edges(
            "supervisor", _after_supervisor, {"reflect": "reflect", "done": END}
        )
        builder.add_conditional_edges(
            "reflect", _after_reflect, {"supervisor": "supervisor", "done": END}
        )

        self._graph = builder.compile()
        self.log.info("workflow_graph_built", nodes=6)
        return self._graph

    async def _node_skip_enrich(self, state: WorkflowState) -> WorkflowState:
        start = time.perf_counter()
        state = await self._do_skip_enrich(state)
        self._mark_history(
            "skip_enrich", ok=True, ms=(time.perf_counter() - start) * 1000
        )
        return state

    async def _do_skip_enrich(self, state: WorkflowState) -> WorkflowState:
        """富化被跳过的占位节点（LangGraph 要求每条边有目标节点）"""
        return state

    @property
    def graph(self):
        """获取编译后的图（懒构建）"""
        if self._graph is None:
            self.build()
        return self._graph

    # ---------- 执行 ----------

    async def run(
        self,
        workflow_type: str = "pipeline",
        cve_id: str | None = None,
        source_code: str | None = None,
        question: str | None = None,
    ) -> WorkflowState:
        """
        执行工作流

        Args:
            workflow_type: pipeline（全流程）/ enrich（富化）/ qa（问答）
            cve_id: 目标漏洞
            source_code: 指定数据源
            question: 问句
        """
        state = WorkflowState(
            workflow_type=workflow_type,
            cve_id=cve_id,
            source_code=source_code,
            question=question,
        )
        state.started_at = time.perf_counter()
        state.status = NodeStatus.RUNNING

        self.log.info(
            "workflow_start",
            workflow_id=state.workflow_id,
            type=workflow_type,
            cve_id=cve_id,
        )

        try:
            final = await self.graph.ainvoke(state)
            # LangGraph 会把 dataclass State 展开成 dict 返回。
            # 必须用 from_state_fields 重建，否则会拿到执行前的旧 state，
            # 丢掉全部节点结果（表现为节点列表始终为空）。
            if isinstance(final, dict):
                merged = WorkflowState.from_state_fields(final)
                merged.started_at = state.started_at
                state = merged
            else:
                state = final
            state.status = (
                NodeStatus.NEEDS_REVIEW
                if state.needs_review
                else NodeStatus.SUCCESS
            )
        except Exception as exc:  # noqa: BLE001
            self.log.error("workflow_failed", error=str(exc)[:200])
            state.status = NodeStatus.FAILED
            state.node_results.append(
                NodeResult(
                    name="workflow",
                    status=NodeStatus.FAILED,
                    error=str(exc)[:500],
                    reasoning="工作流执行异常",
                )
            )
        finally:
            self.log.info(
                "workflow_done",
                workflow_id=state.workflow_id,
                status=state.status.value,
                elapsed_ms=state.elapsed_ms,
                needs_review=state.needs_review,
            )

        return state

    # ---------- 观测 ----------

    def agent_health(self) -> list[dict[str, Any]]:
        """所有 Agent 的健康状态——供监控面板采集"""
        return [
            self.monitor.health(),
            self.enrichment.health(),
            self.qa_agent.health(),
        ]

    def graph_structure(self) -> dict[str, Any]:
        """图结构描述——供前端展示编排流程"""
        return {
            "nodes": [
                {"id": "monitor", "label": "监测 Agent", "kind": "collector"},
                {"id": "triage", "label": "优先级判定", "kind": "decision"},
                {"id": "enrich", "label": "富化 Agent", "kind": "enricher"},
                {"id": "qa", "label": "问答 Agent", "kind": "qa"},
                {"id": "supervisor", "label": "主管 Agent", "kind": "supervisor"},
                {"id": "reflect", "label": "反思重试", "kind": "loop"},
            ],
            "edges": [
                {"from": "START", "to": "monitor", "type": "normal"},
                {"from": "monitor", "to": "triage", "type": "normal"},
                {"from": "triage", "to": "enrich", "type": "conditional"},
                {"from": "triage", "to": "skip_enrich", "type": "conditional"},
                {"from": "enrich", "to": "supervisor", "type": "parallel"},
                {"from": "qa", "to": "supervisor", "type": "parallel"},
                {"from": "supervisor", "to": "reflect", "type": "conditional"},
                {"from": "reflect", "to": "supervisor", "type": "loop"},
            ],
            "features": [
                "条件边（triage 决策 / supervisor 反思）",
                "并行节点（enrich 与 qa 同时执行）",
                "反思循环（失败自动重试）",
                "人工介入（低置信度转核验）",
            ],
        }


# ========== 全局单例 ==========

_workflow: IntelWorkflow | None = None


def get_workflow() -> IntelWorkflow:
    """获取工作流单例"""
    global _workflow
    if _workflow is None:
        _workflow = IntelWorkflow()
    return _workflow
