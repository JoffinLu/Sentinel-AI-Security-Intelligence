"""
智能体编排单元测试

重点验证编排框架的三项核心能力：
  ① 条件边——triage 决策是否正确路由
  ② 反思循环——失败节点的重试与熔断
  ③ 人工介入——低置信度结果的识别

这些是赛题「基于 LangGraph 的 AI Agent 编排」的直接举证。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ========== 工作流状态 ==========


def test_workflow_state_serialization():
    """工作流状态需可完整序列化（供 API 返回与前端展示）"""
    from 项目源码.智能体编排.orchestrator import NodeResult, NodeStatus, WorkflowState

    state = WorkflowState(workflow_type="qa", question="测试问题")
    state.add_result(
        NodeResult(
            name="qa",
            status=NodeStatus.SUCCESS,
            output={"answer": "答案"},
            reasoning="测试",
        )
    )

    d = state.to_dict()
    assert d["workflow_type"] == "qa"
    assert d["question"] == "测试问题"
    assert len(d["nodes"]) == 1
    assert d["nodes"][0]["name"] == "qa"
    assert d["results"]["qa"]["answer"] == "答案"


def test_workflow_state_rebuild_from_dict():
    """
    LangGraph 会把 dataclass State 展开成 dict 返回

    必须用 to_state_fields / from_state_fields 重建，
    否则节点记录会丢失——因为 to_dict() 是给 API 用的展示格式，
    其中的节点字段名是 `nodes` 而非 dataclass 的 `node_results`。
    这是实测踩过的坑：节点列表始终为空。
    """
    from 项目源码.智能体编排.orchestrator import NodeResult, NodeStatus, WorkflowState

    original = WorkflowState(workflow_type="enrich", cve_id="CVE-2026-1")
    original.add_result(
        NodeResult(name="enrich", status=NodeStatus.SUCCESS, output={"ok": True})
    )

    rebuilt = WorkflowState.from_state_fields(original.to_state_fields())

    assert rebuilt.cve_id == "CVE-2026-1"
    assert len(rebuilt.node_results) == 1
    assert rebuilt.node_results[0].name == "enrich"
    assert rebuilt.node_results[0].status == NodeStatus.SUCCESS


def test_node_results_survive_dict_roundtrip():
    """节点记录经 dict 往返后仍完整（LangGraph 可能序列化为 dict）"""
    from 项目源码.智能体编排.orchestrator import (
        NodeResult,
        NodeStatus,
        WorkflowState,
    )

    state = WorkflowState(workflow_type="qa")
    state.node_results = [
        {
            "name": "qa",
            "status": "success",
            "elapsed_ms": 1234,
            "output": {"answer": "x"},
            "error": None,
            "reasoning": "测试",
            "attempt": 1,
        }
    ]

    rebuilt = WorkflowState.from_state_fields(state.to_state_fields())

    assert len(rebuilt.node_results) == 1
    node = rebuilt.node_results[0]
    assert isinstance(node, NodeResult), "dict 应被还原为 NodeResult"
    assert node.name == "qa"
    assert node.elapsed_ms == 1234
    assert node.status == NodeStatus.SUCCESS


def test_node_result_quality_tiers():
    """节点结果的质量分层"""
    from 项目源码.智能体编排.orchestrator import NodeResult, NodeStatus

    r = NodeResult(name="x", status=NodeStatus.SUCCESS, elapsed_ms=100, output={"a": 1})
    assert r.to_dict()["status"] == "success"
    assert r.to_dict()["output"] == {"a": 1}


# ========== Agent 心跳与熔断 ==========


def test_agent_circuit_breaker():
    """
    连续失败应触发熔断

    这是「自愈能力」的基础：单个 Agent 故障不应拖垮整条流水线。
    """
    from 项目源码.智能体编排.orchestrator import BaseAgent

    class FlakyAgent(BaseAgent):
        name = "flaky"

    agent = FlakyAgent()

    # 未失败时熔断器关闭
    assert agent.is_circuit_open is False
    assert agent.health()["healthy"] is True

    # 连续失败达到阈值
    for _ in range(3):
        agent.heartbeat(False)

    assert agent.is_circuit_open is True
    assert agent.health()["healthy"] is False
    assert agent.health()["failure_streak"] == 3


def test_agent_circuit_recovers_on_success():
    """一次成功应重置熔断计数"""
    from 项目源码.智能体编排.orchestrator import BaseAgent

    class FlakyAgent(BaseAgent):
        name = "flaky"

    agent = FlakyAgent()
    agent.heartbeat(False)
    agent.heartbeat(False)
    assert agent.failure_streak == 2

    agent.heartbeat(True)
    assert agent.failure_streak == 0
    assert agent.is_circuit_open is False


@pytest.mark.asyncio
async def test_agent_exception_isolated():
    """
    Agent 抛异常不得向上传播

    异常应转为 status=failed 的结果，其余节点照常执行。
    """
    from 项目源码.智能体编排.orchestrator import (
        BaseAgent,
        NodeStatus,
        WorkflowState,
    )

    class BrokenAgent(BaseAgent):
        name = "broken"

        async def execute(self, state):
            raise RuntimeError("模拟故障")

    state = WorkflowState(workflow_type="test")
    result = await BrokenAgent().run(state)

    assert result.status == NodeStatus.FAILED
    assert "模拟故障" in (result.error or "")
    # 结果已记入状态，后续节点仍可读取
    assert len(state.node_results) == 1


@pytest.mark.asyncio
async def test_circuit_open_skips_agent():
    """熔断打开时应跳过该 Agent，不再执行"""
    from 项目源码.智能体编排.orchestrator import (
        BaseAgent,
        NodeStatus,
        WorkflowState,
    )

    class SkippableAgent(BaseAgent):
        name = "skippable"

        async def execute(self, state):
            raise AssertionError("熔断时不应执行")

    agent = SkippableAgent()
    for _ in range(3):
        agent.heartbeat(False)

    state = WorkflowState(workflow_type="test")
    result = await agent.run(state)
    assert result.status == NodeStatus.SKIPPED
    assert result.error == "circuit_open"


# ========== 主管决策 ==========


def test_supervisor_retry_policy():
    """
    反思循环只重试可恢复的错误

    网络抖动、限流值得重试；
    「未找到该漏洞」这类确定性失败重试也没用。
    """
    from 项目源码.智能体编排.orchestrator import (
        NodeResult,
        NodeStatus,
        SupervisorAgent,
        WorkflowState,
    )

    sup = SupervisorAgent()
    state = WorkflowState(workflow_type="test", max_retry=2)

    # 可恢复错误 → 应重试
    recoverable = NodeResult(
        name="qa", status=NodeStatus.FAILED, error="Request timeout after 60s"
    )
    assert sup.should_retry(state, recoverable) is True

    rate_limited = NodeResult(
        name="qa", status=NodeStatus.FAILED, error="HTTP 429 rate limit"
    )
    assert sup.should_retry(state, rate_limited) is True

    # 确定性失败 → 不重试
    deterministic = NodeResult(
        name="enrich", status=NodeStatus.FAILED, error="未找到该漏洞"
    )
    assert sup.should_retry(state, deterministic) is False

    # 成功 → 不重试
    ok = NodeResult(name="qa", status=NodeStatus.SUCCESS)
    assert sup.should_retry(state, ok) is False


def test_supervisor_respects_max_retry():
    """超过最大重试次数后不再重试"""
    from 项目源码.智能体编排.orchestrator import (
        NodeResult,
        NodeStatus,
        SupervisorAgent,
        WorkflowState,
    )

    sup = SupervisorAgent()
    state = WorkflowState(workflow_type="test", retry_count=2, max_retry=2)
    result = NodeResult(
        name="qa", status=NodeStatus.FAILED, error="connection timeout"
    )
    assert sup.should_retry(state, result) is False


def test_supervisor_human_review_detection():
    """低置信度富化结果应转人工核验"""
    from 项目源码.智能体编排.orchestrator import (
        NodeResult,
        NodeStatus,
        SupervisorAgent,
        WorkflowState,
    )

    sup = SupervisorAgent()
    state = WorkflowState(workflow_type="enrich", confidence_threshold=0.5)

    # 无问题
    state.add_result(
        NodeResult(
            name="enrichment",
            status=NodeStatus.SUCCESS,
            output={"low_confidence_count": 0},
        )
    )
    needs, reason = sup.needs_human_review(state)
    assert needs is False

    # 有低置信度维度
    state2 = WorkflowState(workflow_type="enrich", confidence_threshold=0.5)
    state2.add_result(
        NodeResult(
            name="enrichment",
            status=NodeStatus.SUCCESS,
            output={"low_confidence_count": 2},
        )
    )
    needs2, reason2 = sup.needs_human_review(state2)
    assert needs2 is True
    assert "2" in reason2


def test_supervisor_detects_missing_citations():
    """问答结果无引用应转人工（防编造）"""
    from 项目源码.智能体编排.orchestrator import (
        NodeResult,
        NodeStatus,
        SupervisorAgent,
        WorkflowState,
    )

    sup = SupervisorAgent()
    state = WorkflowState(workflow_type="qa")
    state.add_result(
        NodeResult(
            name="qa",
            status=NodeStatus.SUCCESS,
            output={"answer": "某答案", "citations": []},
        )
    )
    needs, reason = sup.needs_human_review(state)
    assert needs is True
    assert "引用" in reason or "编造" in reason


def test_supervisor_detects_source_failures():
    """过半数据源失败应转人工"""
    from 项目源码.智能体编排.orchestrator import (
        NodeResult,
        NodeStatus,
        SupervisorAgent,
        WorkflowState,
    )

    sup = SupervisorAgent()
    state = WorkflowState(workflow_type="pipeline")
    state.add_result(
        NodeResult(
            name="monitor",
            status=NodeStatus.SUCCESS,
            output={
                "all_sources": [
                    {"source": "nvd", "error": "timeout"},
                    {"source": "ghsa", "error": "timeout"},
                    {"source": "arxiv", "new": 5},
                ]
            },
        )
    )
    needs, reason = sup.needs_human_review(state)
    assert needs is True


# ========== 图结构 ==========


def test_graph_structure_description():
    """图结构描述需体现编排特性（供前端展示）"""
    from 项目源码.智能体编排.orchestrator import get_workflow

    wf = get_workflow()
    structure = wf.graph_structure()

    assert "nodes" in structure and "edges" in structure
    assert len(structure["nodes"]) >= 5
    # 必须体现 LangGraph 的三项特性
    assert any("条件边" in f for f in structure["features"])
    assert any("并行" in f for f in structure["features"])
    assert any("反思" in f for f in structure["features"])


def test_graph_has_all_nodes():
    """编译后的图应包含全部业务节点"""
    from 项目源码.智能体编排.orchestrator import IntelWorkflow

    graph = IntelWorkflow().graph
    nodes = set(graph.get_graph().nodes.keys())

    for expected in ("monitor", "triage", "supervisor", "reflect"):
        assert expected in nodes, f"缺少节点: {expected}"


def test_graph_edge_types():
    """应同时存在普通边与条件边"""
    from 项目源码.智能体编排.orchestrator import IntelWorkflow

    graph = IntelWorkflow().graph
    edges = list(graph.get_graph().edges)

    normal = [e for e in edges if not e.conditional]
    conditional = [e for e in edges if e.conditional]

    assert len(normal) > 0, "缺少普通边"
    assert len(conditional) > 0, "缺少条件边（triage/supervisor 的分支判断）"
