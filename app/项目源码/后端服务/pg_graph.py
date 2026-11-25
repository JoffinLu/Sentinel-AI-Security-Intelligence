"""PostgreSQL 内存知识图谱（无外部图数据库的图谱数据源）

背景：原图谱页依赖 Neo4j（采集/富化时同步的关系副本）。
内嵌分发版不再携带 Neo4j，本模块直接从 PostgreSQL 的关系数据
构建内存图，接口返回结构与原实现完全一致（前端零改动）。

节点：
  Vulnerability  漏洞    （vulnerabilities 表）        id = "v:{db_id}"
  Component      组件    （assets 表聚合 vendor/product）id = "c:{vendor}/{product}"
  Technique      攻击技术（technique_refs 聚合 atlas_id/name）id = "t:{atlas_id}"
  Paper          论文    （paper_refs 聚合 arxiv_id/标题）id = "p:{arxiv_id}"
  POC            验证脚本（poc_refs 聚合 repo_name）      id = "x:{repo_name}"

边（沿用原图谱的关系类型命名）：
  AFFECTS         漏洞 → 组件
  USES_TECHNIQUE  漏洞 → 技术
  CITES           漏洞 → 论文
  HAS_POC         漏洞 → POC

构建一次约 1~3 秒（全库规模：数千漏洞、上万资产），带 TTL 缓存；
构建后所有查询（总览/详情/多跳链路）均在内存完成。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import text

log = logging.getLogger(__name__)

# 关系类型 → 中文名与语义（前端图例与详情面板都要用）
RELATION_LABELS: dict[str, str] = {
    "AFFECTS": "影响",
    "USES_TECHNIQUE": "利用技术",
    "HAS_POC": "存在 POC",
    "CITES": "引用",
    "RELATED_TO": "相关",
}

# 节点标签 → 中文名（未知标签原样显示）
NODE_LABELS: dict[str, str] = {
    "Vulnerability": "漏洞",
    "Component": "组件",
    "Technique": "攻击技术",
    "POC": "验证脚本",
    "Paper": "学术论文",
    "Policy": "政策法规",
    "CWE": "弱点类型",
}

_TTL_SECONDS = 300.0  # 图缓存有效期：采集写入后最多 5 分钟反映到图谱


def _short(name: str, limit: int = 42) -> str:
    """节点名过长会撑爆力导向图的标签，先截断"""
    name = (name or "").strip()
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _as_list(value: Any) -> list[Any]:
    """JSONB 列容错：None/非列表 → []"""
    if isinstance(value, list):
        return value
    return []


class PgGraph:
    """从 PostgreSQL 构建的内存图（懒构建 + TTL 缓存 + 线程安全）"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._built_at = 0.0
        self._nodes: dict[str, dict[str, Any]] = {}
        # 无向邻接表（存双向，供 BFS/邻居查询；度数与"入+出"口径一致）
        self._adj: dict[str, list[tuple[str, str]]] = {}
        # 规范方向的边（vuln → 目标），供总览的 links 使用（避免双向重复）
        self._edges: list[tuple[str, str, str]] = []

    # ---------- 构建 ----------

    def _ensure(self) -> None:
        if time.time() - self._built_at < _TTL_SECONDS and self._nodes:
            return
        with self._lock:
            if time.time() - self._built_at < _TTL_SECONDS and self._nodes:
                return
            t0 = time.time()
            nodes: dict[str, dict[str, Any]] = {}
            adj: dict[str, list[tuple[str, str]]] = {}

            def add_node(nid: str, name: str, label: str, **extra: Any) -> None:
                if nid not in nodes:
                    nodes[nid] = {"id": nid, "name": name or nid, "label": label, **extra}
                    adj[nid] = []

            edges: list[tuple[str, str, str]] = []

            def add_edge(aid: str, bid: str, rtype: str) -> None:
                """规范方向 a→b：邻接表存双向（查询友好），edges 存单向（总览用）"""
                if aid == bid or aid not in adj or bid not in adj:
                    return
                adj[aid].append((bid, rtype))
                adj[bid].append((aid, rtype))
                edges.append((aid, bid, rtype))

            from ..知识库.database import SyncSessionLocal  # noqa: PLC0415

            with SyncSessionLocal() as session:
                # ① 漏洞节点（含引用字段，供 ③ 构建关系边）
                rows = session.execute(
                    text(
                        "SELECT id, cve_id, title, cvss_score, severity, "
                        "technique_refs, paper_refs, poc_refs FROM vulnerabilities"
                    )
                ).all()
                for r in rows:
                    vid = f"v:{r[0]}"
                    add_node(
                        vid,
                        r[1] or r[2] or f"#{r[0]}",
                        "Vulnerability",
                        cvss=float(r[3]) if r[3] is not None else None,
                        severity=(r[4] or "").upper() or None,
                    )
                vuln_refs: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = [
                    (
                        f"v:{r[0]}",
                        {"techniques": _as_list(r[5])},
                        {"papers": _as_list(r[6])},
                        {"pocs": _as_list(r[7])},
                    )
                    for r in rows
                ]

                # ② 组件节点 + AFFECTS 边（assets 表，一次扫描双用途）
                arows = session.execute(
                    text(
                        "SELECT vulnerability_id, vendor, product FROM assets "
                        "WHERE vendor IS NOT NULL OR product IS NOT NULL"
                    )
                ).all()
                for vid_db, vendor, product in arows:
                    cid = f"c:{vendor or ''}/{product or ''}"
                    add_node(cid, product or vendor or "", "Component")
                    add_edge(f"v:{vid_db}", cid, "AFFECTS")

                # ③ 技术/论文/POC 节点 + 边（来自漏洞的引用字段）
                for vid, techs, papers, pocs in vuln_refs:
                    for t in techs["techniques"]:
                        if isinstance(t, dict):
                            tid = str(t.get("atlas_id") or t.get("id") or "").strip()
                            tname = str(t.get("name") or tid)
                        else:
                            tid, tname = str(t).strip(), str(t)
                        if not tid:
                            continue
                        nid = f"t:{tid}"
                        add_node(nid, tname, "Technique")
                        add_edge(vid, nid, "USES_TECHNIQUE")
                    for p in papers["papers"]:
                        if isinstance(p, dict):
                            pid = str(p.get("arxiv_id") or p.get("id") or "").strip()
                            pname = str(p.get("title") or pid)
                        else:
                            pid, pname = str(p).strip(), str(p)
                        if not pid:
                            continue
                        nid = f"p:{pid}"
                        add_node(nid, pname, "Paper")
                        add_edge(vid, nid, "CITES")
                    for x in pocs["pocs"]:
                        if isinstance(x, dict):
                            xid = str(x.get("repo_name") or x.get("source_url") or "").strip()
                            xname = str(x.get("title") or xid)
                        else:
                            xid, xname = str(x).strip(), str(x)
                        if not xid:
                            continue
                        nid = f"x:{xid}"
                        add_node(nid, xname, "POC")
                        add_edge(vid, nid, "HAS_POC")

            self._nodes = nodes
            self._adj = adj
            self._edges = edges
            self._built_at = time.time()
            log.info(
                "pg_graph_built nodes=%s edges=%s elapsed=%s",
                len(nodes),
                len(edges),
                round(time.time() - t0, 2),
            )

    # ---------- 查询 ----------

    def degree(self, nid: str) -> int:
        self._ensure()
        return len(self._adj.get(nid, []))

    def overview(self, limit: int, min_degree: int) -> dict[str, Any]:
        """总览：按度数取子图 + 全局统计 + 关系类型分布 + 度直方图"""
        self._ensure()
        nodes_all = self._nodes
        adj = self._adj

        total_nodes = len(nodes_all)
        total_rels = len(self._edges)

        ranked = sorted(
            ((nid, len(nb)) for nid, nb in adj.items() if len(nb) >= min_degree),
            key=lambda kv: (-kv[1], nodes_all[kv[0]]["name"]),
        )[:limit]
        selected = {nid for nid, _ in ranked}

        nodes: list[dict[str, Any]] = []
        for nid, deg in ranked:
            nd = nodes_all[nid]
            label = nd["label"]
            nodes.append(
                {
                    "id": nid,
                    "name": _short(nd["name"]),
                    "full_name": nd["name"],
                    "label": label,
                    "label_cn": NODE_LABELS.get(label, label),
                    "degree": deg,
                    "cvss": nd.get("cvss"),
                    "severity": nd.get("severity"),
                }
            )

        # 只保留两端都在子图内的边（用规范方向边集，天然无重复）
        rel_count: dict[str, int] = {}
        out_links: list[dict[str, Any]] = []
        for aid, bid, rtype in self._edges:
            if aid not in selected or bid not in selected:
                continue
            rel_count[rtype] = rel_count.get(rtype, 0) + 1
            out_links.append(
                {
                    "source": aid,
                    "target": bid,
                    "type": rtype,
                    "type_cn": RELATION_LABELS.get(rtype, rtype),
                }
            )

        degrees = [deg for _, deg in ranked] or [0]
        buckets = [0] * 6
        for d in degrees:
            buckets[5 if d >= 25 else d // 5] += 1

        return {
            "available": True,
            "stats": {
                "nodes": total_nodes,
                "relationships": total_rels,
                "avg_degree": round(sum(degrees) / len(degrees), 2) if degrees else 0,
                "max_degree": max(degrees),
                "shown_nodes": len(nodes),
                "shown_relationships": len(out_links),
            },
            "nodes": nodes,
            "links": out_links,
            "relation_types": [
                {"type": k, "type_cn": RELATION_LABELS.get(k, k), "count": v}
                for k, v in sorted(rel_count.items(), key=lambda kv: -kv[1])
            ],
            "degree_histogram": [
                {"range": r, "count": c}
                for r, c in zip(
                    ["0-4", "5-9", "10-14", "15-19", "20-24", "25+"], buckets
                )
                if c
            ],
        }

    def node(self, node_id: str, limit_each: int) -> dict[str, Any] | None:
        """节点详情与按关系类型分组的邻居"""
        self._ensure()
        nd = self._nodes.get(node_id)
        if nd is None:
            return None
        label = nd["label"]
        info = {
            "id": node_id,
            "name": _short(nd["name"], 60),
            "label": label,
            "label_cn": NODE_LABELS.get(label, label),
            "degree": len(self._adj.get(node_id, [])),
            "cvss": nd.get("cvss"),
            "severity": nd.get("severity"),
        }
        grouped: dict[str, dict[str, Any]] = {}
        for mid, rtype in self._adj.get(node_id, []):
            bucket = grouped.setdefault(
                rtype,
                {"type": rtype, "type_cn": RELATION_LABELS.get(rtype, rtype), "items": []},
            )
            if len(bucket["items"]) >= limit_each:
                continue
            m = self._nodes.get(mid)
            if m is None:
                continue
            mlb = m["label"]
            bucket["items"].append(
                {
                    "id": mid,
                    "name": _short(m["name"], 34),
                    "label": mlb,
                    "label_cn": NODE_LABELS.get(mlb, mlb),
                }
            )
        return {"node": info, "neighbors": list(grouped.values())}

    def path(self, source: str, target: str, max_hops: int) -> dict[str, Any] | None:
        """两点之间的最短关系链（逐跳 BFS，与赛题最多 3 跳语义一致）。

        返回 None 表示起点/终点不存在；found=False 表示存在但链路不通。
        """
        self._ensure()
        if source not in self._nodes or target not in self._nodes:
            return None

        def one(nodes_out: list[dict[str, Any]], hops: int | None, chain: list[dict[str, Any]]) -> dict[str, Any]:
            # chain[i]["via"] 记录的是「chain[i-1] 走到 chain[i]」用的关系，
            # 而 relations[i] 描述的是「chain[i] 走向 chain[i+1]」——
            # 因此取 chain[i+1] 的来路关系（原实现此处错位一格，已修正）
            return {
                "found": True,
                "hops": hops,
                "nodes": nodes_out,
                "relations": [
                    {
                        "from": nodes_out[i]["id"],
                        "to": nodes_out[i + 1]["id"],
                        "type": chain[i + 1]["via"],
                        "type_cn": RELATION_LABELS.get(
                            chain[i + 1]["via"] or "", chain[i + 1]["via"] or ""
                        ),
                    }
                    for i in range(len(chain) - 1)
                ],
            }

        if source == target:
            nd = self._nodes[source]
            return one(
                [
                    {
                        "id": source,
                        "name": _short(nd["name"], 34),
                        "label": nd["label"],
                        "label_cn": NODE_LABELS.get(nd["label"], nd["label"]),
                        "via": None,
                        "hop": 0,
                    }
                ],
                0,
                [],
            )

        frontier = {source}
        visited: dict[str, tuple[str, str] | None] = {source: None}
        found_hops = 0
        for hop in range(1, max_hops + 1):
            nxt: set[str] = set()
            for nid in frontier:
                for mid, rtype in self._adj.get(nid, []):
                    if mid not in visited:
                        visited[mid] = (nid, rtype)
                        nxt.add(mid)
            if target in nxt:
                found_hops = hop
                break
            frontier = nxt
            if not frontier:
                break

        if not found_hops:
            return {"found": False, "hops": None, "nodes": [], "relations": []}

        chain: list[dict[str, Any]] = []
        cur: str | None = target
        while cur is not None:
            prev = visited.get(cur)
            chain.append({"id": cur, "via": prev[1] if prev else None})
            cur = prev[0] if prev else None
        chain.reverse()

        nodes_out = []
        for i, c in enumerate(chain):
            nd = self._nodes.get(c["id"], {})
            # via = 「从本节点走向下一个节点」所用的关系（最后一个节点为 None）
            via = chain[i + 1]["via"] if i < len(chain) - 1 else None
            nodes_out.append(
                {
                    "id": c["id"],
                    "name": _short(nd.get("name", c["id"]), 34),
                    "label": nd.get("label", "Other"),
                    "label_cn": NODE_LABELS.get(nd.get("label", "Other"), "其他"),
                    "via": via,
                    "hop": i,
                }
            )
        return one(nodes_out, found_hops, chain)


_pg_graph: PgGraph | None = None


def get_pg_graph() -> PgGraph:
    global _pg_graph
    if _pg_graph is None:
        _pg_graph = PgGraph()
    return _pg_graph
