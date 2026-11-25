"""
MITRE ATLAS 攻击技术知识库

ATLAS 是专门针对 AI 系统的攻击技术知识库，与传统 ATT&CK 互补，
是本系统"攻击技基线"的数据来源，支撑两处能力：

1. 富化维度六：漏洞描述 → ATLAS 技术映射
2. 问答模块：攻击链多跳推理的技术节点来源

数据源说明：ATLAS 官方 ATLAS.yaml 采用自定义矩阵格式
（matrices[].tactics[] / techniques[]），并非 STIX bundles，
故解析逻辑不能照搬 STIX 结构。16 个战术矩阵，技术编号形如 AML.T0000。
"""

from __future__ import annotations

from typing import Any

import yaml

from .base_collector import BaseCollector, RawItem

# ATLAS 官方数据源（含版本化镜像）
ATLAS_URLS = [
    (
        "atlas_data",
        "https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/ATLAS.yaml",
    ),
    (
        "atlas_latest",
        "https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/ATLAS-latest.yaml",
    ),
]


class MITREAtlasCollector(BaseCollector):
    """MITRE ATLAS AI 攻击技术库"""

    code = "atlas"
    name = "MITRE ATLAS 攻击技术库"
    category = "标准"
    homepage = "https://atlas.mitre.org/"
    default_interval = 604800  # 7 天，标准数据变动极少
    # ATLAS 是标准库，一次全量同步可覆盖长期使用；
    # 设 1 天下限只是为避免被误设成「一个月」而彻底失效。
    min_interval = 86400
    max_interval = 604800
    interval_basis = "MITRE ATLAS 标准库极少变动，7 天足够；下限 1 天仅作防误设保护"
    ai_native = True

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        拉取 ATLAS 矩阵数据

        解析为扁平的技术条目列表，含子技术与战术归属。
        """
        client = await self.get_client()

        for code, url in ATLAS_URLS:
            try:
                resp = await client.get(url, timeout=60.0)
                if resp.status_code != 200:
                    continue
                parsed = self._parse_matrix(resp.text)
                if parsed:
                    self.log.info("atlas_fetched", source=code, count=len(parsed))
                    return parsed
            except Exception as exc:  # noqa: BLE001, PERF203
                self.log.debug("atlas_source_failed", source=code, error=str(exc)[:150])

        # 全部镜像不可用时返回空，系统降级但不影响其他源
        self.log.warning("atlas_all_sources_unavailable")
        return []

    def _parse_matrix(self, text: str) -> list[dict[str, Any]]:
        """
        解析 ATLAS 矩阵结构

        实际结构：
            matrices[0].tactics[]     战术列表
            matrices[0].techniques[]  技术列表（id 形如 AML.T0000）
        """
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return []

        results: list[dict[str, Any]] = []
        for matrix in data.get("matrices", []) or []:
            matrix_name = matrix.get("name", "ATLAS")
            for tech in matrix.get("techniques", []) or []:
                tid = tech.get("id")
                if not tid:
                    continue

                # 提取子技术（可能是 dict 或裸字符串两种形态）
                subs: list[str] = []
                for s in tech.get("subtechniques") or []:
                    if isinstance(s, dict) and s.get("id"):
                        subs.append(s["id"])
                    elif isinstance(s, str):
                        subs.append(s)

                tactics: list[str] = []
                for t in tech.get("tactics") or []:
                    if isinstance(t, dict):
                        name = t.get("shortname") or t.get("name")
                        if name:
                            tactics.append(name)
                    else:
                        tactics.append(str(t))

                attack_refs = [
                    r.get("url")
                    for r in (tech.get("ATT&CK-reference") or [])
                    if isinstance(r, dict) and r.get("url")
                ]

                results.append(
                    {
                        "id": tid,
                        "name": tech.get("name", ""),
                        "description": tech.get("description", ""),
                        "tactics": tactics,
                        "matrix": matrix_name,
                        "sub_techniques": subs,
                        "platforms": tech.get("platforms", []),
                        "maturity": tech.get("maturity"),
                        "attack_refs": attack_refs,
                    }
                )

        # 案例研究同样纳入，作为真实攻击手法参考
        for case in data.get("case-studies", []) or []:
            cid = case.get("id")
            if not cid:
                continue
            results.append(
                {
                    "id": cid,
                    "name": case.get("name", ""),
                    "description": case.get("summary", ""),
                    "tactics": [],
                    "matrix": "Case Study",
                    "sub_techniques": [],
                    "platforms": [],
                    "is_case_study": True,
                    "attack_refs": [
                        r.get("url")
                        for r in (case.get("references") or [])
                        if isinstance(r, dict) and r.get("url")
                    ],
                }
            )

        return results

    def normalize(self, raw: Any) -> list[RawItem]:
        """ATLAS 技术对象归一化"""
        if not isinstance(raw, dict):
            return []

        atlas_id = raw.get("id", "")
        name = raw.get("name", "")
        if not atlas_id or not name:
            return []

        tactics = raw.get("tactics") or []
        return [
            RawItem(
                dedup_key=f"atlas:{atlas_id}",
                origin_source=self.code,
                external_id=atlas_id,
                title=f"{atlas_id} {name}",
                description=raw.get("description", ""),
                source_name="MITRE ATLAS",
                source_url=f"https://atlas.mitre.org/techniques/{atlas_id}",
                extra={
                    "is_technique": True,
                    "atlas_id": atlas_id,
                    "atlas_name": name,
                    "tactic": tactics[0] if tactics else None,
                    "tactics": tactics,
                    "matrix": raw.get("matrix"),
                    "platform": (raw.get("platforms") or [None])[0],
                    "sub_techniques": raw.get("sub_techniques", []),
                    "maturity": raw.get("maturity"),
                    "attack_refs": raw.get("attack_refs", []),
                    "is_case_study": raw.get("is_case_study", False),
                },
            )
        ]
