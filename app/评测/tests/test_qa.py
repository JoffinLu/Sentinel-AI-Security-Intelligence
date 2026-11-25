"""
问答模块单元测试

重点覆盖三条可离线验证的关键逻辑：
  ① 检索融合（RRF 公式正确性）
  ② 意图识别与指代消解（纯规则，可精确断言）
  ③ 引用校验（防编造红线的最后一道防线）

LLM 与向量检索部分需联网/模型，单独标记为集成测试。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ========== 意图识别 ==========


@pytest.fixture
def classifier():
    from 项目源码.问答模块.intent import IntentClassifier

    return IntentClassifier()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected",
    [
        ("CVE-2026-12372 是什么漏洞", "fact"),
        ("CVE-2026-12372 介绍下", "fact"),
        ("哪些 MCP 漏洞已经有可用 POC", "relation"),
        ("哪些 transformers 漏洞已有人利用", "relation"),
        ("这个漏洞怎么修复", "detail"),
        ("如何缓解这个风险", "detail"),
        ("CVE-2026-12372 应该怎么修复", "detail"),
        ("最近有哪些 RAG 投毒漏洞", "trend"),
        ("当前 AI 漏洞的分布如何", "trend"),
    ],
)
async def test_intent_classification(classifier, query, expected):
    """
    意图识别准确率

    特别注意两条易错用例：
      「哪些 MCP 漏洞有 POC」必须判为 relation 而非 detail
        ——它表面含 POC/利用等处置类词，实为关系查询
      「CVE-xxx 应该怎么修复」必须判为 detail 而非 fact
        ——CVE 编号规则不能覆盖明确的处置诉求
    """
    result = await classifier.classify(query)
    assert result.intent.value == expected, (
        f"{query!r} 判为 {result.intent.value}，期望 {expected}"
    )


@pytest.mark.asyncio
async def test_rule_layer_covers_most_queries(classifier):
    """
    规则层应覆盖绝大多数查询

    理由：规则判定零成本、零延迟，LLM 只用于兜底。
    若规则覆盖率过低，LLM 调用量上升会引发网关限流，
    进而拖垮 P95 响应时间（赛题性能指标）。
    """
    queries = [
        "CVE-2026-12372 是什么",
        "mlflow 有哪些漏洞",
        "这个漏洞怎么修复",
        "最近有哪些高危 AI 漏洞",
        "哪些组件有可用 POC",
        "介绍下提示词注入",
    ]
    rule_count = 0
    for q in queries:
        if classifier.classify_by_rule(q) is not None:
            rule_count += 1
    ratio = rule_count / len(queries)
    assert ratio >= 0.8, f"规则层覆盖率仅 {ratio:.0%}，会导致过多 LLM 调用"


# ========== 实体与指代消解 ==========


def test_entity_extraction():
    """实体抽取应识别 CVE 编号与 AI 组件"""
    from 项目源码.问答模块.intent import extract_entities

    ids = extract_entities("CVE-2026-12372 和 GHSA-abcd-efgh-ijkl 有问题")
    assert "CVE-2026-12372" in ids
    assert any("GHSA" in i for i in ids)

    comps = extract_entities("transformers 有哪些漏洞")
    assert "transformers" in comps


def test_coreference_resolution():
    """
    指代消解应把「它」替换为上一轮的实体

    这是多轮对话的基础能力。
    """
    from 项目源码.问答模块.intent import QueryContext, needs_coreference, resolve_coreference

    ctx = QueryContext(session_id="t", last_entities=["CVE-2026-12372"])

    # 含指代且有上文 → 应补全
    assert needs_coreference("它怎么修复")
    resolved, used = resolve_coreference("它怎么修复", ctx)
    assert "CVE-2026-12372" in resolved
    assert used == ["CVE-2026-12372"]

    # 无指代 → 不动
    q = "mlflow 有哪些漏洞"
    resolved2, used2 = resolve_coreference(q, ctx)
    assert resolved2 == q
    assert used2 == []

    # 有指代但无上文 → 保持原样
    empty = QueryContext(session_id="e")
    resolved3, used3 = resolve_coreference("它怎么修复", empty)
    assert resolved3 == "它怎么修复"
    assert used3 == []

    # 查询本身已有明确实体 → 不覆盖
    resolved4, _ = resolve_coreference("CVE-2026-9999 怎么修复", ctx)
    assert resolved4 == "CVE-2026-9999 怎么修复"


def test_session_context_history_limit():
    """会话历史应限制长度，避免上下文无限增长"""
    from 项目源码.问答模块.intent import QueryContext

    ctx = QueryContext(session_id="t")
    for i in range(10):
        ctx.add_turn(f"问题{i}", f"答案{i}")
    assert len(ctx.history) <= 6
    assert ctx.turn == 10


def test_technique_and_time_extraction():
    """攻击技术与时间范围抽取"""
    from 项目源码.问答模块.intent import extract_technique, extract_time_range

    assert extract_technique("提示词注入相关漏洞") is not None
    assert extract_technique("RAG 投毒问题") is not None

    assert extract_time_range("最近30天") == {"days": 30}
    assert extract_time_range("最近2周") == {"days": 14}
    assert extract_time_range("最近3个月") == {"days": 90}
    assert extract_time_range("近期的漏洞") == {"days": 30}


# ========== RRF 融合 ==========


def test_rrf_fusion_formula():
    """
    RRF 融合公式正确性

    score(d) = Σ 1/(k + rank)，k=60
    两条通道都排第一的文档得分必须最高。
    """
    from 项目源码.问答模块.retrieval import RRF_K, HybridRetriever

    # 文档 A 双通道第 1，B 仅向量第 1，C 仅关键词第 1
    vector_hits = [("A", 1, 0.9), ("B", 2, 0.8), ("C", 1, 0.85)]
    keyword_hits = [("A", 1), ("C", 2)]

    fused = HybridRetriever.rrf_fuse(vector_hits, keyword_hits)
    ids = [f[0] for f in fused]

    # A 出现在两个通道，排名均为第 1 → 分数最高
    assert ids[0] == "A"
    # B 只在向量通道，C 只在两个通道但排名较低
    assert set(ids) == {"A", "B", "C"}

    # 验证公式
    top_score = fused[0][1]
    expected = 1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 1)
    assert top_score == pytest.approx(expected)


def test_rrf_recency_boost():
    """
    时间加权应能翻转同分文档的排名

    构造两个文档在两个通道中排名完全对称的场景，
    未加权时分数相同，加权后新的那个应排到前面。
    """
    from 项目源码.问答模块.retrieval import HybridRetriever

    # old: 向量第 2 + 关键词第 2；new: 向量第 2 + 关键词第 2（完全对称）
    vector_hits = [("X", 1, 0.9), ("old", 2, 0.8), ("new", 2, 0.8)]
    keyword_hits = [("X", 1), ("old", 2), ("new", 2)]

    base = HybridRetriever.rrf_fuse(vector_hits, keyword_hits)
    base_scores = {f[0]: f[1] for f in base}
    # 未加权时两者同分
    assert base_scores["old"] == pytest.approx(base_scores["new"])

    # 给 new 加权后应超过 old
    boosted = HybridRetriever.rrf_fuse(
        vector_hits, keyword_hits, {"new": 1.15, "old": 1.0}
    )
    boosted_ids = [f[0] for f in boosted]
    boosted_scores = {f[0]: f[1] for f in boosted}

    assert boosted_scores["new"] > boosted_scores["old"]
    assert boosted_ids.index("new") < boosted_ids.index("old")


# ========== 引用校验（防编造） ==========


def test_citation_validation_removes_fabricated():
    """
    答案中的伪造 CVE 引用必须被剔除

    这是「不编造」红线的最后一道防线——
    模型可能编造不存在的漏洞编号。
    """
    from 项目源码.问答模块.qa_agent import QAAgent
    from 项目源码.问答模块.retrieval import SearchHit

    hits = [
        SearchHit(cve_id="CVE-2026-0001", title="真实漏洞", description="", score=0.9),
        SearchHit(cve_id="CVE-2026-0002", title="真实漏洞2", description="", score=0.8),
    ]

    # 正常答案：不应被改动
    good = "根据分析 [CVE-2026-0001] 存在该问题，建议升级。"
    assert QAAgent._validate_citations(good, hits) == good

    # 含伪造引用：应从正文剔除，并在提示语中说明剔了什么
    # （提示语保留编号是刻意设计——让用户知道哪些引用被丢弃了）
    bad = "漏洞 [CVE-2026-0001] 与 [CVE-2099-9999] 均存在风险。"
    cleaned = QAAgent._validate_citations(bad, hits)
    body = cleaned.split("> 注：")[0]  # 只看正文部分
    assert "CVE-2099-9999" not in body, "伪造引用必须从正文剔除"
    assert "CVE-2026-0001" in body, "真实引用必须保留"
    assert "剔除" in cleaned and "CVE-2099-9999" in cleaned, (
        "应在提示语中说明剔除了哪个编号"
    )


# ========== 关键词检索策略 ==========


def test_cve_id_extraction():
    """CVE/GHSA 编号提取应统一大写"""
    from 项目源码.问答模块.retrieval import _extract_cve_ids

    ids = _extract_cve_ids("cve-2026-12372 和 GHSA-abcd-efgh-ijkl")
    assert "CVE-2026-12372" in ids
    assert "GHSA-ABCD-EFGH-IJKL" in ids
    assert _extract_cve_ids("没有编号") == []


def test_answer_split_for_streaming():
    """流式输出的答案分块不应丢失内容"""
    from 项目源码.后端服务.app import _split_answer

    text = "第一句。第二句！第三句？第四句；后续内容"
    chunks = _split_answer(text, min_len=6)
    assert "".join(chunks) == text, "分块拼接后必须与原文完全一致"
    assert len(chunks) > 1, "长文本应被切成多块"


# ========== 嵌入服务 ==========


def test_zero_padding_preserves_similarity():
    """
    零填充升维不改变余弦相似度

    这是 512→1024 升维方案成立的前提。
    若相似度改变，检索结果会失真。
    """
    import numpy as np

    from 项目源码.问答模块.embedding import TARGET_DIM, EmbeddingService

    rng = np.random.default_rng(42)
    a = rng.normal(size=512).astype("float32")
    b = rng.normal(size=512).astype("float32")
    c = rng.normal(size=512).astype("float32")

    pa = np.array(EmbeddingService._to_target_dim(a))
    pb = np.array(EmbeddingService._to_target_dim(b))
    pc = np.array(EmbeddingService._to_target_dim(c))

    assert pa.shape[0] == TARGET_DIM

    def cos(x, y):
        return float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y)))

    # 升维前后相似度必须一致
    assert cos(a, b) == pytest.approx(cos(pa, pb), abs=1e-5)
    assert cos(a, c) == pytest.approx(cos(pa, pc), abs=1e-5)


# ========== AI 组件识别 ==========


@pytest.mark.parametrize(
    "product,expected",
    [
        ("transformers", True),
        ("torch", True),
        ("@langchain/core", True),
        ("@modelcontextprotocol/sdk", True),
        ("chromadb", True),
        ("blender-mcp", True),
        ("vllm-core", True),
        ("jackson-core", False),
        ("apache", False),
        ("express", False),
        ("gitea", False),
    ],
)
def test_ai_component_identification(product, expected):
    """
    AI 组件识别必须覆盖生态包名的各种形态

    图谱的供应链推理依赖这个判定——
    早期精确匹配导致 64 个组件只识别出 4 个 AI 组件。
    """
    from 项目源码.富化模块.asset_enricher import is_ai_component_name

    assert is_ai_component_name(product) is expected


# ========== 图谱约束 ==========


def test_graph_hop_constraint():
    """多跳查询必须限制在 3 跳以内（赛题约束）"""
    from 项目源码.问答模块.graph import ALLOWED_RELATIONS, MAX_HOPS

    assert MAX_HOPS <= 3, "赛题要求最多 3 跳"
    # 关系类型必须受白名单约束，防止意外的全图遍历
    assert set(ALLOWED_RELATIONS) == {
        "AFFECTS",
        "USES_TECHNIQUE",
        "HAS_POC",
        "CITES",
        "RELATED_TO",
    }

@pytest.mark.parametrize(
    "query",
    [
        "transformers 组件出现过哪些安全问题？",
        "vllm 有什么安全风险？",
        "langchain 有哪些漏洞？",
        "PyTorch 生态的安全问题有哪些？",
        "MCP 相关的漏洞有哪些？",
        "mlflow 有哪些已知漏洞？",
    ],
)
def test_component_queries_covered_by_rules(classifier, query):
    """
    组件类关系查询必须由规则层覆盖

    这批查询曾因关键词表过窄而落入 LLM 兜底，
    被网关限流拖到 12 秒以上，直接导致 P95 严重超标。
    意图识别是问答链路第一步，此处超时���拖累整个响应。
    """
    result = classifier.classify_by_rule(query)
    assert result is not None, f"{query!r} 落入 LLM 兜底，会导致响应超时"
    assert result.intent.value == "relation"


def test_intent_llm_timeout_is_bounded():
    """
    意图识别的 LLM 兜底必须有硬超时

    没有超时保护时，限流会让单个查询卡住十几秒。
    """
    from 项目源码.问答模块.intent import INTENT_LLM_TIMEOUT

    assert INTENT_LLM_TIMEOUT <= 2.0, "意图识别超时必须控制在 2 秒内"
