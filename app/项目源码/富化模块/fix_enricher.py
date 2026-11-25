"""
维度五：修复建议

赛题要求富化"修复信息 / 修复建议"。产出：
- 补丁版本（从数据源资产信息提取）
- 修复方案要点（LLM 依据描述与补丁信息生成）
- 官方公告与补丁链接
- 缓解措施（无法立即打补丁时的临时手段）

这个维度直接服务"问答"场景——用户问"这个漏洞怎么修"时要引用它，
因此可操作性比完整性更重要。
"""

from __future__ import annotations

import re
from typing import Any

from ..后端服务.llm_client import get_llm_client
from .base_enricher import BaseEnricher, VulnContext

# 从数据源提取补丁线索
_PATCH_PATTERNS = [
    r"patched?\s+(?:in|version)\s+([\d.]+[\w.-]*)",
    r"fixed?\s+(?:in|version)\s+([\d.]+[\w.-]*)",
    r"upgrade\s+to\s+([\d.]+[\w.-]*)",
    r"first_patched_version[\"':\s]+([\d.]+[\w.-]*)",
    r"版本\s*([\d.]+)\s*(?:已)?修复",
    # NVD/GHSA 描述里的标准写法：「... prior to 2.3.1」/「before 1.2.3」
    # 这类表述在真实漏洞数据里非常常见，但原模式列表完全没覆盖，
    # 导致大量本可自动解析出版本号的漏洞落到 LLM 兜底、
    # patch_version 为 None（实测三种常见写法均解析失败）。
    r"prior\s+to\s+([\d.]+[\w.-]*)",
    r"\bbefore\s+([\d.]+[\w.-]*)",
    # 「fixed in versions 2.3.1, 2.4.0」——复数 versions时用 finditer
    # 逐个匹配，靠 _version_key 取最高
    r"versions?\s+([\d.]+[\w.-]*)",
    # 列表里的后续版本：「fixed in versions 2.3.1, 2.4.0」
    # 上面那条只匹配到第一个，后面几个版本没有关键词前缀，
    # 必须单独用「逗号/顿号 + 版本号」的模式补上，否则取最高版本会漏
    r"[,、]\s*([\d]+\.[\d.]+[\w.-]*)",
    # 中文：「在 2.4.0 版本中已修复」「2.4.0 修复」
    # 原模式要求「版本」在版本号之前，但中文习惯是「版本号 + 版本中已修复」
    r"([\d.]+[\w.-]*)\s*版本",
]

# 官方补丁/公告链接特征
_OFFICIAL_HINTS = ("github.com", "nvd.nist.gov", "cve.org", "mitre.org", "msrc", "cisa.gov")


def _version_key(v: str) -> tuple:
    """
    版本号排序键

    用于「多个候选版本取最高」——描述里常写成
    "fixed in versions 2.3.1, 2.4.0"，应取 2.4.0 而非首个匹配。
    按数字分段比较，缺失段补0，所以 "2.4" < "2.4.0" < "2.4.1"。
    """
    parts: list[int] = []
    for seg in re.split(r"[._-]", v):
        m = re.match(r"^(\d+)", seg)
        parts.append(int(m.group(1)) if m else 0)
    # 补齐到 5 段，避免 [2,4] 与 [2,4,0] 比较时长度不同
    while len(parts) < 5:
        parts.append(0)
    return tuple(parts[:5])


def extract_patch_version(text: str) -> str | None:
    """
    从文本中提取补丁版本号

    同一句里可能出现多个版本（如 "fixed in versions 2.3.1, 2.4.0"），
    此时取最高版本——它才是「升级到哪个版本才安全」的答案。
    """
    best: str | None = None
    for pattern in _PATCH_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.I):
            cand = m.group(1)
            # 清理尾部标点：正则里的 [\w.-]* 会把句末的 '.' 一并吃掉，
            # 导致 "prior to 2.3.1." 提取出 "2.3.1."（带点，版本号不可用）
            cand = cand.rstrip(".,;:")
            if not cand or not re.match(r"^[\d]", cand):
                continue
            if best is None or _version_key(cand) > _version_key(best):
                best = cand
    return best


def extract_fix_links(refs: list[str]) -> list[str]:
    """筛选出官方修复相关链接"""
    out = []
    for ref in refs or []:
        lower = (ref or "").lower()
        if any(h in lower for h in _OFFICIAL_HINTS):
            # 排除 CVE 详情页本身，那是漏洞页不是补丁页
            if "/vuln/detail" not in lower:
                out.append(ref)
    return out[:6]


class FixEnricher(BaseEnricher):
    """维度五：修复建议富化"""

    dimension = "fix"
    requires_llm = True

    async def enrich(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        full_text = f"{ctx.title} {ctx.description}"

        # 规则层：先从数据中提取确定性信息
        patch_version = None
        for asset in ctx.affected_assets or []:
            v = asset.get("version_fixed") or asset.get("patched_version")
            if v:
                patch_version = str(v)
                break
        if not patch_version:
            patch_version = extract_patch_version(full_text)

        fix_links = extract_fix_links(ctx.references)
        # 数据源资产信息里的补丁版本字段
        for asset in ctx.affected_assets or []:
            pv = asset.get("patched_version")
            if pv and not patch_version:
                patch_version = str(pv)

        # 若已拿到确定性修复信息，直接产出，不必调用 LLM
        if patch_version or fix_links:
            content = {
                "patch_version": patch_version,
                "fix_links": fix_links,
                "mitigation": self._derive_mitigation(ctx, patch_version),
                "has_official_fix": True,
                "source": "data_source",
                "method": "从数据源的补丁信息与官方链接提取",
            }
            # 有明确补丁版本时置信度高
            confidence = 0.9 if patch_version else 0.8
            # 若还能补充方案要点则更佳
            guidance = await self._llm_guidance(ctx, patch_version)
            if guidance:
                content["guidance"] = guidance
                content["method"] = "数据源提取 + LLM 方案说明"
                confidence = min(0.95, confidence + 0.03)
            return content, confidence

        # 规则层无收获，走 LLM 生成建议（置信度下调）
        return await self._llm_only(ctx)

    async def _llm_guidance(
        self, ctx: VulnContext, patch_version: str | None
    ) -> dict[str, Any] | None:
        """由 LLM 补充修复方案要点"""
        client = get_llm_client()
        result, resp = await client.chat_json(
            [
                {
                    "role": "user",
                    "content": f"""你是 AI 安全漏洞修复专家。针对以下漏洞给出修复建议。

标题：{ctx.title[:250]}
描述：{ctx.description[:900]}
{"补丁版本：" + patch_version if patch_version else ""}

严格输出 JSON：
{{"summary": "一句话说明漏洞成因", "fix_steps": ["修复步骤1", "步骤2"], "workaround": "临时缓解措施", "caution": "注意事项或null"}}""",
                }
            ],
            temperature=0.2,
            max_tokens=700,
            fallback={},
        )
        if resp.degraded or not result:
            return None
        return {
            "summary": str(result.get("summary", ""))[:300],
            "fix_steps": [str(s)[:200] for s in (result.get("fix_steps") or [])][:5],
            "workaround": str(result.get("workaround", ""))[:300],
            "caution": str(result.get("caution") or "")[:200] or None,
        }

    async def _llm_only(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        """数据源无修复信息时完全依赖 LLM"""
        guidance = await self._llm_guidance(ctx, None)
        if guidance:
            return (
                {
                    "patch_version": None,
                    "fix_links": extract_fix_links(ctx.references),
                    "mitigation": guidance.get("workaround") or "建议升级至最新版",
                    "has_official_fix": False,
                    "source": "llm_advice",
                    "method": "LLM 依漏洞描述生成建议（无官方补丁信息）",
                    "guidance": guidance,
                },
                0.6,
            )

        # LLM 也不可用时给出保守兜底
        return (
            {
                "patch_version": None,
                "fix_links": [],
                "mitigation": "建议关注厂商公告，升级至组件最新版本",
                "has_official_fix": False,
                "source": "fallback",
                "method": "LLM 不可用，给出通用建议",
            },
            0.3,
        )

    @staticmethod
    def _derive_mitigation(ctx: VulnContext, patch_version: str | None) -> str:
        """基于补丁版本推导缓解措施"""
        if patch_version:
            return f"升级受影响组件至 {patch_version} 或更高版本"
        return "升级至组件最新版本；如无法立即升级，限制相关接口的访问来源"
