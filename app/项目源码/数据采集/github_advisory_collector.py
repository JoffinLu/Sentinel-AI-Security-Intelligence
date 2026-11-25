"""
GitHub Security Advisory 数据源

AI 生态漏洞最集中的来源——transformers、torch、langchain、vllm 等
主流 AI 框架的漏洞在此首发，是本系统"AI 安全"特性的关键数据支撑。

无需认证即可访问公开 Advisory（限流 60 次/小时），配置 GITHUB_TOKEN 可提升至 5000 次/小时。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .base_collector import BaseCollector, RawItem

API_BASE = "https://api.github.com/advisories"

# 重点关注的 AI 生态关键词，用于标记 is_ai_component
AI_AI_ECOSYSTEM = {
    "transformers", "torch", "pytorch", "tensorflow", "jax",
    "langchain", "llama-index", "llama_index", "autogen", "semantic-kernel",
    "vllm", "ollama", "llama.cpp", "huggingface", "diffusers",
    "accelerate", "datasets", "sentence-transformers", "timm",
    "openai", "anthropic", "langgraph", "haystack", "chainlit",
    "gradio", "streamlit", "triton", "tensorrt", "mlflow", "ray",
}


class GitHubAdvisoryCollector(BaseCollector):
    """GitHub 安全公告数据源"""

    code = "ghsa"
    name = "GitHub Security Advisory"
    category = "漏洞库"
    homepage = "https://github.com/advisories"
    default_interval = 7200  # 2 小时（GitHub API 认证配额 5000/h，2h 稳妥）
    # 已配置 GITHUB_TOKEN → 5000 次/小时，30 分钟无压力。
    # 未认证时仅 60 次/小时，此时不应快于 2 小时（见 config.github_token）。
    min_interval = 1800
    max_interval = 43200
    interval_basis = "GitHub API 认证后 5000 次/小时（本项目已配 Token）；未认证仅 60 次/小时"
    ai_native = True  # 天然 AI 生态偏向

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        拉取安全公告

        优先按更新时间倒序取全部，再在本地按游标过滤——
        公告 API 不支持服务端时间区间查询。
        """
        client = await self.get_client()
        headers = {"Accept": "application/vnd.github+json"}
        # 可选：配置 GITHUB_TOKEN 后限流从 60 次/小时提升至 5000 次/小时
        github_token = getattr(self._settings, "github_token", "")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        all_items: list[Any] = []
        page = 1
        per_page = 100
        max_pages = 5  # 覆盖最近约 500 条公告

        while page <= max_pages:
            params = {
                "per_page": per_page,
                "page": page,
                "sort": "updated",
                "direction": "desc",
            }
            resp = await client.get(API_BASE, params=params, headers=headers)
            if resp.status_code != 200:
                self.log.warning("ghsa_fetch_status", status=resp.status_code)
                break
            batch = resp.json()
            if not batch:
                break
            all_items.extend(batch)
            self.log.info("ghsa_page_fetched", page=page, count=len(batch))
            page += 1
            # 匿名访问限流 60 次/小时，此处轻量节流即可
            await asyncio.sleep(1.5)

        return all_items

    def normalize(self, raw: Any) -> list[RawItem]:
        """GHSA 报文归一化"""
        ghsa_id = raw.get("ghsa_id")
        if not ghsa_id:
            return []

        # dedup_key 统一用 ghsa_id：GitHub 事后分配 CVE 后同公告会换钥匙二次入库
        # （实测 4 对双胞胎：GHSA 键 11h → 次日 CVE 键 33h，后者实为 CVE 分配延迟）。
        # 真实 CVE 存入 extra 供跨源关联，不进 cve_id 列（unique 约束会撞车）。
        summary = raw.get("summary", "")
        description = raw.get("description") or summary

        # CVSS
        cvss = raw.get("cvss") or {}
        cvss_score = cvss.get("score")
        cvss_vector = cvss.get("vector_string")

        # 严重度
        severity = raw.get("severity", "").upper() or None

        # 受影响包与版本范围
        affected: list[dict[str, Any]] = []
        is_ai = False
        for pkg in raw.get("vulnerabilities", []) or []:
            name = (pkg.get("package") or {}).get("name", "")
            ecosystem = (pkg.get("package") or {}).get("ecosystem", "")
            # ecosystem 规范化
            eco_map = {"pip": "PyPI", "npm": "npm", "go": "Go", "maven": "Maven",
                       "nuget": "NuGet", "rubygems": "RubyGems", "cargo": "crates.io",
                       "composer": "Packagist", "pypi": "PyPI"}
            eco = eco_map.get(ecosystem, ecosystem)
            if name.lower() in AI_AI_ECOSYSTEM or "huggingface" in eco.lower():
                is_ai = True
            affected.append(
                {
                    "product": name,
                    "ecosystem": eco,
                    "version_constraint": pkg.get("vulnerable_version_range", ""),
                    "patched_version": pkg.get("first_patched_version"),
                }
            )

        # 分类信息
        cwes = [c.get("cwe_id") for c in (raw.get("cwes") or []) if c.get("cwe_id")]

        # 引用链接
        references = [raw["html_url"]] if raw.get("html_url") else []
        for ref in raw.get("references", []) or []:
            if ref:
                references.append(ref)

        return [
            RawItem(
                dedup_key=ghsa_id,
                origin_source=self.code,
                external_id=ghsa_id,
                title=summary or f"{ghsa_id} 安全公告",
                description=description,
                source_name=raw.get("source_code_location") or "GitHub",
                source_url=raw.get("html_url"),
                cvss_score=cvss_score,
                cvss_vector=cvss_vector,
                cvss_version="3.1" if cvss_score else None,
                severity=severity,
                published_at=self._parse_iso(raw.get("published_at")),
                modified_at=self._parse_iso(raw.get("updated_at")),
                affected_assets=affected,
                references=references,
                extra={
                    "ghsa_id": ghsa_id,
                    "cve_id": raw.get("cve_id"),
                    "cwe_ids": cwes,
                    "is_ai_component": is_ai,
                    "package_ecosystem": affected[0]["ecosystem"] if affected else None,
                },
            )
        ]
