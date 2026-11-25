"""
POC 验证代码采集器

POC（Proof of Concept）是赛题明确要求的富化维度三，用于评估漏洞的可利用性。

数据源策略（实测结论）：
- Exploit-DB 已改为纯前端 DataTables 渲染，服务端无接口、
  搜索结果页 HTML 中不含数据行，HTML 解析方案已失效。
- GitHub 搜索 API 可用，且能精准命中 AI 安全主题的 POC 仓库
  （实测 "prompt injection poc" 命中 84 个仓库，含 RAG 投毒等高质量项目）。

因此以 GitHub 搜索为主源，Exploit-DB 保留为可选补充（需站点恢复服务端渲染）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from .base_collector import BaseCollector, RawItem

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"

# AI 安全 POC 检索式——覆盖赛题要求四大分类的典型攻击场景
POC_QUERIES = [
    "prompt injection poc",
    "jailbreak llm",
    "rag poisoning",
    "model backdoor attack",
    "data poisoning machine learning",
    "adversarial attack llm",
    "agent hijacking attack",
    "mcp security vulnerability",
    "model extraction attack",
    "membership inference attack",
]


class POCCollector(BaseCollector):
    """POC 验证代码采集器（GitHub 搜索为主源）"""

    code = "poc_repo"
    name = "POC 验证代码库"
    category = "社区"
    homepage = "https://github.com/search?q=prompt+injection+poc"
    default_interval = 43200  # 12 小时
    # GitHub 代码搜索：未认证 10 次/分钟、认证（已配 GITHUB_TOKEN）30 次/分钟。
    # 单轮要跑多组关键词搜索，6 小时是稳妥下限；POC 出现的节奏本身也偏慢。
    min_interval = 21600
    max_interval = 86400
    interval_basis = "GitHub 代码搜索未认证 10 次/分钟、认证 30 次/分钟（本项目已配 Token）"
    ai_native = True

    # created:>= 回溯窗口（天）。GitHub 仓库搜索不支持按创建时间排序，
    # 旧实现 per_page=10 + sort=updated：仓库创建后无新动态即掉出前 10
    # （实测 45.8h 才入库 = 23h 等回合 + 22h 查询漏抓）。
    # 现改为 created:>=90 天 + per_page 100 + 翻页：结果集被窗口限定在
    # 新仓库内，配合翻页可全量拿到（实测 90 天窗口最大单组 471 条，
    # 6 页上限覆盖且有余量；GitHub 搜索 API 仅开放前 1000 条）。
    CREATED_WINDOW_DAYS = 90
    PER_PAGE = 100
    MAX_PAGES = 6

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        检索 AI 安全 POC 仓库

        策略：q 追加 created:>= 窗口限定 + per_page 100 + 翻页（≤6 页/组）。
        排序仍用 updated（GitHub 仓库搜索无 created 排序），但 created:>=
        把竞争范围限定在窗口内的新仓库，翻页保证全量——近 7 天创建的仓库
        不可能再被「最近有动态的老仓库」挤出结果集。
        """
        client = await self.get_client()
        headers = {"Accept": "application/vnd.github+json"}
        token = getattr(self._settings, "github_token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        since = (
            datetime.now(UTC) - timedelta(days=self.CREATED_WINDOW_DAYS)
        ).strftime("%Y-%m-%d")

        results: list[Any] = []
        seen: set[str] = set()
        requests = 0

        for query in POC_QUERIES:
            for page in range(1, self.MAX_PAGES + 1):
                params = {
                    "q": f"{query} created:>={since}",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": self.PER_PAGE,
                    "page": page,
                }
                try:
                    resp = await client.get(GITHUB_SEARCH_API, params=params, headers=headers)
                    requests += 1
                    if resp.status_code == 403:
                        # 触发限流，等待后放弃该组剩余翻页（下一组仍会重试）
                        self.log.warning("poc_rate_limited", query=query, page=page)
                        await asyncio.sleep(20)
                        break
                    if resp.status_code != 200:
                        self.log.debug("poc_status", query=query, status=resp.status_code)
                        break

                    items = resp.json().get("items", [])
                    for repo in items:
                        full_name = repo.get("full_name", "")
                        if not full_name or full_name in seen:
                            continue
                        seen.add(full_name)
                        repo["_query"] = query
                        results.append(repo)

                    if len(items) < self.PER_PAGE:
                        break  # 没有更多页
                except Exception as exc:  # noqa: BLE001, PERF203
                    self.log.debug(
                        "poc_query_failed", query=query, page=page, error=str(exc)[:120]
                    )
                    break
                # 认证 30 次/分钟，保守节流（翻页后请求数变多，间隔不能省）
                await asyncio.sleep(2.5)

        self.log.info("poc_fetched", count=len(results), since=since, requests=requests)
        return results

    def normalize(self, raw: Any) -> list[RawItem]:
        """GitHub 仓库归一化为 POC 条目"""
        repo_name = raw.get("full_name", "")
        if not repo_name:
            return []

        desc = raw.get("description") or ""
        topics = raw.get("topics", []) or []
        stars = raw.get("stargazers_count", 0)
        query = raw.get("_query", "")

        # 可用性评估：星标数、是否含 PoC/exploit 关键词、更新时间
        keywords = f"{desc} {' '.join(topics)} {repo_name}".lower()
        quality_signals = sum(
            1 for k in ("poc", "exploit", "cve", "vuln", "attack", "proof") if k in keywords
        )
        availability = "high" if stars >= 50 or quality_signals >= 2 else "medium"

        return [
            RawItem(
                dedup_key=f"poc:{repo_name}",
                origin_source=self.code,
                external_id=repo_name,
                title=f"{repo_name}：{desc[:120]}" if desc else repo_name,
                description=(
                    f"{desc}\n\n"
                    f"星标数：{stars} | 语言：{raw.get('language') or '未指定'} | "
                    f"更新时间：{raw.get('updated_at', '')[:10]}"
                ),
                source_name="GitHub",
                source_url=raw.get("html_url"),
                published_at=self._parse_iso(raw.get("created_at")),
                modified_at=self._parse_iso(raw.get("updated_at")),
                extra={
                    "is_poc": True,
                    "repo_name": repo_name,
                    "stars": stars,
                    "language": raw.get("language"),
                    "topics": topics,
                    "query": query,
                    "availability": availability,
                    "license": (raw.get("license") or {}).get("spdx_id"),
                    "archived": raw.get("archived", False),
                },
            )
        ]


# ========== Exploit-DB 兼容保留 ==========


class ExploitDBCollector(BaseCollector):
    """
    Exploit-DB 采集器（需站点恢复服务端渲染，当前降级为空）

    保留此实现是为了兼容站点改版回滚的情况；
    当前 fetch 恒返回空列表，不影响主流程。
    """

    code = "exploitdb"
    name = "Exploit-DB POC 平台"
    category = "社区"
    homepage = "https://www.exploit-db.com"
    default_interval = 43200
    min_interval = 21600
    max_interval = 86400
    interval_basis = "Exploit-DB 为静态归档页，按 6 小时轮询足够"
    ai_native = True

    async def fetch(self, cursor: str | None) -> list[Any]:
        """
        尝试解析 Exploit-DB

        站点已于近期改为纯客户端 DataTables 渲染，服务端 HTML 不含数据行。
        此处保留探测逻辑：若未来恢复服务端渲染即可自动工作。
        """
        client = await self.get_client()
        try:
            resp = await client.get(
                "https://www.exploit-db.com/search?type=exploit_all&tta=llm"
            )
            if resp.status_code != 200:
                return []
            # 仅当页面含数据表时才解析
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", class_="table-striped")
            if table and len(table.find_all("tr")) > 1:
                self.log.info("exploitdb_parsed", rows=len(table.find_all("tr")))
                return [resp.text]
        except Exception as exc:  # noqa: BLE001
            self.log.debug("exploitdb_unavailable", error=str(exc)[:120])

        self.log.info(
            "exploitdb_degraded",
            reason="站点已改为客户端渲染，服务端无数据，改用 GitHub POC 源",
        )
        return []

    def normalize(self, raw: str) -> list[RawItem]:
        """解析 Exploit-DB 搜索结果表格"""
        from bs4 import BeautifulSoup

        items: list[RawItem] = []
        soup = BeautifulSoup(raw, "lxml")
        table = soup.find("table", class_="table-striped")
        if not table:
            return items

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            link = tds[0].find("a")
            if not link:
                continue
            edb_id = link.get_text(strip=True)
            title = tds[1].get_text(strip=True) if len(tds) > 1 else ""
            if not edb_id or not title:
                continue
            items.append(
                RawItem(
                    dedup_key=f"exploitdb:{edb_id}",
                    origin_source=self.code,
                    external_id=edb_id,
                    title=f"Exploit-DB {edb_id}: {title}",
                    description=f"Exploit-DB 收录的 POC 验证代码：{title}",
                    source_name="Exploit-DB",
                    source_url=f"https://www.exploit-db.com/exploits/{edb_id}",
                    extra={"is_poc": True, "edb_id": edb_id, "availability": "high"},
                )
            )
        return items
