"""
CISA KEV 已知在野利用漏洞目录

CISA 维护的"已被实际利用"漏洞清单，是判断漏洞真实威胁程度的关键情报源。
命中 KEV 意味着该漏洞已被攻击者实际使用，业务风险等级应显著上调。

这部分数据支撑系统的"风险定级"能力与情报价值评估。
"""

from __future__ import annotations

from typing import Any

from .base_collector import BaseCollector, RawItem

KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
)


class CISAKEVCollector(BaseCollector):
    """CISA KEV 已知在野利用漏洞"""

    code = "cisa_kev"
    name = "CISA KEV 在野利用目录"
    category = "漏洞库"
    homepage = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
    default_interval = 3600  # 1 小时（KEV=在野利用信号，时效价值最高；静态小 JSON，加频零成本）
    # KEV 单文件仅数百 KB，加频的边际成本几乎为零，且「已遭在野利用」
    # 是时效价值最高的一类情报 —— 值得比 CVE 库采得更勤。
    min_interval = 900
    max_interval = 21600
    interval_basis = "在野利用信号时效价值最高；静态小 JSON 无配额限制，15 分钟成本极低"
    ai_native = False

    async def fetch(self, cursor: str | None) -> list[Any]:
        """全量拉取 KEV 清单（数据量小，约 1400 条）"""
        client = await self.get_client()
        resp = await client.get(KEV_URL)
        resp.raise_for_status()
        data = resp.json()
        vulns = data.get("vulnerabilities", [])
        self.log.info("cisa_kev_fetched", count=len(vulns))
        return vulns

    def normalize(self, raw: Any) -> list[RawItem]:
        """KEV 报文归一化"""
        cve_id = raw.get("cveID", "")
        if not cve_id:
            return []

        return [
            RawItem(
                dedup_key=cve_id,
                origin_source=self.code,
                external_id=cve_id,
                title=f"{cve_id} 已被在野利用",
                description=(
                    f"{raw.get('vulnerabilityName', '')}\n\n"
                    f"该漏洞已被确认在真实攻击中使用。"
                    f"处置要求：{raw.get('requiredAction', '')}"
                ),
                source_name="CISA KEV",
                source_url="https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
                cvss_score=None,  # KEV 目录不含 CVSS
                cvss_vector=None,
                severity="CRITICAL" if raw.get("knownRansomwareCampaignUse") == "Known" else "HIGH",
                published_at=self._parse_iso(raw.get("dateAdded")),
                modified_at=self._parse_iso(raw.get("dateAdded")),
                affected_assets=[
                    {
                        "vendor": raw.get("vendorProject", "").split(":")[0] if raw.get("vendorProject") else None,
                        "product": raw.get("product", "").split(":")[0] if raw.get("product") else None,
                    }
                ],
                references=[f"https://nvd.nist.gov/vuln/detail/{cve_id}"],
                extra={
                    "is_kev": True,
                    "known_ransomware": raw.get("knownRansomwareCampaignUse", "Unknown"),
                    "required_action": raw.get("requiredAction", ""),
                    "due_date": raw.get("dueDate"),
                    "vendor_project": raw.get("vendorProject"),
                    "product": raw.get("product"),
                },
            )
        ]
