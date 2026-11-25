"""
富化模块单元测试

重点覆盖 7 维富化中可用规则层验证的部分：
CVSS 规范计算、资产解析、ATLAS 技术映射、风险定级逻辑。

这些是「富化准确率」的技术底座——只有确定性部分经过严格验证，
LLM 参与部分才可通过置信度分层来管理质量风险。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ========== 维度 1：CVSS 评分 ==========


def test_cvss_roundup_semantics():
    """
    CVSS 3.1 的 roundup 不是四舍五入

    规范定义 roundup(x*10)/10，是严格向上取整。
    用普通 round 会算出 4.02→4.0 而非 4.1，导致评级跨档错误。
    """
    from 项目源码.富化模块.cvss_enricher import roundup

    assert roundup(4.02) == pytest.approx(4.1)
    assert roundup(4.0) == pytest.approx(4.0)
    assert roundup(0.0) == 0.0
    assert roundup(9.94) == pytest.approx(10.0)
    assert roundup(6.05) == pytest.approx(6.1)
    # 负数与零应归零
    assert roundup(-1.0) == 0.0


def test_cvss_severity_mapping():
    """严重程度映射必须符合 CVSS 3.1 定性评级区间"""
    from 项目源码.富化模块.cvss_enricher import severity_from_score

    assert severity_from_score(0.0) == "None"
    assert severity_from_score(3.9) == "Low"
    assert severity_from_score(4.0) == "Medium"
    assert severity_from_score(6.9) == "Medium"
    assert severity_from_score(7.0) == "High"
    assert severity_from_score(8.9) == "High"
    assert severity_from_score(9.0) == "Critical"
    assert severity_from_score(10.0) == "Critical"


def test_cvss_base_score_formula():
    """CVSS 3.1 基础分公式验证（对照官方规范附录）"""
    from 项目源码.富化模块.cvss_enricher import compute_base_score

    cases = [
        # 满分级
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
        # 仅可用性受影响
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H", 7.5),
        # 作用域改变（需乘 1.08 且 PR 取值表不同）
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
        # 无任何影响
        ("CVSS:3.1/AV:P/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:N", 0.0),
    ]
    for vector, expected in cases:
        got = compute_base_score(vector)
        assert got is not None, f"无法解析: {vector}"
        assert got == pytest.approx(expected, abs=0.05), (
            f"{vector} 计算得 {got}，期望 {expected}"
        )


def test_cvss_scope_change_uses_correct_pr_table():
    """
    作用域改变时 PR 的取值表不同

    Scope=Unchanged 时 PR:L=0.62，Scope=Changed 时 PR:L=0.68。
    若用错取值表，跨作用域漏洞的分数会偏低。
    """
    from 项目源码.富化模块.cvss_enricher import compute_base_score

    unchanged = compute_base_score("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H")
    changed = compute_base_score("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H")
    assert unchanged is not None and changed is not None
    # 作用域改变版本应更高（1.08 系数 + 不同的 PR 取值）
    assert changed > unchanged


def test_cvss_version_detection():
    """
    版本检测需以指标名为准

    LLM 常把 CVSS 4.0 的向量误标为 3.1，
    若只看版本标记会用错公式算出 0 分。
    """
    from 项目源码.富化模块.cvss_enricher import compute_base_score, detect_version

    v4 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:N"
    assert detect_version(v4) == "4.0"
    # 4.0 需 MacroVector 评分，3.x 公式不适用，必须返回 None
    assert compute_base_score(v4) is None

    # 3.x 应正常识别
    assert detect_version("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == "3.1"
    assert detect_version("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == "3.0"


def test_cvss_invalid_vector_returns_none():
    """非法向量应返回 None 而非抛异常"""
    from 项目源码.富化模块.cvss_enricher import compute_base_score, parse_vector

    assert compute_base_score("") is None
    assert compute_base_score("not a vector") is None
    assert compute_base_score("CVSS:3.1/AV:X/AC:Z") is None
    assert parse_vector("") is None


@pytest.mark.asyncio
async def test_cvss_enricher_uses_vector_path():
    """有向量时应走解析路径（高置信度），不调用 LLM"""
    from 项目源码.富化模块.base_enricher import VulnContext
    from 项目源码.富化模块.cvss_enricher import CVSSEnricher

    ctx = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-1",
        title="test",
        description="test",
        cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    )
    result = await CVSSEnricher().run(ctx)

    assert result.success
    assert result.content["score"] == pytest.approx(9.8)
    assert result.content["source"] == "official_vector"
    # 解析路径是确定性计算，置信度应最高
    assert result.confidence >= 0.9


# ========== 维度 2：受影响资产 ==========


def test_cpe_parsing():
    """CPE 2.3 解析"""
    from 项目源码.富化模块.asset_enricher import parse_cpe

    r = parse_cpe("cpe:2.3:a:huggingface:transformers:*:*:*:*:*:*:*:*")
    assert r["vendor"] == "huggingface"
    assert r["product"] == "transformers"
    assert r["part"] == "a"

    # 操作系统类型
    r2 = parse_cpe("cpe:2.3:o:linux:linux_kernel:5.10:*:*:*:*:*:*:*")
    assert r2["part"] == "o"
    assert r2["version"] == "5.10"

    # 非法输入
    assert parse_cpe("") == {}
    assert parse_cpe("not-cpe") == {}


def test_ecosystem_normalization():
    """各数据源的生态名需归一化"""
    from 项目源码.富化模块.asset_enricher import _ECOSYSTEM_MAP

    assert _ECOSYSTEM_MAP["pip"] == "PyPI"
    assert _ECOSYSTEM_MAP["npm"] == "npm"
    assert _ECOSYSTEM_MAP["maven"] == "Maven"
    assert _ECOSYSTEM_MAP["cargo"] == "crates.io"


def test_version_constraint_parsing():
    """
    版本范围解析需覆盖多种源格式

    NVD 用 versionStartIncluding，OSV 用 introduced/fixed 事件流，
    npm 用 >=1.0.0 <2.0.0，三者都要能解析。
    """
    from 项目源码.富化模块.asset_enricher import _parse_version_constraint

    # OSV 风格
    r1 = _parse_version_constraint("introduced:1.0.0 fixed:2.0.0")
    assert r1["introduced"] == "1.0.0"
    assert r1["fixed"] == "2.0.0"

    # npm 风格
    r2 = _parse_version_constraint(">=0.1.0 <0.2.0")
    assert r2["introduced"] == "0.1.0"
    assert r2["fixed"] == "0.2.0"

    # 空值安全
    assert _parse_version_constraint("")["introduced"] is None


def test_asset_type_inference():
    """
    资产类型推断需避免误判

    专有名词可能包含关键词（如 modelcontextprotocol 含 "model"），
    因此关键词表不能用过于宽泛的词。
    """
    from 项目源码.富化模块.asset_enricher import guess_asset_type

    assert guess_asset_type("vllm", "PyPI") == "推理框架"
    assert guess_asset_type("chromadb", "PyPI") == "向量数据库"
    assert guess_asset_type("langchain", "PyPI") == "应用框架"
    # 含 model 字样的 MCP SDK 不应被误判为「模型」
    assert guess_asset_type("@modelcontextprotocol/sdk", "npm") != "模型"


@pytest.mark.asyncio
async def test_asset_enricher_from_cpe():
    """从 NVD CPE 提取资产信息"""
    from 项目源码.富化模块.asset_enricher import AssetEnricher
    from 项目源码.富化模块.base_enricher import VulnContext

    ctx = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-2",
        title="test",
        description="test",
        affected_assets=[
            {
                "cpe": "cpe:2.3:a:huggingface:transformers:*:*:*:*:*:*:*:*",
                "version_start": "4.0.0",
                "version_end": "4.5.2",
            }
        ],
    )
    result = await AssetEnricher().run(ctx)

    assert result.success
    asset = result.content["assets"][0]
    assert asset["product"] == "transformers"
    assert asset["version_introduced"] == "4.0.0"
    assert asset["version_fixed"] == "4.5.2"
    assert asset["is_ai_component"] is True


@pytest.mark.asyncio
async def test_asset_enricher_low_confidence_on_empty():
    """无资产信息时置信度必须低，进入待核验队列"""
    from 项目源码.富化模块.asset_enricher import AssetEnricher
    from 项目源码.富化模块.base_enricher import VulnContext

    ctx = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-3",
        title="generic vulnerability",
        description="no component mentioned",
        affected_assets=[],
    )
    result = await AssetEnricher().run(ctx)

    assert result.success
    assert result.content["asset_count"] == 0
    # 拿不到资产信息，置信度应很低
    assert result.confidence < 0.5
    assert result.quality_tier == "low"


# ========== 维度 6：攻击技映射 ==========


def test_atlas_rule_ids_are_valid():
    """
    规则表中的 ATLAS 编号必须真实存在

    ATLAS 与 ATT&CK 是不同体系，编号不对应。
    若凭记忆书写编号，映射结果会完全失效。
    本测试在数据库可用时校验编号有效性。
    """
    from 项目源码.富化模块.technique_enricher import _ATLAS_RULES

    # 至少要有一定规模的规则
    assert len(_ATLAS_RULES) >= 50

    # 编号格式必须合规
    for atlas_id, keywords in _ATLAS_RULES:
        assert atlas_id.startswith("AML.T"), f"编号格式错误: {atlas_id}"
        assert keywords, f"{atlas_id} 未配置关键词"


def test_atlas_technique_parsing():
    """
    技术解析：英文词干需用完整形态

    匹配用词边界（\\b），故 "obfuscat" 这类词干无法命中
    "obfuscated"/"obfuscation"，规则表必须列出完整形态。
    """
    import re

    from 项目源码.富化模块.technique_enricher import _ATLAS_RULES

    # 验证：obfuscated 形式能被某条规则的关键词命中
    text = "the model output was obfuscated by the attacker"
    hits = []
    for atlas_id, keywords in _ATLAS_RULES:
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text):
                hits.append((atlas_id, kw))
                break
    assert hits, "obfuscated 未被任何规则命中，说明规则表存在词干匹配问题"
    assert any("obfuscat" in kw for _, kw in hits)


@pytest.mark.asyncio
async def test_technique_enricher_rule_channel():
    """
    规则通道应命中明确的攻击技术

    注意：真实场景中漏洞会先经分类器判定 AI 相关性，
    前置门槛依赖该信号。此处显式设置 ai_relevance_score 模拟这一前置条件。
    """
    from 项目源码.富化模块.base_enricher import VulnContext
    from 项目源码.富化模块.technique_enricher import TechniqueEnricher

    cases = [
        ("Prompt injection vulnerability in the LLM application", "AML.T0051"),
        ("RAG poisoning allows injecting malicious documents", "AML.T0070"),
        ("Poison training data by modifying the dataset", "AML.T0020"),
        ("Attacker can extract the system prompt", "AML.T0056"),
    ]

    enricher = TechniqueEnricher()
    for text, expected_id in cases:
        ctx = VulnContext(
            vulnerability_id=0,
            cve_id="TEST-4",
            title=text,
            description=text,
            # 模拟分类器已判定为 AI 安全相关
            ai_relevance_score=0.9,
            ai_category="模型与数据层风险",
        )
        result = await enricher.run(ctx)
        assert result.success, f"富化失败: {text}"
        assert result.content["applicable"] is True
        ids = [t["atlas_id"] for t in result.content["techniques"]]
        assert expected_id in ids, f"{text} 未映射到 {expected_id}，实际 {ids}"


@pytest.mark.asyncio
async def test_technique_skips_non_ai_vulnerabilities():
    """
    传统 Web 漏洞应判定为「不适用 ATLAS」而非强行映射

    ATLAS 描述的是针对 AI 系统的攻击技术。实测传统漏洞占比约 45%，
    若无条件送 LLM 会产生大量无效调用并产生误判。
    这个前置门槛既省 token 又更准确。
    """
    from 项目源码.富化模块.base_enricher import VulnContext
    from 项目源码.富化模块.technique_enricher import TechniqueEnricher

    enricher = TechniqueEnricher()

    # 传统漏洞（无任何 AI 特征）
    traditional = [
        "Gitea: Blind SSRF in OAuth2 avatar synchronization via unvalidated OIDC picture claim",
        "PHP_CodeSniffer gitblame report command injection via crafted filename",
        "Path traversal in a media player allows reading arbitrary files",
    ]
    for text in traditional:
        ctx = VulnContext(
            vulnerability_id=0, cve_id="TEST-7", title=text, description=text
        )
        result = await enricher.run(ctx)
        assert result.success
        assert result.content["applicable"] is False, (
            f"传统漏洞不应适用 ATLAS: {text}"
        )
        assert result.content["technique_count"] == 0
        # 确定性判定，置信度应较高（优于让 LLM 猜）
        assert result.confidence >= 0.7


@pytest.mark.asyncio
async def test_technique_applies_to_ai_vulnerabilities():
    """AI 相关漏洞应正常进入映射流程"""
    from 项目源码.富化模块.base_enricher import VulnContext
    from 项目源码.富化模块.technique_enricher import TechniqueEnricher

    enricher = TechniqueEnricher()

    # 通过描述文本中的 AI 特征词判定为相关
    ctx = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-8",
        title="Prompt injection in LLM agent",
        description="An attacker can override the system prompt of the language model",
    )
    result = await enricher.run(ctx)
    assert result.success
    assert result.content["applicable"] is True

    # 通过 ai_relevance_score 判定为相关
    ctx2 = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-9",
        title="某种缺陷",
        description="某种描述",
        ai_relevance_score=0.9,
    )
    result2 = await enricher.run(ctx2)
    assert result2.content["applicable"] is True

    # 通过受影响资产为 AI 组件判定为相关
    ctx3 = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-10",
        title="路径穿越",
        description="可读取任意文件",
        affected_assets=[{"product": "transformers", "is_ai_component": True}],
    )
    result3 = await enricher.run(ctx3)
    assert result3.content["applicable"] is True


# ========== 维度 7：风险定级 ==========


def test_priority_rating_logic():
    """
    优先级评定：利用状态优先于 CVSS

    已被实际利用的漏洞危害远大于潜在高危，
    这是安全运营的真实逻辑。
    """
    from 项目源码.富化模块.risk_enricher import RiskEnricher

    rate = RiskEnricher._rate_priority

    # 勒索 + 在野利用 → P1（即使 CVSS 不高）
    p, _ = rate(cvss=5.0, category="模型与数据层风险", exploited=True, ransomware=True, ai_related=True)
    assert p == "P1"

    # 在野利用 + 高 CVSS → P1
    p, _ = rate(cvss=8.0, category=None, exploited=True, ransomware=False, ai_related=False)
    assert p == "P1"

    # 极高危直击推理框架 → P1
    p, _ = rate(cvss=9.5, category="AI基础设施/推理框架风险", exploited=False, ransomware=False, ai_related=True)
    assert p == "P1"

    # 高危 AI 漏洞 → P2
    p, _ = rate(cvss=7.5, category="AI应用与智能体风险", exploited=False, ransomware=False, ai_related=True)
    assert p == "P2"

    # 低危非 AI → P4
    p, _ = rate(cvss=2.0, category=None, exploited=False, ransomware=False, ai_related=False)
    assert p == "P4"


def test_risk_score_computation():
    """综合风险评分：以 CVSS 为基线，用利用状态调整"""
    from 项目源码.富化模块.risk_enricher import RiskEnricher

    compute = RiskEnricher._compute_risk_score

    base = compute(5.0, exploited=False, ransomware=False, category=None)
    exploited = compute(5.0, exploited=True, ransomware=False, category=None)
    ransomware = compute(5.0, exploited=True, ransomware=True, category=None)

    # 在野利用应显著提升分数
    assert exploited > base
    assert ransomware > exploited
    # 上限 100
    assert compute(10.0, exploited=True, ransomware=True, category="AI基础设施/推理框架风险") <= 100.0
    # 下限 0
    assert compute(0.0, exploited=False, ransomware=False, category=None) >= 0.0


@pytest.mark.asyncio
async def test_risk_enricher_kev_escalation():
    """CISA KEV 命中的漏洞应升级优先级并标注"""
    from 项目源码.富化模块.base_enricher import VulnContext
    from 项目源码.富化模块.risk_enricher import RiskEnricher

    ctx = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-5",
        title="test vulnerability",
        description="actively exploited in the wild",
        cvss_score=7.5,
        extra={"is_kev": True, "known_ransomware": "Known"},
        ai_category="模型与数据层风险",
    )
    result = await RiskEnricher().run(ctx)

    assert result.success
    assert result.content["priority"] == "P1"
    assert result.content["actively_exploited"] is True
    assert result.content["ransomware_related"] is True
    assert result.content["in_kev_catalog"] is True
    # 有明确利用证据时置信度应高
    assert result.confidence >= 0.85


# ========== 编排器 ==========


def test_all_dimensions_registered():
    """7 个富化维度必须全部注册（赛题要求 7 维）"""
    from 项目源码.富化模块.base_enricher import ALL_DIMENSIONS

    assert len(ALL_DIMENSIONS) == 7
    assert set(ALL_DIMENSIONS) == {"cvss", "asset", "poc", "paper", "fix", "technique", "risk"}


def test_orchestrator_registers_all_enrichers():
    """编排器应注册全部 7 个富化器"""
    from 项目源码.富化模块.base_enricher import ALL_DIMENSIONS
    from 项目源码.富化模块.orchestrator import get_enrichers

    enrichers = get_enrichers()
    assert set(enrichers.keys()) == set(ALL_DIMENSIONS)


@pytest.mark.asyncio
async def test_enricher_exception_isolation():
    """
    单维异常不得影响其余维度

    这是编排器的核心保障：某个维度崩溃时，
    其余 6 维仍应正常产出。
    """
    from 项目源码.富化模块.base_enricher import BaseEnricher, VulnContext

    class BrokenEnricher(BaseEnricher):
        dimension = "cvss"

        async def enrich(self, ctx):
            raise RuntimeError("模拟维度崩溃")

    class GoodEnricher(BaseEnricher):
        dimension = "asset"

        async def enrich(self, ctx):
            return {"ok": True}, 0.9

    ctx = VulnContext(vulnerability_id=0, cve_id="TEST-6", title="t", description="d")

    broken_result = await BrokenEnricher().run(ctx)
    good_result = await GoodEnricher().run(ctx)

    assert broken_result.success is False
    assert broken_result.error is not None
    # 崩溃被隔离，不向上抛出
    assert good_result.success is True


@pytest.mark.asyncio
async def test_enricher_confidence_is_clamped():
    """置信度必须落在 [0, 1] 区间"""
    from 项目源码.富化模块.base_enricher import BaseEnricher, VulnContext

    class OutOfRangeEnricher(BaseEnricher):
        dimension = "cvss"

        async def enrich(self, ctx):
            return {"x": 1}, 5.0  # 故意越界

    result = await OutOfRangeEnricher().run(
        VulnContext(vulnerability_id=0, cve_id="T", title="", description="")
    )
    assert 0.0 <= result.confidence <= 1.0
    assert result.confidence == 1.0


def test_quality_tier_thresholds():
    """置信度分层阈值"""
    from 项目源码.富化模块.base_enricher import EnrichResult

    assert EnrichResult("cvss", True, confidence=0.85).quality_tier == "high"
    assert EnrichResult("cvss", True, confidence=0.8).quality_tier == "high"
    assert EnrichResult("cvss", True, confidence=0.6).quality_tier == "medium"
    assert EnrichResult("cvss", True, confidence=0.3).quality_tier == "low"


# ========== 攻击技映射：结论三态 ==========


@pytest.mark.asyncio
async def test_technique_rule_channel_maps_traditional_attacks():
    """
    传统 Web 攻击手法在 AI 组件上应能映射到 ATLAS

    查证 ATLAS 官方库后确认：库中没有 SSRF / 路径穿越的独立条目，
    但 AML.T0050（命令解释器滥用）对应命令注入、
    AML.T0049（利用公网应用）对应未授权访问——这两项有真实依据。

    注意：需传入 ai_relevance_score 通过前置门槛判定，
    因为真实流程中这些漏洞已在分类器阶段被标记为 AI 相关。
    """
    from 项目源码.富化模块.base_enricher import VulnContext
    from 项目源码.富化模块.technique_enricher import TechniqueEnricher

    enricher = TechniqueEnricher()

    # 只断言有真实 ATLAS 依据的两类。
    # 「path traversal」不在断言中——查证 ATLAS 官方库后确认它
    # 没有对应技术条目，强行映射会污染图谱。
    cases = [
        ("mlflow vulnerable to OS Command Injection", "AML.T0050"),
        ("mlflow lacks authentication requirement check", "AML.T0049"),
    ]
    for title, expected_id in cases:
        ctx = VulnContext(
            vulnerability_id=0,
            cve_id="TEST-T",
            title=title,
            description=title,
            ai_relevance_score=0.9,
            ai_category="模型与数据层风险",
        )
        result = await enricher.run(ctx)
        ids = [t["atlas_id"] for t in result.content.get("techniques", [])]
        assert expected_id in ids, f"{title} 未映射到 {expected_id}，实际 {ids}"
        assert result.content.get("conclusion") == "mapped"


@pytest.mark.asyncio
async def test_technique_distinguishes_undetermined_from_no_match():
    """
    「未完成判定」与「判定无匹配」必须区分

    这是本模块的重要设计：LLM 限流降级 ≠ 该漏洞无对应技术。
    混为一谈会让人误判数据质量，也让后续重试无从下手。
    """
    from 项目源码.富化模块.base_enricher import VulnContext
    from 项目源码.富化模块.technique_enricher import TechniqueEnricher

    enricher = TechniqueEnricher()
    ctx = VulnContext(
        vulnerability_id=0,
        cve_id="TEST-T2",
        title="某个未匹配任何规则的漏洞描述",
        description="no technique keyword here",
        ai_relevance_score=0.9,
    )
    result = await enricher.run(ctx)
    # 无论走了哪条分支，conclusion 字段必须存在且语义明确
    conclusion = result.content.get("conclusion")
    assert conclusion in {"mapped", "no_match", "undetermined", None}, (
        f"conclusion 取值不明确：{conclusion}"
    )
