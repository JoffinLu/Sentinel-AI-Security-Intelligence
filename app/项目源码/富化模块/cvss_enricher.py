"""
维度一：CVSS 评分

赛题要求富化出 CVSS 评分。实现分两条路径：

优先路径（可信度高）：从 NVD 原始报文解析 CVSS 3.1 向量并按规范计算基础分。
    CVSS 3.1 基础分是确定性的数学计算，不应交给模型猜测。

回退路径（覆盖率补齐）：源数据缺失时，由 LLM 依据 CVSS 3.1 规范评估。
    此时置信度上限设为 0.75，明确低于解析路径，体现可信度分层。

CVSS 3.1 基础分公式（CVSS v3.1 Specification 6.1）：

    ISS = 1 - [(1-C) × (1-I) × (1-A)]
    Impact = 6.42 × ISS                       （Scope Unchanged）
    Impact = 7.52 × (ISS-0.029) - 3.25 × (ISS-0.02)^15   （Scope Changed）
    Exploitability = 8.22 × AV × AC × PR × UI
    BaseScore = 0                          （Impact ≤ 0）
    BaseScore = roundup(min(Impact+Exploitability, 10))       （Unchanged）
    BaseScore = roundup(min(1.08×(Impact+Exploitability), 10))（Changed）
"""

from __future__ import annotations

import math
from typing import Any

from ..后端服务.llm_client import get_llm_client
from .base_enricher import BaseEnricher, VulnContext

# CVSS 3.1 指标取值表
_METRIC_VALUES = {
    # 攻击向量 AV
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    # 攻击复杂度 AC
    "AC": {"L": 0.77, "H": 0.44},
    # 权限要求 PR（取值依赖 Scope）
    "PR_U": {"N": 0.85, "L": 0.62, "H": 0.27},
    "PR_C": {"N": 0.85, "L": 0.68, "H": 0.5},
    # 用户交互 UI
    "UI": {"N": 0.85, "R": 0.62},
    # 影响 CIA
    "C": {"H": 0.56, "L": 0.22, "N": 0.0},
    "I": {"H": 0.56, "L": 0.22, "N": 0.0},
    "A": {"H": 0.56, "L": 0.22, "N": 0.0},
}

# 严重程度分级阈值见 severity_from_score 的说明（CVSS v3.1 规范）


def roundup(value: float) -> float:
    """
    CVSS 3.1 定义的向上取整

    官方定义为「roundup(x * 10) / 10」——不是普通四舍五入，
    而是保留一位小数的严格向上取整（0.0 归为 0.0）。
    """
    if value <= 0:
        return 0.0
    int_input = round(value * 100000)
    if int_input % 10000 == 0:
        return int_input / 100000.0
    return (math.floor(int_input / 10000) + 1) / 10.0


def parse_vector(vector: str) -> dict[str, str] | None:
    """
    解析 CVSS 向量字符串为指标字典

    兼容 CVSS 2.0/3.x/4.0 三种规范。
    """
    if not vector or "CVSS:" not in vector.upper():
        return None
    metrics: dict[str, str] = {}
    for part in vector.upper().split("/"):
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        value = value.strip()
        if key.startswith("CVSS") or not value:
            continue
        metrics[key] = value
    return metrics or None


def detect_version(vector: str) -> str | None:
    """
    检测向量版本

    不仅看 CVSS:x.y 标记，也看指标名——
    CVSS 4.0 用 VC/VI/VA/AC/AT/MA 等指标，与 3.x 的 C/I/A 明显不同。
    模型返回值时经常把版本标错，指标名更可靠。
    """
    if not vector:
        return None
    upper = vector.upper()

    # 指标名优先判断（4.0 独有）
    if any(m in upper for m in ("VC:", "VI:", "VA:", "SC:", "SI:", "SA:", "AT:", "MA:")):
        return "4.0"

    # 再看版本标记
    if "CVSS:4" in upper:
        return "4.0"
    if "CVSS:3.1" in upper:
        return "3.1"
    if "CVSS:3" in upper:
        return "3.0"
    if "CVSS:2" in upper:
        return "2.0"
    return None


def compute_base_score(vector: str) -> float | None:
    """
    按 CVSS 规范计算基础分

    3.x 使用 CVSS v3.1 公式（3.0 与 3.1 的基础分公式一致）；
    4.0 使用其 MacroVector 评分体系，算法完全不同，
    需由专门的评分器处理——此处返回 None 表示"需专用算法"，
    调用方应回退到使用数据源提供的分数或 LLM 评估。
    """
    version = detect_version(vector)
    if version is None or version.startswith("4."):
        return None

    m = parse_vector(vector)
    if not m:
        return None

    try:
        av = _METRIC_VALUES["AV"][m["AV"]]
        ac = _METRIC_VALUES["AC"][m["AC"]]
        ui = _METRIC_VALUES["UI"][m["UI"]]
        scope_changed = m.get("S") == "C"
        pr = _METRIC_VALUES["PR_C" if scope_changed else "PR_U"][m["PR"]]
        c = _METRIC_VALUES["C"][m["C"]]
        i = _METRIC_VALUES["I"][m["I"]]
        a = _METRIC_VALUES["A"][m["A"]]
    except KeyError:
        return None

    iss = 1 - ((1 - c) * (1 - i) * (1 - a))
    if scope_changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss

    if impact <= 0:
        return 0.0

    exploitability = 8.22 * av * ac * pr * ui
    raw = impact + exploitability
    if scope_changed:
        raw *= 1.08
    return roundup(min(raw, 10.0))


def severity_from_score(score: float) -> str:
    """
    按 CVSS 3.1 定性评级映射严重程度

    官方评级区间（CVSS v3.1 Qualitative Severity Rating Scale）：
        0.0        None
        0.1 - 3.9  Low
        4.0 - 6.9  Medium
        7.0 - 8.9  High
        9.0 - 10.0 Critical
    """
    if score <= 0:
        return "None"
    if score < 4.0:
        return "Low"
    if score < 7.0:
        return "Medium"
    if score < 9.0:
        return "High"
    return "Critical"


def attack_vector_explanation(vector: str) -> str:
    """生成中文可读的向量说明"""
    m = parse_vector(vector) or {}
    labels = {
        "AV": {"N": "网络", "A": "邻接网络", "L": "本地", "P": "物理"},
        "AC": {"L": "低", "H": "高"},
        "UI": {"N": "无需交互", "R": "需交互"},
        "S": {"U": "影响范围未改变", "C": "影响范围已改变"},
    }
    parts = []
    if "AV" in m:
        parts.append(f"攻击向量 {labels['AV'].get(m['AV'], m['AV'])}")
    if "AC" in m:
        parts.append(f"攻击复杂度 {labels['AC'].get(m['AC'], m['AC'])}")
    if "PR" in m:
        parts.append(f"所需权限 {m['PR']}")
    if "UI" in m:
        parts.append(f"用户交互 {labels['UI'].get(m['UI'], m['UI'])}")
    if "S" in m:
        parts.append(labels["S"].get(m["S"], m["S"]))
    return "；".join(parts)


class CVSSEnricher(BaseEnricher):
    """维度一：CVSS 评分富化"""

    dimension = "cvss"
    requires_llm = True

    async def enrich(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        # 路径一：从源数据解析（3.x 有确定性公式，可信度最高）
        if ctx.cvss_vector:
            version = detect_version(ctx.cvss_vector)
            score = compute_base_score(ctx.cvss_vector)
            if score is not None:
                return (
                    {
                        "score": score,
                        "severity": severity_from_score(score),
                        "vector": ctx.cvss_vector,
                        "version": version,
                        "explanation": attack_vector_explanation(ctx.cvss_vector),
                        "source": "official_vector",
                        "method": f"按 CVSS {version} 规范解析计算",
                    },
                    0.95,
                )

            # CVSS 4.0 使用 MacroVector 评分体系，基础分需由源提供或 LLM 评估。
            # 若数据源已给出分数，直接沿用（仍属权威来源）。
            if ctx.cvss_score is not None:
                return (
                    {
                        "score": ctx.cvss_score,
                        "severity": severity_from_score(ctx.cvss_score),
                        "vector": ctx.cvss_vector,
                        "version": version,
                        "explanation": attack_vector_explanation(ctx.cvss_vector),
                        "source": "official_vector_4_0",
                        "method": f"CVSS {version} 向量需 MacroVector 评分，沿用数据源分数",
                    },
                    0.9,
                )

        # 路径二：源数据已有分数但无向量（如 CISA KEV 目录）
        if ctx.cvss_score is not None:
            return (
                {
                    "score": ctx.cvss_score,
                    "severity": severity_from_score(ctx.cvss_score),
                    "vector": None,
                    "version": detect_version(ctx.cvss_vector) if ctx.cvss_vector else None,
                    "explanation": "沿用数据源提供的评分",
                    "source": "source_provided",
                    "method": "数据源自带评分",
                },
                0.9,
            )

        # 路径三：LLM 按 CVSS 规范评估（置信度上限 0.8，体现可信度分层）
        return await self._llm_estimate(ctx)

    async def _llm_estimate(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        """
        LLM 评估 CVSS

        严格要求模型按 CVSS 3.1 规范推理，但结果本质是主观判断，
        故置信度上限设为 0.8，低于解析路径。
        """
        client = get_llm_client()
        result, resp = await client.chat_json(
            [
                {
                    "role": "user",
                    "content": f"""你是 CVSS 3.1 评分专家。请评估以下漏洞的基础分。

标题：{ctx.title[:300]}
描述：{ctx.description[:1200]}

按 CVSS 3.1 规范评估以下指标：
- AV 攻击向量：N 网络 / A 邻接 / L 本地 / P 物理
- AC 攻击复杂度：L 低 / H 高
- PR 权限要求：N 无需 / L 低权限 / H 高权限
- UI 用户交互：N 无需 / R 需交互
- S 影响范围：U 未改变 / C 已改变
- C/I/A 机密性/完整性/可用性影响：H 高 / L 低 / N 无

严格输出 JSON：
{{"score": 9.8, "severity": "Critical", "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "reason": "一句话依据"}}""",
                }
            ],
            temperature=0.0,
            max_tokens=800,
            fallback={"score": None, "severity": None, "vector": None},
        )

        if resp.degraded or result.get("score") is None:
            # LLM 不可用时降级：用严重程度映射一个保守估计
            fallback_score = {
                "CRITICAL": 9.5,
                "HIGH": 7.5,
                "MEDIUM": 5.0,
                "MODERATE": 5.0,
                "LOW": 2.5,
            }.get((ctx.severity or "").upper(), 5.0)
            return (
                {
                    "score": fallback_score,
                    "severity": ctx.severity or severity_from_score(fallback_score),
                    "vector": None,
                    "explanation": "LLM 不可用，按数据源严重程度保守估计",
                    "source": "fallback_heuristic",
                    "method": "严重程度映射",
                },
                0.4,
            )

        score = float(result["score"])
        vector = result.get("vector")
        # 交叉验证：模型给的分数与向量能否对上。
        # 注意必须校验向量版本——模型常把 CVSS 4.0 的向量误标为 3.1，
        # 而 3.x 公式不认 4.0 的 VC/VI/VA 指标，会算出 0.0 而覆盖掉正确评估。
        confidence = 0.75
        method = "LLM 依 CVSS 3.1 规范评估"
        vector_version = detect_version(vector) if vector else None

        if vector and not vector_version:
            # 模型给了无法识别版本的向量，丢弃它但保留分数
            confidence = 0.6
            method = "LLM 评估，返回的向量格式无效（已忽略向量）"
        elif vector and vector_version.startswith("4."):
            # CVSS 4.0 需 MacroVector 评分体系，3.x 公式不适用，
            # 此处只做数值合理性检查，不覆盖模型给出的分数
            if 0.0 <= score <= 10.0:
                confidence = 0.7
                method = f"LLM 依 CVSS {vector_version} 规范评估（4.0 分数需官方评分器校验）"
            else:
                score = min(max(score, 0.0), 10.0)
                confidence = 0.55
                method = "LLM 评估结果超出 0-10 范围，已截断"
        elif vector:
            computed = compute_base_score(vector)
            if computed is not None and abs(computed - score) < 1.0:
                confidence = 0.8
                method = f"LLM 评估 + CVSS {vector_version} 向量校验通过"
            elif computed is not None:
                # 分数与向量不一致时以规范计算为准
                score = computed
                confidence = 0.7
                method = "LLM 评估，向量与分数不一致，已按规范重算"

        return (
            {
                "score": score,
                "severity": result.get("severity") or severity_from_score(score),
                "vector": vector,
                "version": vector_version,
                "explanation": attack_vector_explanation(vector) if vector else "",
                "reason": str(result.get("reason", ""))[:300],
                "source": "llm_estimate",
                "method": method,
            },
            confidence,
        )
