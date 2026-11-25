"""Read-only tools for stable collection scopes, real severity counts and details."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import math
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..知识库.database import get_session
from ..知识库.models import CollectionLog, Enrichment, Vulnerability

VULNERABILITY_SOURCES = ('nvd', 'ghsa', 'osv', 'cisa_kev', 'poc_repo', 'rss')


def _time(value: datetime | None) -> str | None:
    return value.astimezone(ZoneInfo('Asia/Shanghai')).isoformat() if value else None


def _record(row: Any) -> dict[str, Any]:
    score = row.cvss_score
    return {
        'cve_id': row.cve_id, 'title': row.title,
        'description': (row.description or '')[:700],
        'cvss_score': float(score) if score is not None and math.isfinite(float(score)) else None,
        'severity': row.severity, 'source': row.origin_source,
        'url': row.source_url, 'published_at': _time(row.published_at),
        'ingest_at': _time(row.ingest_at), 'fix_info': row.fix_info,
    }


def _citations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        'cve_id': r['cve_id'], 'title': r['title'], 'url': r['url'],
        'source_name': r['source'], 'origin_source': r['source'],
        'published_at': r['published_at'], 'ingest_at': r['ingest_at'],
        'severity': r['severity'], 'cvss': r['cvss_score'], 'evidence_type': 'record',
    } for r in rows]


def _distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [r['cvss_score'] for r in rows if r['cvss_score'] is not None]
    highest = max(scores) if scores else None
    return {
        'traceable_records': len(rows), 'scored_records': len(scores),
        'unscored_records': len(rows) - len(scores),
        'cvss_critical_ge_9': sum(s >= 9 for s in scores),
        'cvss_high_7_to_9': sum(7 <= s < 9 for s in scores),
        'highest_cvss': highest,
        'highest_score_records': sum(s == highest for s in scores) if scores else 0,
        'severity_labels': dict(Counter(r['severity'] or 'UNKNOWN' for r in rows)),
    }


async def get_vulnerability_details(cve_id: str):
    if not cve_id or len(cve_id) > 128:
        return '请使用查询结果中的确切情报编号。', []
    async with get_session() as session:
        row = (await session.execute(select(Vulnerability).where(Vulnerability.cve_id == cve_id))).scalars().first()
        if row is None:
            return f'未找到编号为{cve_id}的记录；这不表示知识库中没有其他相关内容。', []
        record = _record(row)
        record['description'] = (row.description or '')[:12000]
        record['affected_assets'] = row.affected_assets
        record['references'] = row.references
        record['poc_refs'] = row.poc_refs
        record['paper_refs'] = row.paper_refs
        enrichments = (await session.execute(select(Enrichment).where(Enrichment.vulnerability_id == row.id))).scalars().all()
        record['enrichments'] = [{
            'dimension': e.dimension, 'status': e.status, 'confidence': e.confidence,
            'content': e.content, 'error': e.error,
        } for e in enrichments]
        record['interpretation_note'] = '区分原始公告与自动富化结果；候选关联和置信度不等于已验证可利用或外部评测准确率。'
    return json.dumps(record, ensure_ascii=False, default=str), _citations([record])


async def get_collection_batch(context: dict[str, Any], *, batch_id=None, view='summary', limit=100, offset=0):
    if view not in ('summary', 'severity', 'list'):
        raise ValueError('view 必须是 summary、severity 或 list')
    limit, offset = min(max(int(limit), 1), 100), max(int(offset), 0)
    frozen = context.get('collection')
    chosen = batch_id if batch_id is not None else (frozen or {}).get('batch_id')
    async with get_session() as session:
        statement = select(CollectionLog).where(CollectionLog.status == 'success')
        if chosen is not None:
            statement = statement.where(CollectionLog.id == int(chosen))
        else:
            statement = statement.where(
                CollectionLog.source_code.in_(VULNERABILITY_SOURCES),
                CollectionLog.finished_at.isnot(None), CollectionLog.fetched_count > 0,
            ).order_by(CollectionLog.finished_at.desc(), CollectionLog.id.desc()).limit(1)
        log = (await session.execute(statement)).scalars().first()
        if log is None:
            return '没有找到符合条件的成功采集批次；不能据此判断整个知识库为空。', []
        metrics = log.metrics or {}
        recorded = metrics.get('record_ids')
        legacy = recorded is None
        ids = list(dict.fromkeys(recorded if recorded is not None else metrics.get('sample_ids') or []))
        ids = [s for s in ids if isinstance(s, str)][:10000]
        if frozen and chosen == frozen.get('batch_id'):
            ids = list(frozen.get('item_ids') or ids)
        query = select(Vulnerability).where(Vulnerability.cve_id.in_(ids)).order_by(
            Vulnerability.cvss_score.desc().nullslast(), Vulnerability.cve_id,
        )
        data = (await session.execute(query)).scalars().all() if ids else []
        records = [_record(row) for row in data]
        complete = not legacy and bool(metrics.get('record_ids_complete')) and len(records) == len(ids)
        scope = {
            'batch_id': log.id, 'source': log.source_code,
            'finished_at': _time(log.finished_at), 'reported_fetched_count': log.fetched_count,
            'reported_new_count': log.new_count, 'reported_updated_count': log.updated_count,
            'traceable_count': len(records), 'membership_complete': complete,
            'legacy_sample_only': legacy, 'item_ids': ids,
        }
        context['collection'] = scope
    public = {k: v for k, v in scope.items() if k != 'item_ids'}
    public['statistics'] = _distribution(records)
    public['scope_note'] = (
        '统计仅基于本批次可追溯的已保存条目ID，不使用全库或入库时间窗口冒充本批次。'
        '日志中的抓取数、新增数与唯一可追溯条目数是不同口径。'
    )
    if not complete:
        public['limitation'] = (
            f'这个历史批次只保存了{len(records)}条可追溯记录，批次明细并不完整。'
            '只能报告这些记录的分布，不能声称覆盖日志报告的全部条目，更不能编造缺失记录。'
        )
    context['_checked_batch'] = {**public, 'view': view}
    context['batch_facts'] = {**public, 'view': view}
    cites = [{
        'cve_id': None, 'title': f'采集批次 {log.id} · {log.source_code}',
        'url': None, 'source_name': '采集调度日志', 'origin_source': 'collection_logs',
        'published_at': None, 'ingest_at': scope['finished_at'], 'evidence_type': 'inference',
    }]
    if view == 'list':
        page = records[offset:offset + limit]
        public.update(records=page, offset=offset, returned=len(page),
                      has_more=offset + len(page) < len(records))
        context['_display_records'] = page
        context['_display_scope'] = {k: v for k, v in public.items() if k != 'records'}
        cites.extend(_citations(page))
    elif view == 'summary':
        public['examples'] = records[:3]
    return json.dumps(public, ensure_ascii=False, default=str), cites


def batch_facts_markdown(batch: dict[str, Any]) -> str:
    """Render exact database values without asking a model to copy numbers or dates."""
    finished = batch.get('finished_at') or '未记录'
    lines = [f'**已核验的采集批次 {batch["batch_id"]}**（{batch["source"]}）',
             f'采集完成时间：{finished}（北京时间）。',
             f'日志报告抓取 {batch["reported_fetched_count"]} 条、新增 {batch["reported_new_count"]} 条、更新 {batch["reported_updated_count"]} 条；可追溯的唯一明细为 {batch["traceable_count"]} 条。']
    if batch.get('view') == 'severity':
        stats = batch['statistics']
        lines.append(f'可追溯记录中：有评分 {stats["scored_records"]} 条，未评分 {stats["unscored_records"]} 条；CVSS≥9 有 {stats["cvss_critical_ge_9"]} 条，7≤CVSS<9 有 {stats["cvss_high_7_to_9"]} 条。')
        lines.append(f'最高 CVSS 为 {stats["highest_cvss"]}，共 {stats["highest_score_records"]} 条。' if stats['highest_cvss'] is not None
                     else '这些记录均未记录 CVSS，无法据此判断最高分或整批最严重的条数；未评分不代表没有风险。')
    if batch.get('limitation'):
        lines.append(batch['limitation'])
    return '\n\n'.join(lines)


async def get_latest_vulnerabilities(context: dict[str, Any], *, order_by='published_at', limit=10):
    if order_by not in ('published_at', 'ingest_at'):
        raise ValueError('order_by 必须是 published_at 或 ingest_at')
    limit = min(max(int(limit), 1), 50)
    column = getattr(Vulnerability, order_by)
    async with get_session() as session:
        rows = (await session.execute(select(Vulnerability).where(column.isnot(None)).order_by(
            column.desc(), Vulnerability.cve_id,
        ).limit(limit))).scalars().all()
    records = [_record(row) for row in rows]
    context.pop('collection', None)
    context['last_query'] = {
        'description': f'按{order_by}倒序的最新情报',
        'columns': list(records[0]) if records else [],
        'rows': records, 'row_count': len(records), 'record_ids': [r['cve_id'] for r in records],
    }
    context['_display_records'] = records
    context['_display_scope'] = {'order_by': order_by, 'returned': len(records)}
    return json.dumps({
        'order_by': order_by, 'records': records, 'returned': len(records),
        'note': '发布时间、首次入库时间和采集任务完成时间是不同概念。明细由系统原样展示，请分析并说明时间口径。',
    }, ensure_ascii=False, default=str), _citations(records)


def records_markdown(records: list[dict[str, Any]], scope: dict[str, Any]) -> str:
    def cell(value):
        return str(value if value is not None else '未记录').replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    if not records:
        return ''
    lines = ['\n\n### 查询明细',
             '| 编号 | 标题 | CVSS | 严重度 | 发布时间 | 来源 | 描述 |',
             '|---|---|---|---|---|---|---|']
    for row in records:
        title = cell(row['title'])
        url = row.get('url')
        if isinstance(url, str) and url.startswith(('https://', 'http://')):
            title = f'[{title}]({url.replace(" ", "%20").replace(")", "%29")})'
        lines.append('| ' + ' | '.join([
            cell(row['cve_id']), title, cell(row['cvss_score']), cell(row['severity']),
            cell(row['published_at']), cell(row['source']), cell((row['description'] or '')[:350]),
        ]) + ' |')
    if scope.get('has_more'):
        lines.append(f'\n当前列出第{scope.get("offset", 0) + 1}至{scope.get("offset", 0) + len(records)}条；可以继续追问下一页。')
    if scope.get('limitation'):
        lines.append('\n' + scope['limitation'])
    return '\n'.join(lines)
