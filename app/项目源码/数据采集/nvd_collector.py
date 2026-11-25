"""
NVD CVE 2.0 数据源

全球权威漏洞库，赛题明确要求覆盖的数据源。
采用游标增量拉取，配合 6 小时调度满足"监测延迟 ≤6h"的优秀档要求。

注意：NVD 官方限流为 5 次/30 秒（无 Key），配置 apiKey 后提升至 50 次/30 秒。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from .base_collector import BaseCollector, RawItem

API_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"


async def _throttle() -> None:
    """官方限流保护：每次翻页后等待 6 秒"""
    await asyncio.sleep(6)


class NVDCollector(BaseCollector):
    """NVD 官方 CVE 数据源"""

    code = "nvd"
    name = "NVD CVE 2.0"
    category = "漏洞库"
    homepage = "https://nvd.nist.gov/"
    default_interval = 7200  # 2 小时（NVD API 限流 5req/30s，增量请求少，2h 稳妥）
    # 已配置 NVD_API_KEY（50 次/30 秒），且增量轮次只发数次请求，
    # 因此 30 分钟是安全下限；未配 Key 时不应快于 1 小时。
    # 注意：再快也压不动「发布→可查」那几小时的上游滞后，收益有限。
    min_interval = 1800
    max_interval = 43200
    interval_basis = "NVD API 无 Key 5 次/30 秒、有 Key 50 次/30 秒（本项目已配）；增量请求仅数次"
    ai_native = False  # 需 AI 分类器过滤

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        拉取增量 CVE

        NVD 支持 pubStartDate/pubEndDate 区间查询，
        单次上限 2000 条，超过则分页。
        """
        client = await self.get_client()
        all_items: list[Any] = []

        if cursor:
            start = cursor
            # 断档补偿同样适用于「有游标」的情况。
            # 游标可能因上次采集**部分失败**而滞后于真实缺口，
            # 此时若只从游标往后拉，中间遗漏的数据永远补不回来。
            # 做法：断档超过阈值时，用「现在 - 回溯窗口」与游标取较早者。
            if self.gap_lookback_hours:
                floor = self.cursor_from_time(self.lookback_from(hours=168))
                if floor < start:
                    self.log.info(
                        "nvd_lookback_expanded",
                        cursor=start,
                        expanded_to=floor,
                    )
                    start = floor
        else:
            # 首次运行回溯 7 天，保证初始数据量
            start = self.cursor_from_time(self.lookback_from(hours=168))

        end = self.cursor_from_time(datetime.now(UTC))
        start_index = 0
        page_size = 2000

        while True:
            params: dict[str, Any] = {
                "pubStartDate": start,
                "pubEndDate": end,
                "resultsPerPage": page_size,
                "startIndex": start_index,
            }
            resp = await client.get(API_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()

            vulns = data.get("vulnerabilities", [])
            all_items.extend(vulns)
            self.log.info(
                "nvd_page_fetched",
                page=len(vulns),
                total=data.get("totalResults"),
            )

            if len(vulns) < page_size or start_index + page_size >= data.get("totalResults", 0):
                break
            start_index += page_size
            # 避免触发官方限流
            await _throttle()
        return all_items

    def normalize(self, raw: Any) -> list[RawItem]:
        """NVD 报文归一化"""
        cve = raw.get("cve", {})
        cve_id = cve.get("id")
        if not cve_id:
            return []

        # 描述：优先取英文
        description = ""
        for desc in cve.get("descriptions", []):
            if desc.get("lang") == "en":
                description = desc.get("value", "")
                break
        if not description and cve.get("descriptions"):
            description = cve["descriptions"][0].get("value", "")

        # CVSS 评分：CVSS3.1 优先，其次 3.0、2.0
        cvss_score = cvss_vector = cvss_version = severity = None
        metrics = cve.get("metrics", {})
        for key, version in (("cvssMetricV31", "3.1"), ("cvssMetricV30", "3.0"), ("cvssMetricV2", "2.0")):
            entries = metrics.get(key, [])
            if entries:
                data = entries[0].get("cvssData", {})
                cvss_score = data.get("baseScore")
                cvss_vector = data.get("vectorString")
                cvss_version = version
                break
        # 严重度文本
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            entries = metrics.get(key, [])
            if entries:
                sev = entries[0].get("baseSeverity") or entries[0].get("cvssData", {}).get("baseSeverity")
                if sev:
                    severity = str(sev).upper()
                    break

        # 受影响资产与 CPE
        affected: list[dict[str, Any]] = []
        for conf in cve.get("configurations", []):
            for node in conf.get("nodes", []):
                for cpe_match in node.get("cpeMatch", []):
                    if not cpe_match.get("vulnerable", False):
                        continue
                    criteria = cpe_match.get("criteria", "")
                    parts = criteria.split(":")
                    affected.append(
                        {
                            "cpe": criteria,
                            "vendor": parts[3] if len(parts) > 3 else None,
                            "product": parts[4] if len(parts) > 4 else None,
                            "version_start": (
                                cpe_match.get("versionStartIncluding")
                                or cpe_match.get("versionStartExcluding")
                            ),
                            "version_end": (
                                cpe_match.get("versionEndIncluding")
                                or cpe_match.get("versionEndExcluding")
                            ),
                        }
                    )

        references = [r.get("url", "") for r in cve.get("references", []) if r.get("url")]

        return [
            RawItem(
                dedup_key=cve_id,
                origin_source=self.code,
                external_id=cve_id,
                title=f"{cve_id}: {description[:180]}",
                description=description,
                source_name=cve.get("sourceIdentifier", "NVD"),
                source_url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                cvss_score=cvss_score,
                cvss_vector=cvss_vector,
                cvss_version=cvss_version,
                severity=severity,
                published_at=self._parse_iso(cve.get("published")),
                modified_at=self._parse_iso(cve.get("lastModified")),
                affected_assets=affected,
                references=references,
                extra={
                    "vuln_status": cve.get("vulnStatus"),
                    "cwe_ids": [
                        d.get("value")
                        for w in cve.get("weaknesses", [])
                        for d in w.get("description", [])
                        if d.get("value", "").startswith("CWE")
                    ],
                },
            )
        ]
