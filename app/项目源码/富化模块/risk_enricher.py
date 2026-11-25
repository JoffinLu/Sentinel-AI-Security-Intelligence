"""
维度七：AI 风险定级

富化的最终输出，也是前端展示与 P3 问答的直接数据来源。

产出三样东西：
1. 赛题四分类归属（AI 基础设施 / 模型与数据 / 应用与智能体 / 供应链）
2. 业务影响评估（对 AI 系统的实际危害）
3. 处置优先级 P1-P4（把技术风险翻译为可执行的安全决策）

优先级评定融合四个信号：
- CVSS 基础分（技术严重度）
- 是否在 CISA KEV（已被在野利用 → 优先级上调）
- AI 相关性（是否属于本系统关注范畴）
- POC 可用性（是否易被利用）

这样定级不是简单搬运 CVSS，而是给出"我该怎么办"的答案——
这正是赛题要求的"AI 安全风险定级"的核心价值。
"""

from __future__ import annotations

from typing import Any

from .base_enricher import BaseEnricher, VulnContext

# 赛题四分类 → 处置优先级基线
_CATEGORY_BASELINE = {
    "AI基础设施/推理框架风险": ("推理服务被直接攻击，影响面广", 1),
    "模型与数据层风险": ("模型或训练数据被污染/窃取，损害核心资产", 2),
    "AI应用与智能体风险": ("应用层被操纵，可能触发工具执行", 2),
    "供应链风险": ("上游投毒会向下游扩散，需追溯排查", 2),
}

# 资产类型 → 业务影响
_ASSET_IMPACT = {
    "推理框架": "推理服务不可用或输出被操纵",
    "应用框架": "上层应用逻辑被绕过",
    "智能体组件": "智能体可能被劫持执行非预期操作",
    "向量数据库": "知识库被污染，检索结果不可信",
    "模型": "模型资产受损或被投毒",
    "数据集": "训练数据泄露或被污染",
    "组件": "依赖链上的组件存在已知缺陷",
    "服务": "服务功能异常或信息泄露",
}


def _detect_ransomware(text: str) -> bool:
    """检测是否涉及勒索软件"""
    t = text.lower()
    return any(k in t for k in ("ransomware", "勒索", "encrypt files", "数据加密勒索"))


def _detect_exploited(text: str) -> bool:
    """检测是否为已在野利用"""
    t = text.lower()
    return any(
        k in t
        for k in ("known exploited", "在野利用", "actively exploited", "observed exploitation")
    )


class RiskEnricher(BaseEnricher):
    """维度七：AI 风险定级"""

    dimension = "risk"
    requires_llm = False  # 规则可完成，LLM 仅作补充说明

    async def enrich(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        full_text = f"{ctx.title} {ctx.description} {ctx.extra.get('required_action', '')}"

        # ---------- 1. 风险分类 ----------
        category = ctx.ai_category
        subcategory = ctx.extra.get("ai_subcategory")
        if not category:
            category, subcategory = self._infer_category(ctx)

        # ---------- 2. 技术严重度 ----------
        cvss = ctx.cvss_score or 0.0
        is_kev = bool(ctx.extra.get("is_kev"))
        exploited = is_kev or _detect_exploited(full_text)
        ransomware = _detect_ransomware(full_text) or (
            ctx.extra.get("known_ransomware") == "Known"
        )

        # ---------- 3. 资产影响 ----------
        assets = ctx.affected_assets or []
        ai_assets = [a for a in assets if a.get("is_ai_component")] or assets
        asset_impact = self._assess_asset_impact(ai_assets)

        # ---------- 4. 优先级评定 ----------
        priority, priority_reason = self._rate_priority(
            cvss=cvss,
            category=category,
            exploited=exploited,
            ransomware=ransomware,
            ai_related=ctx.ai_relevance_score >= 0.5,
        )

        # ---------- 5. 综合评分 ----------
        risk_score = self._compute_risk_score(cvss, exploited, ransomware, category)

        confidence = 0.85 if category else 0.6
        if is_kev or exploited:
            confidence = 0.9  # 有明确利用证据，可信度高

        return (
            {
                "category": category,
                "subcategory": subcategory,
                "priority": priority,
                "priority_label": {
                    "P1": "紧急处置",
                    "P2": "优先处置",
                    "P3": "计划处置",
                    "P4": "持续观察",
                }.get(priority, "计划处置"),
                "priority_reason": priority_reason,
                "risk_score": round(risk_score, 2),
                "risk_level": self._risk_level(risk_score),
                "cvss_score": cvss,
                "is_ai_related": ctx.ai_relevance_score >= 0.5,
                "ai_relevance": round(ctx.ai_relevance_score, 3),
                "actively_exploited": exploited,
                "ransomware_related": ransomware,
                "in_kev_catalog": is_kev,
                "asset_impact": asset_impact,
                "business_impact": self._business_impact(
                    category, asset_impact, ransomware, exploited
                ),
                "recommendation": self._recommend(priority, exploited, ransomware, category),
            },
            confidence,
        )

    def _infer_category(self, ctx: VulnContext) -> tuple[str | None, str | None]:
        """
        未分类时按关键词推断

        置信度有限，仅作为兜底。
        """
        text = f"{ctx.title} {ctx.description}".lower()

        rules = [
            ("AI应用与智能体风险", ["prompt injection", "jailbreak", "rag", "agent", "mcp", "提示词", "智能体"]),
            ("供应链风险", ["supply chain", "pickle", "huggingface", "model repository", "依赖投毒", "恶意模型"]),
            ("AI基础设施/推理框架风险", ["vllm", "ollama", "triton", "serving", "inference server", "推理框架"]),
            ("模型与数据层风险", ["model", "training data", "adversarial", "backdoor", "poisoning", "模型", "训练数据"]),
        ]
        for category, keywords in rules:
            if any(k in text for k in keywords):
                return category, None
        return None, None

    @staticmethod
    def _assess_asset_impact(assets: list[dict[str, Any]]) -> str:
        """评估资产影响面"""
        if not assets:
            return "影响范围待确认（缺少资产信息）"
        products = [str(a.get("product", "")) for a in assets if a.get("product")]
        # 主流 AI 框架受影响 → 影响面广
        major = {"transformers", "torch", "vllm", "langchain", "tensorflow", "numpy", "pillow"}
        hit_major = [p for p in products if p.lower() in major]
        if hit_major:
            return f"影响主流 AI 组件（{', '.join(hit_major[:2])}），波及范围广"
        if len(assets) > 1:
            return f"影响 {len(assets)} 个组件，范围较广"
        return f"影响单一组件（{products[0][:50]}），范围可控" if products else "影响范围待确认"

    @staticmethod
    def _rate_priority(
        cvss: float,
        category: str | None,
        exploited: bool,
        ransomware: bool,
        ai_related: bool,
    ) -> tuple[str, str]:
        """
        处置优先级评定

        最高优先级由「被实际利用」决定，而非仅看 CVSS——
        这是安全运营的真实逻辑：已被利用的漏洞危害远大于潜在风险。
        """
        if ransomware and exploited:
            return "P1", "已在野利用且涉及勒索软件，需立即响应"
        if exploited and cvss >= 7.0:
            return "P1", "已确认在野利用且 CVSS ≥7.0，属紧急风险"
        if exploited:
            return "P2", "已确认在野利用，需优先排查"
        if category == "AI基础设施/推理框架风险" and cvss >= 8.0:
            return "P1", "高危漏洞直击推理服务，可能导致服务中断或输出被操纵"
        if cvss >= 9.0:
            return "P2", "CVSS ≥9.0 极高危，建议尽快修复"
        if cvss >= 7.0 and ai_related:
            return "P2", "高危且属 AI 安全范畴，需优先处置"
        if cvss >= 7.0:
            return "P3", "CVSS ≥7.0，建议纳入近期修复计划"
        if ai_related:
            return "P3", "属 AI 安全范畴，建议持续跟踪厂商公告"
        return "P4", "风险较低，纳入常规监控即可"

    @staticmethod
    def _compute_risk_score(
        cvss: float, exploited: bool, ransomware: bool, category: str | None
    ) -> float:
        """
        综合风险评分（0-100）

        以 CVSS 为基线，用利用状态做乘数调整。
        """
        base = min(cvss, 10.0) * 8  # CVSS 满分 80 分
        if exploited:
            base += 15
        if ransomware:
            base += 5
        if category == "AI基础设施/推理框架风险":
            base += 3
        return min(base, 100.0)

    @staticmethod
    def _risk_level(score: float) -> str:
        """风险等级"""
        if score >= 85:
            return "严重"
        if score >= 65:
            return "高"
        if score >= 40:
            return "中"
        return "低"

    @staticmethod
    def _business_impact(
        category: str | None,
        asset_impact: str,
        ransomware: bool,
        exploited: bool,
    ) -> str:
        """业务影响描述"""
        if ransomware:
            return "可能导致业务数据被加密勒索，直接造成业务中断与数据损失"
        if exploited:
            return f"已被实际利用，攻击者可能已获取系统访问权限。{asset_impact}"

        base = _CATEGORY_BASELINE.get(
            category or "", ("影响 AI 系统的安全与稳定", 3)
        )[0]
        return f"{base}。{asset_impact}"

    @staticmethod
    def _recommend(
        priority: str, exploited: bool, ransomware: bool, category: str | None
    ) -> list[str]:
        """处置建议"""
        actions: list[str] = []
        if ransomware:
            actions.append("立即隔离受影响资产，启动勒索事件响应流程")
            actions.append("核实是否存在数据外泄，评估合规与法律风险")
        if exploited:
            actions.append("排查系统是否已被入侵（横向移动、持久化痕迹）")

        if priority in ("P1", "P2"):
            actions.append("按厂商公告升级至修复版本")
            actions.append("在升级前临时限制相关接口的访问来源")

        if category == "AI基础设施/推理框架风险":
            actions.append("检查推理服务对外暴露面，补充网关与鉴权")
        elif category == "模型与数据层风险":
            actions.append("评估模型权重与训练数据是否已受影响")
        elif category == "AI应用与智能体风险":
            actions.append("审查智能体工具调用权限，收敛可执行操作")
        elif category == "供应链风险":
            actions.append("追溯依赖树，排查同源组件是否受影响")

        if not actions:
            actions.append("纳入常规监控，跟踪厂商公告更新")
        return actions[:5]
