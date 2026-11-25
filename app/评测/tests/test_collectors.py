# pytest 单元测试
#
# 重点覆盖 AI 安全分类器（项目核心技术难点）与采集器归一化逻辑。
# 测试集是赛题「性能指标」的支撑材料，故用真实数据回归。

import sys
from pathlib import Path

import pytest

# 以项目根为包根导入中文包
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ========== AI 分类器测试 ==========

# 人工标注验证集：(标题, 描述, 是否AI安全)
AI_CASES = [
    # --- 应判为 AI 安全 ---
    (
        "Indirect prompt injection in LLM agent via web content",
        "A remote attacker can inject instructions through retrieved documents to hijack the agent",
        True,
    ),
    (
        "Prompt injection vulnerability in langchain",
        "The application fails to sanitize user input allowing prompt injection to override system prompt",
        True,
    ),
    (
        "Jailbreak bypass in LLM safety guardrails",
        "Adversarial suffix enables jailbreak of aligned models producing harmful output",
        True,
    ),
    (
        "Poisoned model weights enable backdoor attack",
        "A trojaned PyTorch model on HuggingFace executes arbitrary code via pickle deserialization",
        True,
    ),
    (
        "RAG poisoning allows retrieval attack",
        "Malicious documents in vector store poison the RAG pipeline to produce false answers",
        True,
    ),
    (
        "Membership inference on machine learning models",
        "Attackers can determine training data membership via model outputs",
        True,
    ),
    # --- 应判为非 AI 安全 ---
    (
        "SQL injection in WordPress plugin",
        "A vulnerability was found in a WordPress plugin allowing SQL injection",
        False,
    ),
    (
        "Path traversal in Apache Tomcat",
        "Remote attacker can read arbitrary files via path traversal in Tomcat",
        False,
    ),
    (
        "Cross-site scripting in Drupal module",
        "Reflected XSS in a Drupal module allows session hijacking",
        False,
    ),
    (
        "Kernel privilege escalation in Android",
        "Local privilege escalation allows root access on Android devices",
        False,
    ),
    (
        "Hardcoded credentials in Cisco router",
        "A Cisco router firmware contains hardcoded SSH credentials",
        False,
    ),
]


@pytest.fixture(scope="module")
def classifier():
    """分类器实例（关闭 LLM 以保证测试离线可跑）"""
    from 项目源码.数据采集.ai_classifier import AISecurityClassifier

    return AISecurityClassifier(use_llm=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("title,desc,expected", AI_CASES)
async def test_ai_classification(classifier, title, desc, expected):
    """AI 相关性分类准确率验证"""
    result = await classifier.classify(title, desc)
    assert result.is_ai_security == expected, (
        f"分类错误：期望 {expected}，实际 {result.is_ai_security}（{title}）"
    )


def test_category_normalization():
    """一级分类必须对齐赛题四分类"""
    from 项目源码.数据采集.ai_classifier import (
        CATEGORIES,
        to_primary_category,
    )

    # 二级细类应映射到赛题四分类
    assert to_primary_category("提示词注入") == "AI应用与智能体风险"
    assert to_primary_category("模型后门") == "模型与数据层风险"
    assert to_primary_category("AI供应链") == "供应链风险"

    # 容忍 LLM 生成的中文空格变体
    assert to_primary_category("AI 应用与智能体风险") == "AI应用与智能体风险"

    # 映射结果必须落在赛题四分类内
    for sub in ["提示词注入", "模型后门", "AI供应链", "RAG安全", "成员推断"]:
        assert to_primary_category(sub) in CATEGORIES

    # 空值安全
    assert to_primary_category(None) is None


def test_exclusion_words():
    """强排除词应正确识别传统漏洞"""
    from 项目源码.数据采集.ai_classifier import AISecurityClassifier

    c = AISecurityClassifier(use_llm=False)
    r = c._classify_by_rule(
        "SQL injection in WordPress plugin",
        "A vulnerability in a WordPress plugin",
        None,
    )
    assert r is not None
    assert r.is_ai_security is False
    assert r.method == "rule-exclude"


def test_ai_component_heuristic():
    """
    AI 组件作为判定信号

    设计约定：受影响资产命中 AI 组件库时，即使描述无 AI 攻击特征，
    也应纳入 AI 安全范畴（高置信 0.85）——因为 AI 组件本身的漏洞
    即便描述笼统，也属于本系统关注范围。这是有意的宽松策略，
    宁可多收不可漏收，后续可由富化维度做二次精确化。
    """
    from 项目源码.数据采集.ai_classifier import CATEGORIES, AISecurityClassifier

    c = AISecurityClassifier(use_llm=False)
    r = c._classify_by_rule(
        "Some vulnerability in a library",
        "No AI-specific keywords here",
        [{"product": "transformers", "ecosystem": "PyPI"}],
    )
    assert r is not None
    # AI 组件命中 → 判定为 AI 安全，置信度较高
    assert r.is_ai_security is True
    assert r.score >= 0.7
    # 分类必须归一到赛题四分类
    assert r.category in CATEGORIES


def test_non_ai_component_not_flagged():
    """非 AI 组件不应被误判为 AI 安全"""
    from 项目源码.数据采集.ai_classifier import AISecurityClassifier

    c = AISecurityClassifier(use_llm=False)
    r = c._classify_by_rule(
        "Buffer overflow in a media library",
        "A generic stack overflow in libfoo",
        [{"product": "libfoo", "ecosystem": "Debian"}],
    )
    # 可能返回 None（交由下一级）或低分拒绝，但绝不能判为 AI 安全
    if r is not None:
        assert r.is_ai_security is False


# ========== 采集器归一化测试 ==========


def test_nvd_normalize():
    """NVD 报文归一化"""
    from 项目源码.数据采集.nvd_collector import NVDCollector

    c = NVDCollector()
    sample = {
        "cve": {
            "id": "CVE-2026-1234",
            "sourceIdentifier": "cve@mitre.org",
            "published": "2026-01-15T10:00:00.000",
            "lastModified": "2026-02-20T10:00:00.000",
            "descriptions": [{"lang": "en", "value": "Test description"}],
            "metrics": {
                "cvssMetricV31": [
                    {
                        "baseSeverity": "CRITICAL",
                        "cvssData": {
                            "baseScore": 9.8,
                            "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        },
                    }
                ]
            },
            "configurations": [
                {
                    "nodes": [
                        {
                            "cpeMatch": [
                                {
                                    "vulnerable": True,
                                    "criteria": "cpe:2.3:a:huggingface:transformers:*:*:*:*:*:*:*:*",
                                    "versionStartIncluding": "4.0.0",
                                }
                            ]
                        }
                    ]
                }
            ],
            "references": [{"url": "https://example.com"}],
            "weaknesses": [{"description": [{"value": "CWE-77"}]}],
        }
    }

    items = c.normalize(sample)
    assert len(items) == 1
    it = items[0]
    assert it.dedup_key == "CVE-2026-1234"
    assert it.cvss_score == 9.8
    assert it.severity == "CRITICAL"
    assert it.affected_assets[0]["product"] == "transformers"
    assert "CWE-77" in it.extra["cwe_ids"]
    assert it.published_at is not None


def test_ghsa_normalize():
    """GitHub Advisory 报文归一化"""
    from 项目源码.数据采集.github_advisory_collector import GitHubAdvisoryCollector

    c = GitHubAdvisoryCollector()
    sample = {
        "ghsa_id": "GHSA-test-1234",
        "cve_id": "CVE-2026-5678",
        "summary": "Test vulnerability in transformers",
        "description": "A test description",
        "severity": "high",
        "cvss": {"score": 7.5, "vector_string": "CVSS:3.1/AV:N"},
        "published_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "vulnerabilities": [
            {
                "package": {"name": "transformers", "ecosystem": "pip"},
                "vulnerable_version_range": "<4.0.0",
                "first_patched_version": "4.0.0",
            }
        ],
        "cwes": [{"cwe_id": "CWE-502"}],
        "html_url": "https://github.com/advisories/GHSA-test-1234",
        "references": ["https://example.com"],
    }

    items = c.normalize(sample)
    assert len(items) == 1
    it = items[0]
    # dedup_key 统一 ghsa_id：GitHub 事后分配 CVE 后同公告会换钥匙二次入库
    assert it.dedup_key == "GHSA-test-1234"
    assert it.extra["ghsa_id"] == "GHSA-test-1234"
    assert it.extra["cve_id"] == "CVE-2026-5678"  # 真实 CVE 存 extra 供跨源关联
    assert it.cvss_score == 7.5
    assert it.severity == "HIGH"
    assert it.extra["is_ai_component"] is True  # transformers 应被识别为 AI 组件
    assert it.affected_assets[0]["ecosystem"] == "PyPI"  # pip 应规范化为 PyPI


def test_ghsa_normalize_no_cve():
    """无 CVE 的 GHSA 公告：dedup_key 仍是 ghsa_id，extra.cve_id 为 None"""
    from 项目源码.数据采集.github_advisory_collector import GitHubAdvisoryCollector

    c = GitHubAdvisoryCollector()
    sample = {
        "ghsa_id": "GHSA-test-9999",
        "cve_id": None,
        "summary": "Test vulnerability",
        "description": "A test description",
        "severity": "moderate",
        "published_at": "2026-01-01T00:00:00Z",
        "html_url": "https://github.com/advisories/GHSA-test-9999",
    }

    items = c.normalize(sample)
    assert len(items) == 1
    it = items[0]
    assert it.dedup_key == "GHSA-test-9999"
    assert it.extra["cve_id"] is None


def test_cisa_kev_normalize():
    """CISA KEV 报文归一化"""
    from 项目源码.数据采集.cisa_kev_collector import CISAKEVCollector

    c = CISAKEVCollector()
    sample = {
        "cveID": "CVE-2026-9999",
        "vendorProject": "huggingface:transformers",
        "product": "transformers:4.0.0",
        "vulnerabilityName": "Test AI vulnerability",
        "dateAdded": "2026-01-01",
        "knownRansomwareCampaignUse": "Known",
        "requiredAction": "Apply mitigations",
    }

    items = c.normalize(sample)
    assert len(items) == 1
    it = items[0]
    assert it.dedup_key == "CVE-2026-9999"
    assert it.extra["is_kev"] is True
    assert it.extra["known_ransomware"] == "Known"
    # 被勒索软件利用应定为 CRITICAL
    assert it.severity == "CRITICAL"


def test_field_clipping():
    """字段截断应防止超长数据导致事务回滚"""
    from 项目源码.数据采集.ingest_pipeline import FIELD_LIMITS, _clip, _clip_asset

    long_text = "x" * 5000
    assert len(_clip(long_text, 128)) == 128
    assert _clip(None, 128) is None
    assert _clip(123, 128) == 123

    # 资产字段应逐项截断
    asset = {"product": "p" * 900, "vendor": "v" * 900, "ecosystem": "PyPI"}
    safe = _clip_asset(asset)
    assert len(safe["product"]) <= FIELD_LIMITS["product"]
    assert len(safe["vendor"]) <= FIELD_LIMITS["vendor"]
    assert safe["ecosystem"] == "PyPI"


# ========== 配置与降级测试 ==========


def test_settings_derived_properties():
    """配置派生属性"""
    from 项目源码.后端服务.config import get_settings

    s = get_settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.redis_url.startswith("redis://")
    assert s.neo4j_uri.startswith("bolt://")
    assert isinstance(s.cors_origins, list)
    assert "aiintel" in s.database_url


def test_llm_degraded_never_crashes():
    """LLM 完全不可用时必须降级而非抛异常（自愈能力核心保障）"""
    import asyncio

    from 项目源码.后端服务.llm_client import LLMBackend, LLMClient

    async def run():
        client = LLMClient()
        # 用无效端点模拟网关故障
        client._settings.llm_api_key = "invalid-key-for-test"
        client._settings.llm_api_base = "http://127.0.0.1:1/v1"  # 必然连接失败
        client._settings.llm_max_retries = 1
        client._settings.llm_fallback_base = "http://127.0.0.1:1/v1"

        resp = await client.chat([{"role": "user", "content": "test"}])
        assert resp.content, "降级后仍应返回非空内容"
        assert resp.degraded is True
        assert resp.backend == LLMBackend.DEGRADED
        await client.close()

    asyncio.run(run())


def test_prometheus_metrics_registered():
    """
    监控指标应全部注册（监控告警评分项）

    注意：带 label 的指标在未发生调用前不会出现在 collect() 结果中，
    因此这里检查指标定义本身，而非采样值。
    """
    from 项目源码.后端服务 import metrics

    registered = set(metrics.REGISTRY._names_to_collectors.keys())

    # 核心指标：覆盖赛题"性能指标"与"监控告警"两项
    expected = [
        # 采集监测与时效性
        "intel_collect_total",
        "intel_collect_items_total",
        "intel_collect_duration_seconds",
        "intel_ingest_latency_seconds",
        "intel_source_available",
        # AI 分类
        "intel_classify_total",
        "intel_classify_duration_seconds",
        # 富化（7 维）
        "intel_enrich_total",
        "intel_enrich_duration_seconds",
        "intel_enrich_confidence",
        "intel_enrich_pending",
        # LLM 与降级
        "intel_llm_total",
        "intel_llm_duration_seconds",
        "intel_llm_degraded_mode",
        "intel_llm_rate_limited_total",
        # 问答（响应速度 ≤5s 指标）
        "intel_qa_total",
        "intel_qa_duration_seconds",
        # 知识库
        "intel_db_records",
        "intel_build",
    ]
    missing = [m for m in expected if m not in registered]
    assert not missing, f"缺少指标定义: {missing}"


def test_metrics_record_helpers():
    """指标记录辅助函数不应抛异常"""
    from 项目源码.后端服务 import metrics

    metrics.record_collect("test_source", True, 10, 1500)
    metrics.record_ingest_latency("test_source", 3600)
    metrics.record_classify("rule", True)
    metrics.record_llm("primary", True, 1200)
    metrics.record_enrich("cvss", True, 0.9, 800)

    names = {m.name for metric in metrics.REGISTRY.collect() for m in metric.samples}
    assert "intel_collect_total" in names or any("intel_collect" in n for n in names)


def test_collector_registry_complete():
    """
    采集器注册表应覆盖赛题要求的全部渠道与三类知识底座

    赛题原文：「对 CVE/NVD、安全社区、厂商公告、安全博客等主流渠道的
    最新 AI 安全漏洞信息进行自动化监测与采集」+
    「同步采集最新的 AI 安全学术论文、技术标准与政策法规等信息」
    """
    from 项目源码.数据采集.registry import COLLECTOR_CLASSES, get_collectors

    # 赛题要求 7 类以上（优秀档），另要求覆盖论文、技术标准与政策法规三类知识底座
    assert len(COLLECTOR_CLASSES) >= 9, (
        "数据源需覆盖 9 类：漏洞库4 + 学术1 + 社区2 + 标准1 + 政策法规1"
    )

    collectors = get_collectors()
    # 覆盖赛题列举的四类渠道
    codes = set(collectors.keys())
    assert "nvd" in codes  # CVE/NVD
    assert "ghsa" in codes  # 安全社区
    assert "arxiv" in codes  # 学术论文
    assert "atlas" in codes  # 技术标准
    assert "policy" in codes, (
        "赛题明确要求采集政策法规，缺失会直接失分"
    )

    # 每个采集器元信息完整
    for code, c in collectors.items():
        assert c.name, f"{code} 缺少名称"
        assert c.category in {"漏洞库", "学术", "社区", "标准", "政策法规"}, (
            f"{code} 类别非法（赛题要求覆盖论文/标准/政策三类知识底座）"
        )
        assert c.default_interval > 0, f"{code} 未配置调度间隔"


# ========== _parse_iso 时区回归测试 ==========
#
# 背景（2026-10-13 实测修复）：NVD/KEV 的时间串无时区后缀，_parse_iso 曾
# 返回 naive datetime。asyncpg 编码 timestamptz 时会在客户端把 naive 值按
# 本机本地时区（国内 +08）换算，published_at 整体早存 8 小时——NVD 报
# 19:17:39，库里存成 11:17:39+00；nvd 7298 行 + cisa_kev 1740 行全部中招，
# 看板「24 小时发布」窗口因此实际框成 32 小时。而采集延迟计算把同一个值
# 当 UTC 算，数值虽对，两处口径不一致。
# 修复：无时区后缀一律按 UTC 补 tzinfo。以下用例是这条坑的回归防线。


def test_parse_iso_naive_treated_as_utc():
    """无时区后缀（NVD published 的真实形态）必须补 UTC，绝不能留 naive"""
    from 项目源码.数据采集.nvd_collector import NVDCollector

    c = NVDCollector()
    dt = c._parse_iso("2026-10-13T19:17:39.690")
    assert dt is not None
    assert dt.tzinfo is not None, (
        "naive datetime 会被 asyncpg 按本地时区(+08)存进 timestamptz，"
        "published_at 将早 8 小时"
    )
    assert dt.utcoffset().total_seconds() == 0
    # 墙钟本身不变：补的是时区标识，不是挪时间
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (
        2026, 10, 13, 19, 17, 39,
    )


def test_parse_iso_z_suffix_and_explicit_offset():
    """Z 后缀按 UTC；显式偏移原样保留（偏移是上游给的事实，不强行转 UTC）"""
    from 项目源码.数据采集.nvd_collector import NVDCollector

    c = NVDCollector()
    dt_z = c._parse_iso("2026-10-13T19:17:39Z")
    assert dt_z is not None and dt_z.utcoffset().total_seconds() == 0

    dt_off = c._parse_iso("2026-10-14T03:17:39+08:00")
    assert dt_off is not None and dt_off.utcoffset().total_seconds() == 8 * 3600


def test_parse_iso_invalid_returns_none():
    """空值与非法串返回 None，不抛异常"""
    from 项目源码.数据采集.nvd_collector import NVDCollector

    c = NVDCollector()
    assert c._parse_iso(None) is None
    assert c._parse_iso("") is None
    assert c._parse_iso("not-a-date") is None


def test_nvd_normalize_timestamps_are_aware():
    """NVD normalize 产出的 published_at/modified_at 必须带时区（落库口径防线）"""
    from 项目源码.数据采集.nvd_collector import NVDCollector

    c = NVDCollector()
    items = c.normalize(
        {
            "cve": {
                "id": "CVE-2026-9999",
                "published": "2026-10-13T19:17:39.690",
                "lastModified": "2026-10-13T19:20:00.000",
                "descriptions": [{"lang": "en", "value": "test"}],
            }
        }
    )
    assert len(items) == 1
    assert items[0].published_at is not None
    assert items[0].published_at.tzinfo is not None
    assert items[0].modified_at is not None
    assert items[0].modified_at.tzinfo is not None


def test_parse_rfc822_unknown_offset_treated_as_utc():
    """RFC2822 的 -0000（未知偏移）必须补 UTC，不能留 naive（同一陷阱的变种）"""
    from 项目源码.数据采集.community_collector import SecurityRSSCollector

    dt = SecurityRSSCollector._parse_rfc822("Tue, 13 Oct 2026 19:17:39 -0000")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    # 带真实偏移的保持原样
    dt2 = SecurityRSSCollector._parse_rfc822("Wed, 14 Oct 2026 03:17:39 +0800")
    assert dt2 is not None and dt2.utcoffset().total_seconds() == 8 * 3600
    assert SecurityRSSCollector._parse_rfc822(None) is None


# ========== POC 采集器检索策略测试 ==========


class _FakeResp:
    def __init__(self, status: int, items: list):
        self.status_code = status
        self._items = items

    def json(self) -> dict:
        return {"items": self._items}


class _FakeGHClient:
    """按 (query, page) 返回预置响应的假 GitHub 搜索客户端

    q 串形如 "<关键词> created:>=YYYY-MM-DD"，查表前剥掉窗口限定后缀。
    """

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[tuple[str, int, int, str]] = []

    async def get(self, url, params=None, headers=None):
        raw_q = params["q"]
        q = raw_q.split(" created:>=")[0]
        page = params.get("page", 1)
        self.calls.append((raw_q, page, params.get("per_page"), params.get("sort")))
        status, items = self.script.get((q, page), (200, []))
        return _FakeResp(status, items)


async def _async_noop(_):
    return None


async def test_poc_fetch_created_window_pagination_dedup(monkeypatch):
    """created:>= 窗口限定 + per_page 100 + 翻页 + 跨组去重"""
    from 项目源码.数据采集 import poc_collector as pc
    from 项目源码.数据采集.poc_collector import POCCollector

    monkeypatch.setattr(pc.asyncio, "sleep", _async_noop)

    full_page = [
        {"full_name": f"owner/repo{i}", "created_at": "2026-10-01T00:00:00Z"}
        for i in range(100)
    ]
    script = {
        # 第一组：page1 满页 → 触发翻页；page2 不满页 → 停止
        ("prompt injection poc", 1): (200, full_page),
        ("prompt injection poc", 2): (200, full_page[:3]),
        # 第二组：page1 即不满页 → 不翻页；repo0 与第一组重复
        ("jailbreak llm", 1): (
            200,
            [{"full_name": "owner/repo0"}, {"full_name": "owner/newrepo"}],
        ),
    }
    client = _FakeGHClient(script)

    c = POCCollector()

    async def _fake_get_client():
        return client

    monkeypatch.setattr(c, "get_client", _fake_get_client)

    items = await c.fetch(None)

    # 检索式带 created:>= 限定，per_page 为 100
    q, _page, per_page, _sort = client.calls[0]
    assert "created:>=" in q
    assert per_page == 100
    # 翻页行为：满页组翻到 page2，不满页组只取 page1
    pages_q1 = [p for (qq, p, _, _) in client.calls if qq.startswith("prompt injection poc")]
    assert pages_q1 == [1, 2]
    pages_q2 = [p for (qq, p, _, _) in client.calls if qq.startswith("jailbreak llm")]
    assert pages_q2 == [1]
    # 跨组 + 跨页去重：100 + 3(重复) + 1(新) = 101
    names = [r["full_name"] for r in items]
    assert len(names) == len(set(names)) == 101
    assert "owner/newrepo" in names


async def test_poc_fetch_rate_limit_breaks_pagination(monkeypatch):
    """403 限流：等待后放弃该组剩余翻页，不卡死循环"""
    from 项目源码.数据采集 import poc_collector as pc
    from 项目源码.数据采集.poc_collector import POCCollector

    slept: list[float] = []

    async def _record_sleep(sec):
        slept.append(sec)

    monkeypatch.setattr(pc.asyncio, "sleep", _record_sleep)

    script = {("prompt injection poc", 1): (403, [])}
    client = _FakeGHClient(script)

    c = POCCollector()

    async def _fake_get_client():
        return client

    monkeypatch.setattr(c, "get_client", _fake_get_client)

    items = await c.fetch(None)

    assert items == []
    # 每组限流只请求一次 page1（不翻页），并触发 20s 等待
    pages = [p for (_q, p, _, _) in client.calls]
    assert pages and all(p == 1 for p in pages)
    assert 20 in slept
