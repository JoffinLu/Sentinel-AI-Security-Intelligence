"""
OSV.dev 开源漏洞库

Google 维护的开源漏洞聚合平台，覆盖 PyPI、npm、Go、Maven 等生态，
对 AI 生态（transformers、torch 等）覆盖度优于 NVD。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .base_collector import BaseCollector, RawItem

API_BASE = "https://api.osv.dev/v1"

# 重点关注的 AI 生态包——用于定向查询，提升采集效率
AI_AI_PACKAGES = [
    ("transformers", "PyPI"), ("torch", "PyPI"), ("torchvision", "PyPI"),
    ("langchain", "PyPI"), ("langchain-core", "PyPI"), ("llama-index", "PyPI"),
    ("vllm", "PyPI"), ("sglang", "PyPI"), ("accelerate", "PyPI"),
    ("diffusers", "PyPI"), ("datasets", "PyPI"), ("safetensors", "PyPI"),
    ("tokenizers", "PyPI"), ("huggingface-hub", "PyPI"), ("sentence-transformers", "PyPI"),
    ("openai", "PyPI"), ("anthropic", "PyPI"), ("haystack-ai", "PyPI"),
    ("chromadb", "PyPI"), ("qdrant-client", "PyPI"), ("faiss-cpu", "PyPI"),
    ("onnxruntime", "PyPI"), ("mlflow", "PyPI"), ("ray", "PyPI"),
    ("optuna", "PyPI"), ("gradio", "PyPI"), ("streamlit", "PyPI"),
    ("timm", "PyPI"), ("peft", "PyPI"), ("trl", "PyPI"),
    ("huggingface", "npm"), ("@modelcontextprotocol/sdk", "npm"),
    ("ollama", "npm"), ("@langchain/core", "npm"),
]


class OSVCollector(BaseCollector):
    """OSV.dev 开源漏洞数据源"""

    code = "osv"
    name = "OSV.dev 开源漏洞库"
    category = "漏洞库"
    homepage = "https://osv.dev/"
    default_interval = 43200  # 12 小时
    # 单轮采集实测约 115 秒，且要按包名逐个查询 OSV 接口
    # （查询列表覆盖 20+ AI 生态包），1 小时已是稳妥下限。
    # 开源漏洞的时效价值低于在野利用与厂商公告，故默认仍取 12 小时。
    min_interval = 3600
    max_interval = 43200
    interval_basis = "单轮采集约 115 秒且需按包逐个查询；开源漏洞时效价值低于在野利用"
    ai_native = True

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        定向查询 AI 生态包的已知漏洞

        OSV 的 query 接口按包查询，天然聚焦于 AI 生态，
        相比遍历全库更高效。
        """
        client = await self.get_client()
        results: list[Any] = []

        for pkg_name, ecosystem in AI_AI_PACKAGES:
            payload = {
                "package": {"name": pkg_name, "ecosystem": ecosystem},
                "vulns": [],  # 返回该包全部历史漏洞
            }
            try:
                resp = await client.post(f"{API_BASE}/query", json=payload)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                vulns = data.get("vulns", [])
                for v in vulns:
                    v["_query_package"] = pkg_name
                    v["_query_ecosystem"] = ecosystem
                results.extend(vulns)
            except Exception as exc:  # noqa: BLE001, PERF203
                self.log.debug("osv_pkg_failed", pkg=pkg_name, error=str(exc)[:100])
            # OSV 建议请求间隔，避免限流
            await asyncio.sleep(0.4)

        return results

    def normalize(self, raw: Any) -> list[RawItem]:
        """OSV 报文归一化"""
        osv_id = raw.get("id", "")
        if not osv_id:
            return []

        # 关联 CVE
        aliases = raw.get("aliases", [])
        cve_id = next(
            (a for a in aliases if a.startswith("CVE-")), osv_id
        )

        summary = raw.get("summary", "")
        details = raw.get("details", "")

        # CVSS
        cvss_score = None
        cvss_vector = None
        severity = None
        for sev in raw.get("severity", []) or []:
            score = sev.get("score", "")
            if score.startswith("CVSS:"):
                cvss_vector = score
                cvss_score = _parse_cvss_base(score)
                break

        # 严重度（OSV 存在多种字段位置）
        severity = None
        db_specific = raw.get("database_specific")
        if isinstance(db_specific, dict):
            severity = db_specific.get("severity")

        # 受影响包与版本范围
        affected: list[dict[str, Any]] = []
        for aff in raw.get("affected", []) or []:
            pkg = aff.get("package", {})
            for rng in aff.get("ranges", []) or []:
                for event in rng.get("events", []) or []:
                    affected.append(
                        {
                            "product": pkg.get("name"),
                            "ecosystem": pkg.get("ecosystem"),
                            "version_constraint": " / ".join(
                                f"{k}={v}" for k, v in event.items()
                            ),
                        }
                    )
            if not aff.get("ranges"):
                affected.append(
                    {
                        "product": pkg.get("name"),
                        "ecosystem": pkg.get("ecosystem"),
                        "version_constraint": "all",
                    }
                )

        references = [r.get("url") for r in raw.get("references", []) or [] if r.get("url")]

        return [
            RawItem(
                dedup_key=cve_id if cve_id.startswith("CVE-") else osv_id,
                origin_source=self.code,
                external_id=osv_id,
                title=summary or f"{osv_id} 漏洞",
                description=details or summary,
                source_name="OSV.dev",
                source_url=f"https://osv.dev/vulnerability/{osv_id}",
                cvss_score=cvss_score,
                cvss_vector=cvss_vector,
                severity=severity,
                published_at=self._parse_iso(raw.get("published")),
                modified_at=self._parse_iso(raw.get("modified")),
                affected_assets=affected,
                references=references,
                extra={
                    "osv_id": osv_id,
                    "aliases": aliases,
                    "is_ai_component": True,
                    "cwe_ids": _extract_cwes(raw),
                },
            )
        ]


def _extract_cwes(raw: dict[str, Any]) -> list[str]:
    """
    提取 CWE 编号

    OSV 的 CWE 信息分散在 database_specific 与 affected 两处，
    这里做兼容提取。
    """
    cwes: list[str] = []

    db_specific = raw.get("database_specific") or {}
    if isinstance(db_specific, dict):
        raw_cwes = db_specific.get("cwe_ids") or db_specific.get("cwes") or []
        for c in raw_cwes:
            if isinstance(c, str) and c.upper().startswith("CWE-"):
                cwes.append(c.upper())

    for aff in raw.get("affected") or []:
        adb = aff.get("database_specific")
        if isinstance(adb, dict):
            for c in adb.get("cwe_ids") or []:
                if isinstance(c, str) and c.upper().startswith("CWE-"):
                    cwes.append(c.upper())

    return sorted(set(cwes))


def _parse_cvss_base(vector: str) -> float | None:
    """从 CVSS 向量解析基础分（具体计算交由富化模块的 CVSS 解析器处理）"""
    return None
