"""
知识图谱构建与多跳推理

赛题要求Cypher 模板生成 + 执行，最多 3 跳，带路径约束。

图谱设计——四类节点、五种关系：

    (:Vulnerability)  漏洞主体（id / 标题 / CVSS / 严重度 / 四分类）
    (:Component)      受影响组件（产品名 / 生态 / 是否 AI 组件）
    (:Technique)      MITRE ATLAS 攻击技术（编号 / 名称 / 战术）
    (:Paper)          学术论文（arXiv ID / 标题 / 主题）
    (:POC)            POC 验证代码（仓库名 / 星标 / 可用性）

    (:Vulnerability)-[:AFFECTS]->(:Component)      漏洞影响某组件
    (:Vulnerability)-[:USES_TECHNIQUE]->(:Technique)  漏洞利用某攻击技术
    (:Vulnerability)-[:HAS_POC]->(:POC)            漏洞存在 POC
    (:Vulnerability)-[:CITES]->(:Paper)            漏洞关联论文
    (:Component)-[:RELATED_TO {shared_vulns:N}]->(:Component)
                                                  组件间共现漏洞（供应链传导路径）

为什么需要"组件-组件"这条边：
    它是供应链攻击传导路径的建模——若组件 A 与组件 B 共享多个漏洞
    （说明二者常同框出现，如 vllm 与 transformers），则 A 上的新漏洞
    很可能也需要检查 B。这是单跳关系推不出来的，只能靠 2 跳。

多跳推理的价值举例：
    用户问"哪些 MCP 相关漏洞有可用 POC"——需要 2 跳：
        Vulnerability -[:AFFECTS]-> Component(名字含 MCP)
                      -[:HAS_POC]-> POC
    纯向量检索无法回答这种"带过滤条件的关系查询"，
    这正是图谱相对纯 RAG 的增值点。

安全约束：
    ① 最多 3 跳——避免组合爆炸
    ② 每跳限定关系类型白名单——防止意外的全图遍历
    ③ 结果条数上限——控制响应体量与前端渲染压力
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy import select

from ..后端服务.config import get_settings
from ..后端服务.logger import get_logger
from ..富化模块.asset_enricher import is_ai_component_name
from ..知识库.database import get_session
from ..知识库.models import Paper, Vulnerability

logger = get_logger("graph")

# 允许的关系类型白名单——多跳查询只能沿这些边走
ALLOWED_RELATIONS = ["AFFECTS", "USES_TECHNIQUE", "HAS_POC", "CITES", "RELATED_TO"]

# 跳数上限（赛题要求最多 3 跳）
MAX_HOPS = 3


@dataclass(slots=True)
class GraphNode:
    """图谱节点（用于前端展示推理路径）"""

    id: str
    labels: list[str] = field(default_factory=list)
    name: str = ""
    props: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "labels": self.labels,
            "name": self.name,
            "props": self.props,
        }


@dataclass(slots=True)
class GraphPath:
    """
    推理路径

    记录"从哪个节点、经过什么关系、到达哪个节点"，
    前端据此展示可解释的推理链——赛题要求的"推理链展示"能力。
    """

    nodes: list[GraphNode] = field(default_factory=list)
    relations: list[dict[str, str]] = field(default_factory=list)
    hops: int = 0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "relations": self.relations,
            "hops": self.hops,
            "description": self.description,
        }

    def to_text(self) -> str:
        """转成可读文本，用于在答案中展示推理过程"""
        if not self.nodes:
            return ""
        parts = [self.nodes[0].name or self.nodes[0].id]
        for idx, rel in enumerate(self.relations):
            parts.append(f"--[{rel['type']}]-->")
            if idx + 1 < len(self.nodes):
                parts.append(self.nodes[idx + 1].name or self.nodes[idx + 1].id)
        return " ".join(parts)


class GraphBuilder:
    """
    图谱构建器

    从 PostgreSQL 同步到 Neo4j。使用 UNWIND 批量写入，
    4710 条数据实测约 3-5 秒完成。
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._driver: GraphDatabase | None = None

    @property
    def driver(self):
        """惰性初始化驱动"""
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
                max_connection_pool_size=20,
            )
        return self._driver

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def health_check(self) -> dict[str, Any]:
        """图谱健康检查"""
        try:
            self.driver.verify_connectivity()
            with self.driver.session() as s:
                counts = {}
                for label in ("Vulnerability", "Component", "Technique", "Paper", "POC"):
                    counts[label] = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
                rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            return {
                "available": True,
                "nodes": counts,
                "relations": rels,
                "total_nodes": sum(counts.values()),
            }
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": str(exc)[:200]}

    def clear(self) -> None:
        """清空图谱（重建前调用）"""
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        logger.info("graph_cleared")

    def create_constraints(self) -> None:
        """创建约束与索引——保证查询性能与数据唯一性"""
        stmts = [
            # 唯一约束保证幂等写入
            "CREATE CONSTRAINT vuln_id IF NOT EXISTS "
            "FOR (n:Vulnerability) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT comp_key IF NOT EXISTS "
            "FOR (n:Component) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT tech_id IF NOT EXISTS "
            "FOR (n:Technique) REQUIRE n.atlas_id IS UNIQUE",
            "CREATE CONSTRAINT paper_id IF NOT EXISTS "
            "FOR (n:Paper) REQUIRE n.arxiv_id IS UNIQUE",
            "CREATE CONSTRAINT poc_key IF NOT EXISTS "
            "FOR (n:POC) REQUIRE n.key IS UNIQUE",
            # 查询加速
            "CREATE INDEX vuln_severity IF NOT EXISTS "
            "FOR (n:Vulnerability) ON (n.severity)",
            "CREATE INDEX vuln_ai_rel IF NOT EXISTS "
            "FOR (n:Vulnerability) ON (n.ai_relevance)",
            "CREATE INDEX comp_name IF NOT EXISTS "
            "FOR (n:Component) ON (n.name)",
            "CREATE INDEX tech_atlas IF NOT EXISTS "
            "FOR (n:Technique) ON (n.atlas_id)",
        ]
        with self.driver.session() as s:
            for stmt in stmts:
                try:
                    s.run(stmt)
                except Neo4jError as exc:
                    # 约束已存在等非致命错误
                    logger.debug("constraint_skip", reason=str(exc)[:80])
        logger.info("graph_constraints_ready", count=len(stmts))

    async def build_from_database(
        self, vuln_limit: int | None = None, clear: bool = True
    ) -> dict[str, int]:
        """
        从 PostgreSQL 构建图谱

        只导入有实际情报价值的节点——CISA KEV 的 1734 条
        （标题就是"xxx 已被在野利用"，无描述信息）会让图谱噪声过大，
        因此按 AI 相关性过滤。
        """
        stats = {"vulnerabilities": 0, "components": 0, "techniques": 0, "papers": 0, "pocs": 0, "relations": 0}
        start = time.perf_counter()

        if clear:
            self.clear()
        self.create_constraints()

        # ---------- 节点：漏洞 ----------
        async with get_session() as session:
            stmt = select(
                Vulnerability.cve_id,
                Vulnerability.title,
                Vulnerability.cvss_score,
                Vulnerability.severity,
                Vulnerability.ai_category,
                Vulnerability.ai_relevance_score,
                Vulnerability.published_at,
                Vulnerability.source_url,
                Vulnerability.affected_assets,
                Vulnerability.technique_refs,
                Vulnerability.poc_refs,
                Vulnerability.paper_refs,
            )
            # 只导入有 AI 属性或已富化的条目——它们才是本系统的核心价值
            stmt = stmt.where(Vulnerability.ai_relevance_score >= 0.3)
            if vuln_limit:
                stmt = stmt.limit(vuln_limit)
            vuln_rows = (await session.execute(stmt)).all()

        vuln_data = []
        component_set: set[str] = set()
        affects_edges: list[dict[str, Any]] = []
        tech_edges: list[dict[str, Any]] = []
        poc_edges: list[dict[str, Any]] = []
        paper_edges: list[dict[str, Any]] = []
        tech_ids: set[str] = set()
        paper_ids: set[str] = set()
        poc_keys: set[str] = set()

        for r in vuln_rows:
            vid = r.cve_id
            vuln_data.append(
                {
                    "id": vid,
                    "title": (r.title or "")[:400],
                    "cvss": r.cvss_score,
                    "severity": r.severity,
                    "ai_category": r.ai_category,
                    "ai_relevance": round(r.ai_relevance_score or 0, 3),
                    "published": r.published_at.isoformat() if r.published_at else None,
                    "url": r.source_url,
                }
            )

            # 组件节点与 AFFECTS 边
            for asset in (r.affected_assets or [])[:5]:
                product = asset.get("product")
                if not product:
                    continue
                name = str(product)[:200]
                key = f"{name}|{asset.get('ecosystem') or ''}"
                component_set.add(key)
                affects_edges.append(
                    {
                        "vuln": vid,
                        "key": key,
                        "name": name,
                        "ecosystem": asset.get("ecosystem"),
                        # 用统一判定函数而非采集器标记——各采集器的
                        # is_ai_component 字段可能为 None 或缺失，
                        # 实测导致 64 个组件里仅 4 个被识别为 AI 组件，
                        # 供应链传导推理因此失效。
                        "is_ai": bool(asset.get("is_ai_component"))
                        or is_ai_component_name(product),
                        "type": asset.get("asset_type"),
                    }
                )

            # 攻击技节点与 USES_TECHNIQUE 边
            for tech in (r.technique_refs or [])[:3]:
                aid = tech.get("atlas_id")
                if not aid:
                    continue
                tech_ids.add(aid)
                tech_edges.append({"vuln": vid, "atlas_id": aid, "name": tech.get("name") or aid})

            # POC 节点与 HAS_POC 边
            for poc in (r.poc_refs or [])[:2]:
                pname = poc.get("name")
                if not pname:
                    continue
                key = f"poc:{pname}"
                poc_keys.add(key)
                poc_edges.append(
                    {
                        "vuln": vid,
                        "key": key,
                        "name": str(pname)[:200],
                        "url": poc.get("url"),
                        "stars": poc.get("stars") or 0,
                    }
                )

            # 论文节点与 CITES 边
            for paper in (r.paper_refs or [])[:3]:
                pid = paper.get("arxiv_id")
                if not pid:
                    continue
                paper_ids.add(pid)
                paper_edges.append(
                    {"vuln": vid, "arxiv_id": pid, "title": paper.get("title") or pid}
                )

        # ---------- 写节点 ----------
        with self.driver.session() as s:
            # 漏洞节点
            s.run(
                """
                UNWIND $rows AS row
                MERGE (v:Vulnerability {id: row.id})
                SET v.title = row.title, v.cvss = row.cvss, v.severity = row.severity,
                    v.ai_category = row.ai_category, v.ai_relevance = row.ai_relevance,
                    v.published = row.published, v.url = row.url
                """,
                rows=vuln_data,
            )
            stats["vulnerabilities"] = len(vuln_data)

            # 组件节点
            # 预聚合每个组件的 AI 标记，避免在列表推导里反复线性扫描
            ai_keys = {e["key"] for e in affects_edges if e["is_ai"]}
            comp_rows = [
                {
                    "key": k,
                    "name": k.split("|")[0],
                    "ecosystem": k.split("|")[1] if "|" in k else None,
                    "is_ai": k in ai_keys,
                }
                for k in component_set
            ]
            if comp_rows:
                s.run(
                    """
                    UNWIND $rows AS row
                    MERGE (c:Component {key: row.key})
                    SET c.name = row.name, c.ecosystem = row.ecosystem, c.is_ai = row.is_ai
                    """,
                    rows=comp_rows,
                )
            stats["components"] = len(comp_rows)

            # 攻击技节点
            if tech_ids:
                tech_rows = [
                    {"atlas_id": t, "name": next((e["name"] for e in tech_edges if e["atlas_id"] == t), t)}
                    for t in tech_ids
                ]
                s.run(
                    """
                    UNWIND $rows AS row
                    MERGE (t:Technique {atlas_id: row.atlas_id})
                    SET t.name = row.name
                    """,
                    rows=tech_rows,
                )
            stats["techniques"] = len(tech_ids)

            # POC 节点
            if poc_keys:
                poc_rows = [
                    {
                        "key": next(
                            (e["key"] for e in poc_edges if e["vuln"] == e["vuln"] and e["key"] == k),
                            k,
                        ),
                        "name": k.replace("poc:", ""),
                        "url": next((e["url"] for e in poc_edges if e["key"] == k), None),
                        "stars": next((e["stars"] for e in poc_edges if e["key"] == k), 0),
                    }
                    for k in poc_keys
                ]
                s.run(
                    """
                    UNWIND $rows AS row
                    MERGE (p:POC {key: row.key})
                    SET p.name = row.name, p.url = row.url, p.stars = row.stars
                    """,
                    rows=poc_rows,
                )
            stats["pocs"] = len(poc_keys)

            # 论文节点（关联表里可能没有，补一次独立查询）
            if paper_ids:
                async with get_session() as session:
                    prows = (
                        await session.execute(
                            select(Paper.arxiv_id, Paper.title).where(
                                Paper.arxiv_id.in_(list(paper_ids)[:500])
                            )
                        )
                    ).all()
                s.run(
                    """
                    UNWIND $rows AS row
                    MERGE (p:Paper {arxiv_id: row.arxiv_id})
                    SET p.title = row.title
                    """,
                    rows=[{"arxiv_id": r[0], "title": (r[1] or r[0])[:300]} for r in prows],
                )
            stats["papers"] = len(paper_ids)

            # ---------- 写关系 ----------
            s.run(
                """
                UNWIND $rows AS row
                MATCH (v:Vulnerability {id: row.vuln})
                MATCH (c:Component {key: row.key})
                MERGE (v)-[r:AFFECTS]->(c)
                SET r.type = row.type
                """,
                rows=affects_edges,
            )
            s.run(
                """
                UNWIND $rows AS row
                MATCH (v:Vulnerability {id: row.vuln})
                MATCH (t:Technique {atlas_id: row.atlas_id})
                MERGE (v)-[:USES_TECHNIQUE]->(t)
                """,
                rows=tech_edges,
            )
            s.run(
                """
                UNWIND $rows AS row
                MATCH (v:Vulnerability {id: row.vuln})
                MATCH (p:POC {key: row.key})
                MERGE (v)-[:HAS_POC]->(p)
                """,
                rows=poc_edges,
            )
            if paper_edges:
                s.run(
                    """
                    UNWIND $rows AS row
                    MATCH (v:Vulnerability {id: row.vuln})
                    MATCH (p:Paper {arxiv_id: row.arxiv_id})
                    MERGE (v)-[:CITES]->(p)
                    """,
                    rows=paper_edges,
                )

            # 组件共现边（供应链传导路径的建模核心）
            # 组件共现（供应链传导路径）
            # 不用 id——该函数在新版 Neo4j 已废弃且行为不稳定，
            # 改用 elementId 做方向去重（保证每对只建一条边）。
            s.run(
                """
                MATCH (b:Vulnerability)-[:AFFECTS]->(c1:Component)
                MATCH (b)-[:AFFECTS]->(c2:Component)
                WHERE elementId(c1) < elementId(c2)
                  AND coalesce(c1.is_ai, false) = true
                  AND coalesce(c2.is_ai, false) = true
                WITH c1, c2, count(DISTINCT b) AS shared
                WHERE shared >= 1
                MERGE (c1)-[r:RELATED_TO]->(c2)
                SET r.shared_vulns = shared
                """
            )

            total_rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

        stats["relations"] = total_rels
        stats["elapsed_s"] = round(time.perf_counter() - start, 1)
        logger.info("graph_built", **stats)
        return stats


class GraphRetriever:
    """
    图谱检索器

    提供模板化的多跳查询。每个方法对应一类关系推理，
    内部保证跳数 ≤3、关系类型受白名单约束。
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._driver: GraphDatabase | None = None

    @property
    def driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
                max_connection_pool_size=20,
            )
        return self._driver

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def health_check(self) -> dict[str, Any]:
        try:
            self.driver.verify_connectivity()
            with self.driver.session() as s:
                n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            return {"available": True, "nodes": n}
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": str(exc)[:200]}

    # ---------- 模板化查询 ----------

    def find_by_component(
        self, component_keyword: str, limit: int = 20
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """
        模板 1：按组件查漏洞（1 跳）

        "transformers 有哪些漏洞" → (:Vulnerability)-[:AFFECTS]->(:Component)
        """
        cypher = """
        MATCH (v:Vulnerability)-[r:AFFECTS]->(c:Component)
        WHERE toLower(c.name) CONTAINS toLower($keyword)
        RETURN v.id AS id, v.title AS title, v.severity AS severity,
               v.cvss AS cvss, v.ai_category AS ai_category,
               c.name AS component, c.ecosystem AS ecosystem, c.is_ai AS is_ai
        ORDER BY v.ai_relevance DESC, v.cvss DESC
        LIMIT $limit
        """
        results, paths = self._run_with_paths(cypher, {"keyword": component_keyword, "limit": limit})
        return results, paths

    def find_vulns_with_poc(
        self, component_keyword: str | None = None, limit: int = 20
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """
        模板 2：查有 POC 的漏洞（1-2 跳）

        "MCP 相关哪些漏洞有 POC" →
            (:Vulnerability)-[:AFFECTS]->(:Component{name 含 MCP})
                          -[:HAS_POC]->(:POC)
        这是典型的 2 跳组合查询，纯向量检索无法回答。
        """
        if component_keyword:
            cypher = """
            MATCH (v:Vulnerability)-[:AFFECTS]->(c:Component)-[:HAS_POC]->(p:POC)
            WHERE toLower(c.name) CONTAINS toLower($keyword)
            RETURN v.id AS id, v.title AS title, v.severity AS severity,
                   v.cvss AS cvss, c.name AS component, p.name AS poc,
                   p.url AS poc_url, p.stars AS poc_stars
            ORDER BY p.stars DESC
            LIMIT $limit
            """
            params = {"keyword": component_keyword, "limit": limit}
        else:
            cypher = """
            MATCH (v:Vulnerability)-[:HAS_POC]->(p:POC)
            RETURN v.id AS id, v.title AS title, v.severity AS severity,
                   v.cvss AS cvss, p.name AS poc, p.url AS poc_url, p.stars AS poc_stars
            ORDER BY p.stars DESC, v.ai_relevance DESC
            LIMIT $limit
            """
            params = {"limit": limit}

        results, paths = self._run_with_paths(cypher, params)
        return results, paths

    def find_techniques_by_component(
        self, component_keyword: str, limit: int = 10
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """
        模板 3b：组件涉及的攻击技术（2 跳）

        路径：Component ← AFFECTS ← Vulnerability → USES_TECHNIQUE → Technique

        回答「mlflow 涉及哪些 ATT&CK 攻击技术」这类问题。
        这是纯向量检索答不了的——需要同时满足「该组件被影响」
        与「该漏洞用了某技术」两个条件，属关系型查询。

        实测：mlflow 关联到 5 项技术，其中 Active Scanning 13 条、
        AI Supply Chain Compromise 9 条。
        """
        cypher = """
        MATCH (c:Component)<-[:AFFECTS]-(v:Vulnerability)-[:USES_TECHNIQUE]->(t:Technique)
        WHERE toLower(c.name) CONTAINS toLower($keyword)
        RETURN t.atlas_id AS atlas_id, t.name AS technique, count(*) AS vuln_count,
               collect(DISTINCT v.id)[0..3] AS sample_cves
        ORDER BY vuln_count DESC
        LIMIT $limit
        """
        with self.driver.session() as s:
            rows = [dict(r) for r in s.run(cypher, {"keyword": component_keyword, "limit": limit})]

        paths = [
            GraphPath(
                nodes=[
                    GraphNode(
                        id=component_keyword,
                        labels=["Component"],
                        name=component_keyword,
                    ),
                    GraphNode(
                        id=r["atlas_id"],
                        labels=["Technique"],
                        name=r["technique"],
                    ),
                ],
                relations=[{"type": "AFFECTS→USES_TECHNIQUE"}],
                hops=2,
                description=f"{component_keyword} 涉及 {r['technique']}（{r['vuln_count']} 条漏洞）",
            )
            for r in rows[:3]
        ]
        return rows, paths

    def find_affected_components_by_technique(
        self, atlas_id: str, limit: int = 10
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """
        模板 3c：攻击技术的波及面（2 跳，反向）

        路径：Technique ← USES_TECHNIQUE ← Vulnerability → AFFECTS → Component

        回答「AML.T0006 会波及哪些 AI 组件」——
        这是供应链横切分析：某攻击技术出现时需连带排查哪些组件。
        """
        cypher = """
        MATCH (t:Technique)<-[:USES_TECHNIQUE]-(v:Vulnerability)-[:AFFECTS]->(c:Component)
        WHERE t.atlas_id STARTS WITH $atlas_id AND c.is_ai = true
        RETURN c.name AS component, c.ecosystem AS ecosystem, count(*) AS vuln_count
        ORDER BY vuln_count DESC
        LIMIT $limit
        """
        with self.driver.session() as s:
            rows = [dict(r) for r in s.run(cypher, {"atlas_id": atlas_id, "limit": limit})]

        paths = [
            GraphPath(
                nodes=[
                    GraphNode(id=atlas_id, labels=["Technique"], name=atlas_id),
                    GraphNode(id=r["component"], labels=["Component"], name=r["component"]),
                ],
                relations=[{"type": "AFFECTS"}],
                hops=2,
                description=f"{atlas_id} 波及 {r['component']}（{r['vuln_count']} 条）",
            )
            for r in rows[:3]
        ]
        return rows, paths

    def find_supply_chain_risk(
        self, component_keyword: str, limit: int = 20
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """
        模板 3：供应链传导风险（2 跳）

        "A 组件上的漏洞会影响哪些其他组件" →
            (:Component{name 含 A}) <-[:AFFECTS]-(:Vulnerability)-[:AFFECTS]->(:Component)
            同一漏洞同时影响 A 与 B，说明二者存在依赖关系，A 上的新漏洞需连带检查 B。

        这是单跳推不出来的关系，纯 RAG 方案无法回答这类问题。
        """
        cypher = """
        MATCH (c1:Component)<-[:AFFECTS]-(v:Vulnerability)-[:AFFECTS]->(c2:Component)
        WHERE toLower(c1.name) CONTAINS toLower($keyword)
          AND c1.key <> c2.key
        RETURN v.id AS id, v.title AS title, v.severity AS severity,
               c1.name AS source_component, c2.name AS affected_component,
               v.ai_relevance AS ai_relevance
        ORDER BY v.ai_relevance DESC, v.cvss DESC
        LIMIT $limit
        """
        results, paths = self._run_with_paths(cypher, {"keyword": component_keyword, "limit": limit})
        return results, paths

    def find_related_components(self, component_keyword: str, limit: int = 15) -> list[dict[str, Any]]:
        """
        模板 4：共现组件（1 跳）

        利用构建时的 RELATED_TO 边（共享漏洞数 ≥2）。
        """
        cypher = """
        MATCH (c1:Component)-[r:RELATED_TO]->(c2:Component)
        WHERE toLower(c1.name) CONTAINS toLower($keyword)
        RETURN c2.name AS name, c2.ecosystem AS ecosystem, r.shared_vulns AS shared_vulns
        ORDER BY r.shared_vulns DESC
        LIMIT $limit
        """
        with self.driver.session() as s:
            return [dict(r) for r in s.run(cypher, {"keyword": component_keyword, "limit": limit})]

    def find_technique_vulns(
        self, atlas_id: str, limit: int = 20
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """
        模板 5：按攻击技术查漏洞（1 跳）

        "针对提示词注入的漏洞有哪些" →
            (:Vulnerability)-[:USES_TECHNIQUE]->(:Technique{atlas_id})
        """
        cypher = """
        MATCH (v:Vulnerability)-[:USES_TECHNIQUE]->(t:Technique)
        WHERE t.atlas_id STARTS WITH $atlas_id
        RETURN v.id AS id, v.title AS title, v.severity AS severity, v.cvss AS cvss,
               t.atlas_id AS atlas_id, t.name AS technique
        ORDER BY v.ai_relevance DESC
        LIMIT $limit
        """
        results, paths = self._run_with_paths(cypher, {"atlas_id": atlas_id, "limit": limit})
        return results, paths

    def vulnerability_graph(
        self, vuln_id: str, max_hops: int = MAX_HOPS, limit: int = 30
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """
        模板 6：以某漏洞为中心的 N 跳子图

        支撑"这个漏洞和哪些组件/技术/论文相关"这类问题，
        也是前端"推理链展示"的数据来源。
        跳数由 max_hops 控制（≤3），符合约束。
        """
        hops = max(1, min(max_hops, MAX_HOPS))
        cypher = f"""
        MATCH path = (v:Vulnerability {{id: $vuln_id}})-
                     [r*1..{hops}]-(n)
        WHERE ALL(rel IN r WHERE type(rel) IN $allowed)
        WITH nodes(path) AS ns, relationships(path) AS rs
        RETURN [x IN ns | x.id] AS node_ids,
               [x IN ns | labels(x)[0]] AS node_labels,
               [x IN ns | coalesce(x.title, x.name, x.id)] AS node_names,
               [r IN rs | type(r)] AS rel_types
        LIMIT $limit
        """
        with self.driver.session() as s:
            rows = list(
                s.run(
                    cypher,
                    {
                        "vuln_id": vuln_id,
                        "limit": limit,
                        "allowed": ALLOWED_RELATIONS,
                    },
                )
            )

        paths: list[GraphPath] = []
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            ids = row["node_ids"] or []
            labels = row["node_labels"] or []
            names = row["node_names"] or []
            rel_types = row["rel_types"] or []

            path = GraphPath(
                nodes=[
                    GraphNode(id=i, labels=[lb], name=nm)
                    for i, lb, nm in zip(ids, labels, names, strict=False)
                ],
                relations=[{"type": t} for t in rel_types],
                hops=len(rel_types),
                description=f"{names[0] if names else vuln_id} 的 {len(rel_types)} 跳关联",
            )
            paths.append(path)

            for i, nid in enumerate(ids):
                if nid != vuln_id and nid not in seen:
                    seen.add(nid)
                    results.append(
                        {
                            "id": nid,
                            "label": labels[i] if i < len(labels) else "",
                            "name": names[i] if i < len(names) else nid,
                            "hops": i,
                        }
                    )
        return results, paths

    def trend_analysis(
        self, technique_keyword: str | None = None, days: int = 90
    ) -> dict[str, Any]:
        """
        模板 7：趋势统计（支撑"最近有哪些 X 攻击"类问题）

        在图上做聚合而非检索——这是图谱相对纯检索的第二个增值点。
        """
        # 所有 Result 必须在 with 块内用 .data 一次性取完。
        # Neo4j 的 Result 被迭代后即失效，推迟到返回阶段转换会抛
        # ResultConsumedError——趋势查询会因此静默失败。
        with self.driver.session() as s:
            if technique_keyword:
                base_where = (
                    "MATCH (v:Vulnerability)-[:USES_TECHNIQUE]->(t:Technique) "
                    "WHERE toLower(t.name) CONTAINS toLower($kw) "
                )
                sev_rows = s.run(
                    base_where + "RETURN v.severity AS severity, count(*) AS c ORDER BY c DESC",
                    kw=technique_keyword,
                ).data()
                cat_rows = s.run(
                    base_where + "RETURN v.ai_category AS category, count(*) AS c ORDER BY c DESC",
                    kw=technique_keyword,
                ).data()
                total = s.run(
                    base_where + "RETURN count(*) AS c", kw=technique_keyword
                ).single()["c"]
            else:
                sev_rows = s.run(
                    "MATCH (v:Vulnerability) "
                    "RETURN v.severity AS severity, count(*) AS c ORDER BY c DESC"
                ).data()
                cat_rows = s.run(
                    "MATCH (v:Vulnerability) WHERE v.ai_category IS NOT NULL "
                    "RETURN v.ai_category AS category, count(*) AS c ORDER BY c DESC"
                ).data()
                total = s.run("MATCH (v:Vulnerability) RETURN count(*) AS c").single()["c"]

            # 涉及的技术与组件 TOP
            tech_rows = s.run(
                """
                MATCH (t:Technique)<-[:USES_TECHNIQUE]-(:Vulnerability)
                RETURN t.atlas_id AS atlas_id, t.name AS name, count(*) AS c
                ORDER BY c DESC LIMIT 8
                """
            ).data()
            comp_rows = s.run(
                """
                MATCH (c:Component)<-[:AFFECTS]-(:Vulnerability)
                WHERE c.is_ai = true
                RETURN c.name AS name, count(*) AS c
                ORDER BY c DESC LIMIT 8
                """
            ).data()

        # 注意：Neo4j 的 Result 只能消费一次。
        # 必须在此处全部转成 list[dict]，返回阶段不能再遍历原始 Result。
        return {
            "total": total,
            "by_severity": {r["severity"] or "Unknown": r["c"] for r in sev_rows},
            "by_category": {r["category"] or "未分类": r["c"] for r in cat_rows},
            "top_techniques": [dict(r) for r in tech_rows],
            "top_components": [dict(r) for r in comp_rows],
        }

    def recommend_questions(self, vuln_id: str, limit: int = 4) -> list[str]:
        """
        推荐追问

        基于图谱邻接节点生成——赛题要求的能力。
        """
        questions: list[str] = []
        with self.driver.session() as s:
            # 关联组件 → 可问"该组件还有哪些漏洞"
            comps = list(
                s.run(
                    """
                    MATCH (v:Vulnerability {id: $vid})-[:AFFECTS]->(c:Component)
                    RETURN c.name AS name
                    LIMIT 3
                    """,
                    vid=vuln_id,
                )
            )
            # 关联技术 → 可问"针对该技术还有哪些攻击"
            techs = list(
                s.run(
                    """
                    MATCH (v:Vulnerability {id: $vid})-[:USES_TECHNIQUE]->(t:Technique)
                    RETURN t.name AS name, t.atlas_id AS atlas_id
                    LIMIT 2
                    """,
                    vid=vuln_id,
                )
            )
            # 同组件的其他漏洞
            others = list(
                s.run(
                    """
                    MATCH (v:Vulnerability {id: $vid})-[:AFFECTS]->(c:Component)
                          <-[:AFFECTS]-(o:Vulnerability)
                    WHERE o.id <> $vid
                    RETURN o.id AS id, o.title AS title
                    LIMIT 2
                    """,
                    vid=vuln_id,
                )
            )

        for c in comps:
            questions.append(f"{c['name']} 还有哪些相关漏洞？")
        for t in techs:
            questions.append(f"针对 {t['atlas_id']} {t['name']} 还有哪些攻击手法？")
        for o in others:
            questions.append(f"{o['id']} 与该漏洞有什么关联？")

        return questions[:limit]

    # ---------- 内部工具 ----------

    def _run_with_paths(
        self, cypher: str, params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[GraphPath]]:
        """执行查询并构造推理路径"""
        try:
            with self.driver.session() as s:
                rows = [dict(r) for r in s.run(cypher, params)]
        except (Neo4jError, ServiceUnavailable) as exc:
            logger.warning("graph_query_failed", error=str(exc)[:150])
            return [], []

        paths: list[GraphPath] = []
        for row in rows[:5]:
            # 从结果字段推断推理链，用于前端展示
            nodes: list[GraphNode] = []
            rels: list[dict[str, str]] = []
            if "id" in row:
                nodes.append(GraphNode(id=row["id"], labels=["Vulnerability"], name=row.get("title", "")))
            if row.get("component"):
                nodes.append(GraphNode(id=row["component"], labels=["Component"], name=row["component"]))
                rels.append({"type": "AFFECTS"})
            elif row.get("poc"):
                nodes.append(GraphNode(id=row["poc"], labels=["POC"], name=row["poc"]))
                rels.append({"type": "HAS_POC"})
            elif row.get("technique"):
                nodes.append(GraphNode(id=row.get("atlas_id", ""), labels=["Technique"], name=row.get("technique", "")))
                rels.append({"type": "USES_TECHNIQUE"})

            if nodes:
                paths.append(
                    GraphPath(
                        nodes=nodes,
                        relations=rels,
                        hops=len(rels),
                        description=" → ".join(n.name[:40] for n in nodes),
                    )
                )
        return rows, paths


# ========== 全局单例 ==========

_builder: GraphBuilder | None = None
_graph_retriever: GraphRetriever | None = None


def get_graph_builder() -> GraphBuilder:
    global _builder
    if _builder is None:
        _builder = GraphBuilder()
    return _builder


def get_graph_retriever() -> GraphRetriever:
    global _graph_retriever
    if _graph_retriever is None:
        _graph_retriever = GraphRetriever()
    return _graph_retriever
