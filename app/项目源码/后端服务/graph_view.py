"""知识图谱可视化接口（PostgreSQL 内存图版）
================================================================
数据源：PgGraph —— 直接由 PostgreSQL 关系数据构建的内存图
（见 pg_graph.py）。不再依赖外部图数据库（Neo4j）。

迁移说明：原实现读取 Neo4j（采集/富化时同步的关系副本）。
内嵌分发版不携带 Neo4j，故改为以 PostgreSQL 为唯一数据源；
本模块的接口路径、查询参数与返回结构与原实现完全一致，
前端无需任何改动。

节点规模控制：
    内存图中节点为全量（数千），但力导向布局在前端是 O(n²)，
    因此默认只取**度数最高的 N 个节点**及其之间的关系，
    并提供 min_degree 过滤。

线程与事件循环：
    图构建（首次约 1~3 秒）与查询均为同步内存操作，
    统一经 asyncio.to_thread 执行，不阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .pg_graph import get_pg_graph

log = logging.getLogger(__name__)

router = APIRouter(tags=["图谱"], prefix="/api/graph")


@router.get("/overview", summary="图谱总览（子图 + 统计）")
async def graph_overview(
    limit: int = Query(120, ge=10, le=400, description="返回节点数上限（按度数优先）"),
    min_degree: int = Query(1, ge=1, le=20, description="最小连接度过滤"),
) -> dict[str, Any]:
    """返回一片子图：节点、关系、统计与关系类型分布"""
    try:
        return await asyncio.to_thread(
            get_pg_graph().overview, limit=limit, min_degree=min_degree
        )
    except Exception as exc:  # noqa: BLE001
        # 完整堆栈只进日志；返回给浏览器的只有异常类型名
        log.exception("graph_query_failed")
        raise HTTPException(status_code=503, detail=f"图谱数据不可用：{type(exc).__name__}")


@router.get("/node", summary="节点详情与邻居")
async def graph_node(
    node_id: str = Query(..., description="节点 ID"),
    limit_each: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """选中节点后：基本信息 + 按关系类型分组的邻居"""
    try:
        result = await asyncio.to_thread(get_pg_graph().node, node_id, limit_each)
    except Exception as exc:  # noqa: BLE001
        log.exception("graph_query_failed")
        raise HTTPException(status_code=503, detail=f"图谱数据不可用：{type(exc).__name__}")
    if result is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@router.get("/path", summary="多跳链路")
async def graph_path(
    source: str = Query(..., description="起点节点 ID"),
    target: str = Query(..., description="终点节点 ID"),
    max_hops: int = Query(3, ge=1, le=3, description="跳数上限（赛题要求最多 3 跳）"),
) -> dict[str, Any]:
    """
    两点之间的最短关系链——页面右侧的「多跳链路探索」。

    逐跳 BFS 在内存邻接表上执行，天然给出「最少跳数」，
    与赛题「最多 3 跳」的语义一致。
    """
    try:
        result = await asyncio.to_thread(get_pg_graph().path, source, target, max_hops)
    except Exception as exc:  # noqa: BLE001
        log.exception("graph_query_failed")
        raise HTTPException(status_code=503, detail=f"图谱数据不可用：{type(exc).__name__}")
    if result is None:
        raise HTTPException(status_code=404, detail="起点或终点节点不存在")
    return result
